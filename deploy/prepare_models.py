"""Image build: the query encoder the site serves. The pinned BGE revision's tokenizer and its own ONNX export are
downloaded, the export is checked against the digest research.py pins, quantized to int8 and checked again; the
full-precision export is then removed. onnx 1.20.1, onnxruntime 1.23.2 and numpy 2.4.6 give identical bytes on every
run, so this runs in its own environment with exactly those, and reads the pins from research.py's source rather than
importing the app.

    python prepare_models.py RESEARCH_PY OUT_DIR      writes OUT_DIR/bge-base.int8.onnx; any mismatch fails the build
"""
import ast
import hashlib
import sys
from pathlib import Path

from huggingface_hub import hf_hub_download
from onnxruntime.quantization import QuantType, quantize_dynamic

PINS = ("_BGE_MODEL_ID", "_BGE_ENCODER_REVISION", "BGE_ONNX_FP32_SHA256", "BGE_ONNX_INT8_SHA256")


def pinned(source: Path) -> dict[str, str]:
    found = {}
    for node in ast.parse(source.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            if node.targets[0].id in PINS:
                found[node.targets[0].id] = ast.literal_eval(node.value)
    if missing := [name for name in PINS if not isinstance(found.get(name), str)]:
        raise SystemExit(f"{source} does not pin {', '.join(missing)}")
    return found


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


pins = pinned(Path(sys.argv[1]))
out = Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
model, revision = pins["_BGE_MODEL_ID"], pins["_BGE_ENCODER_REVISION"]
hf_hub_download(model, "tokenizer.json", revision=revision)
fp32 = Path(hf_hub_download(model, "onnx/model.onnx", revision=revision)).resolve()
if sha256(fp32) != pins["BGE_ONNX_FP32_SHA256"]:
    raise SystemExit("the BGE ONNX export is not the pinned one")
int8 = out / "bge-base.int8.onnx"
quantize_dynamic(str(fp32), str(int8), weight_type=QuantType.QInt8)
if (made := sha256(int8)) != pins["BGE_ONNX_INT8_SHA256"]:
    raise SystemExit(f"the int8 quantization ({made}) is not the pinned file ({pins['BGE_ONNX_INT8_SHA256']})")
fp32.unlink()  # 436 MB the site never reads
print(f"query encoder ready: {int8} ({int8.stat().st_size // 2**20} MB, {made[:16]})")
