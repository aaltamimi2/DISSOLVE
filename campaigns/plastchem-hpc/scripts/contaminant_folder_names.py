"""A-11: one human-readable folder name per contaminant for the opencosmo-outputs and orca-calculation-files archives
(owner, 2026-10-06: "rename ... the folder with all the orca/opencosmo files for contaminants with the actual human
interpretable contaminant name").

The name is the contaminant's own (PubChem) name, kept as written except where a file system needs otherwise:
characters that Windows, macOS or Linux reject (/ \\ : * ? " < > | and control characters) become "-", spaces become
"_", and the result is NFC-normalised, stripped of trailing dots, and cut at 100 characters on a word boundary. Two
contaminants whose names would collide (equal ignoring case) both get " [first InChIKey block]" appended, so every
folder is unique on case-insensitive file systems too. MANIFEST.tsv keeps the InChIKey beside each name.

    python3 scripts/contaminant_folder_names.py names.tsv   reads inchikey<TAB>name lines, writes inchikey<TAB>folder"""
import re
import sys
import unicodedata

UNSAFE = re.compile(r'[/\\:*?"<>|\x00-\x1f\x7f]')
LIMIT = 100


def folder(name: str) -> str:
    text = unicodedata.normalize("NFC", str(name)).strip()
    text = UNSAFE.sub("-", text)
    text = re.sub(r"\s+", "_", text)
    if len(text) > LIMIT:
        cut = text[:LIMIT]
        boundary = max(cut.rfind("_"), cut.rfind(","), cut.rfind("-"))
        text = cut[:boundary] if boundary > LIMIT // 2 else cut
    text = text.rstrip(". _-")
    return text or "unnamed"


def folders(rows: list[tuple[str, str]]) -> dict[str, str]:
    """inchikey -> unique folder name."""
    base = {key: folder(name) for key, name in rows}
    seen: dict[str, list[str]] = {}
    for key, value in base.items():
        seen.setdefault(value.casefold(), []).append(key)
    out = {}
    for key, value in base.items():
        if len(seen[value.casefold()]) > 1:
            value = f"{value}_[{key.split('-')[0]}]"
        out[key] = value
    taken: dict[str, str] = {}
    for key, value in out.items():  # a first block shared by stereoisomers needs the whole key
        if value.casefold() in taken:
            value = f"{base[key]}_[{key}]"
            out[key] = value
        taken[value.casefold()] = key
    assert len({v.casefold() for v in out.values()}) == len(out)
    return out


if __name__ == "__main__":
    pairs = [tuple(line.rstrip("\n").split("\t")[:2]) for line in open(sys.argv[1]) if line.strip()]
    for key, value in folders(pairs).items():
        print(f"{key}\t{value}")
