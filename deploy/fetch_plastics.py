"""Image build: the plastics process model that live TEA runs (Yoel Cortes-Pena's plastics 0.1.4, the core modules
only). A local build uses deploy/plastics-core when it is there, which git ignores. The hosted build downloads the
owner's private repository at the commit pinned in the Dockerfile, with a read-only token for that repository alone
(PLASTICS_TOKEN, a build-time secret). Only this build stage sees the token: the final image copies the files it
wrote, not the stage.

    python fetch_plastics.py OUT_DIR          writes OUT_DIR/plastics/...; anything missing fails the build
"""
import io
import os
import shutil
import sys
import tarfile
import urllib.request
from pathlib import Path

REPOSITORY = "aaltamimi2/plastics-core"
REQUIRED = ("plastics/__init__.py", "plastics/strap/__init__.py", "plastics/strap/process_model.py")

out = Path(sys.argv[1])
local = Path(__file__).with_name("plastics-core")
if local.is_dir():
    shutil.copytree(local, out, ignore=shutil.ignore_patterns("__pycache__"))
    source = f"the local copy in {local}"
else:
    token = os.environ.get("PLASTICS_TOKEN", "").strip()
    commit = os.environ.get("PLASTICS_CORE_COMMIT", "").strip()
    if not token or not commit:
        raise SystemExit("The hosted build needs PLASTICS_TOKEN (a build-time secret) and PLASTICS_CORE_COMMIT to "
                         f"fetch the plastics model from {REPOSITORY}.")
    request = urllib.request.Request(f"https://api.github.com/repos/{REPOSITORY}/tarball/{commit}", headers={
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = response.read()
    except OSError as error:  # the message names the status, never the token
        raise SystemExit(f"Could not download {REPOSITORY} at {commit}: {error}") from None
    out.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
        for member in archive.getmembers():
            _top, _, inner = member.name.partition("/")  # GitHub puts everything under owner-repo-commit/
            if inner:
                member.name = inner
                archive.extract(member, out, filter="data")
    source = f"{REPOSITORY} at {commit}"
missing = [name for name in REQUIRED if not (out / name).is_file()]
if missing:
    raise SystemExit(f"The plastics model from {source} lacks {', '.join(missing)}.")
print(f"plastics model: {source}, {sum(1 for path in out.rglob('*') if path.is_file())} files")
