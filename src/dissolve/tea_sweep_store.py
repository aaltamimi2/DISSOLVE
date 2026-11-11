"""Stored TEA/LCA results from the Euler core grid, served at query time one record at a time (owner, 2026-11-11).

The grid ran every modellable polymer-solvent pair through the live engine (each plant a fresh forked live run, so a
stored number equals a cold live run of the same engine). It is kept as one file per polymer x solvent x energy case,
named by ``partition_id``, beside a ``manifest.json``, under one URL prefix (DISSOLVE_TEA_SWEEP_URL: a private
DigitalOcean Spaces folder, an https folder, or a file:// folder):

    manifest.json   {"schema", "fingerprint", "partitions": {pid: {"sha256", "bytes", "count", "target_plastic",
                     "solvent", "energy_case"}}}
    <pid>.sqlite    meta(k, v): schema, partition, fingerprint, template (the result fields every plant in the file
                    shares, zlib JSON), zdict (a preset compression dictionary); plant(key_hash, data): one row per
                    plant, data = the plant's other result fields, zlib-compressed against zdict.

A plant's key is tea._config_key(config) (all 46 public fields, the cache's serve key), so a stored plant is served
only when every field matches. Nothing is served unless the manifest's engine fingerprint (process-model and worker
hashes, pinned BioSTEAM versions, cited package hashes) equals the running engine's.

Built for the hosted site (2 GB, 20-40 people at once): a file is downloaded the first time a question needs it,
under a per-file lock and at most MAX_PARALLEL_DOWNLOADS downloads at once, checked against the manifest's sha256,
and kept in DISSOLVE_TEA_SWEEP_DIR, which never holds more than DISSOLVE_TEA_SWEEP_MAX_MB (default 300) of files:
the least recently used are deleted first. A lookup opens the file read-only, reads one row and decompresses that one
record; nothing else stays in memory but the manifest and a few files' templates. Any failure (network, checksum,
missing file, other engine) answers None, and the caller runs the plant live.
"""
from __future__ import annotations

import collections
import datetime
import hashlib
import hmac
import json
import logging
import os
import sqlite3
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import zlib
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

SCHEMA = "dissolve.tea-sweep.v1"
URL_ENV = "DISSOLVE_TEA_SWEEP_URL"
DIR_ENV = "DISSOLVE_TEA_SWEEP_DIR"
MAX_MB_ENV = "DISSOLVE_TEA_SWEEP_MAX_MB"
ACCESS_KEY_ENV = "DISSOLVE_TEA_SWEEP_ACCESS_KEY"
SECRET_KEY_ENV = "DISSOLVE_TEA_SWEEP_SECRET_KEY"
REGION_ENV = "DISSOLVE_TEA_SWEEP_REGION"
DEFAULT_MAX_MB = 300
MAX_PARALLEL_DOWNLOADS = 2
TIMEOUT_SECONDS = 30
MANIFEST_RETRY_SECONDS = 300
RECENT_USE_SECONDS = 60
PINNED_DISTRIBUTIONS = ("biosteam", "thermosteam", "biorefineries")
_TEMPLATES_KEPT = 64

log = logging.getLogger(__name__)


def partition_id(key: str) -> str:
    """The file a plant lives in: its key's target polymer, solvent and energy case (normalized by the key)."""
    fields = json.loads(key)
    ident = [str(fields["target_plastic"]), str(fields["solvent"]), str(fields["energy_case"])]
    return hashlib.sha256(json.dumps(ident, separators=(",", ":")).encode()).hexdigest()[:24]


def key_hash(key: str) -> bytes:
    return hashlib.sha256(key.encode()).digest()


def _row_id(digest: bytes) -> int:
    """A plant's row: the first 8 bytes of its key hash. Rows are an ordinary rowid table because a 1.3 KB record
    fits in a 4 KB page there; in a WITHOUT ROWID table every record spilled to its own overflow page (3.5x the
    size). The full hash is kept beside the record and checked on every read."""
    return int.from_bytes(digest[:8], "big", signed=True)


def engine_fingerprint(provenance: Mapping[str, Any]) -> dict[str, Any]:
    """What must match for a stored number to equal a live one, from a live result's (or the engine's) provenance."""
    pins = provenance.get("expected_runtime_versions") or {}
    cited = provenance.get("cited_package_sources") or {}
    return {
        "process_model_sha256": provenance.get("process_model_sha256") or provenance.get("expected_process_model_sha256"),
        "worker_source_sha256": provenance.get("worker_source_sha256") or provenance.get("expected_worker_source_sha256"),
        "runtime_versions": {name: pins.get(name) for name in PINNED_DISTRIBUTIONS},
        "cited_package_sha256": {name: (entry or {}).get("sha256") for name, entry in sorted(cited.items())},
    }


# ------------------------------------------------------------------------------------------------- writing (tooling)
def write_partition(path: Path, records: Iterable[tuple[str, Mapping[str, Any]]], fingerprint: Mapping[str, Any],
                    *, dictionary_sample: int = 6) -> dict[str, Any]:
    """Write one partition file from (key, live result) pairs of one polymer x solvent x energy case. The live
    result's ``config`` is dropped (a served result carries the request's own). Returns the manifest entry."""
    rows = [(key, {k: v for k, v in result.items() if k != "config"}) for key, result in records]
    if not rows:
        raise ValueError("a partition needs at least one plant")
    pids = {partition_id(key) for key, _ in rows}
    if len(pids) != 1:
        raise ValueError("one partition per polymer, solvent and energy case")
    dumped = {key: {k: json.dumps(v, sort_keys=True, separators=(",", ":")) for k, v in result.items()} for key, result in rows}
    first = next(iter(dumped.values()))
    shared = [k for k in first if all(d.get(k) == first[k] for d in dumped.values())]
    template = {k: rows[0][1][k] for k in shared}

    def varying(result: Mapping[str, Any]) -> bytes:
        return json.dumps({k: v for k, v in result.items() if k not in template}, sort_keys=True,
                          separators=(",", ":")).encode()

    step = max(1, len(rows) // dictionary_sample)
    zdict = b"".join(varying(result) for _, result in rows[::step][:dictionary_sample])[-32768:]
    tmp = path.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    try:
        con.execute("PRAGMA page_size=16384")  # ~12 records a page instead of 2: a third less disk than 4 KB pages
        con.execute("PRAGMA journal_mode=OFF")
        con.execute("CREATE TABLE meta (k TEXT PRIMARY KEY, v BLOB NOT NULL)")
        con.execute("CREATE TABLE plant (id INTEGER PRIMARY KEY, key_hash BLOB NOT NULL, data BLOB NOT NULL)")
        first_key = rows[0][0]
        meta = {"schema": SCHEMA.encode(), "fingerprint": json.dumps(fingerprint, sort_keys=True).encode(),
                "partition": json.dumps([json.loads(first_key)[k] for k in ("target_plastic", "solvent", "energy_case")]).encode(),
                "template": zlib.compress(json.dumps(template, sort_keys=True).encode(), 9), "zdict": zdict}
        con.executemany("INSERT INTO meta VALUES (?, ?)", meta.items())
        seen: set[int] = set()
        for key, result in rows:
            digest = key_hash(key)
            if _row_id(digest) in seen:  # the same plant twice (or an 8-byte collision): the first one stays
                continue
            seen.add(_row_id(digest))
            packer = zlib.compressobj(9, zlib.DEFLATED, 15, 9, zlib.Z_DEFAULT_STRATEGY, zdict)
            con.execute("INSERT INTO plant VALUES (?, ?, ?)",
                        (_row_id(digest), digest, packer.compress(varying(result)) + packer.flush()))
        con.commit()
        con.execute("VACUUM")
    finally:
        con.close()
    os.replace(tmp, path)
    data = path.read_bytes()
    fields = json.loads(rows[0][0])
    return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "count": len(seen),
            "target_plastic": fields["target_plastic"], "solvent": fields["solvent"], "energy_case": fields["energy_case"]}


# ----------------------------------------------------------------------------------------------- S3 / Spaces signing
def sigv4_headers(method: str, url: str, *, access_key: str, secret_key: str, region: str,
                  payload_sha256: str = "UNSIGNED-PAYLOAD", now: Optional[datetime.datetime] = None,
                  extra_headers: Optional[Mapping[str, str]] = None) -> dict[str, str]:
    """AWS Signature Version 4 headers for one S3-compatible request (Spaces speaks the S3 API)."""
    parts = urllib.parse.urlsplit(url)
    moment = now or datetime.datetime.now(datetime.timezone.utc)
    amz_date, day = moment.strftime("%Y%m%dT%H%M%SZ"), moment.strftime("%Y%m%d")
    headers = {"host": parts.netloc, "x-amz-content-sha256": payload_sha256, "x-amz-date": amz_date,
               **{k.lower(): str(v) for k, v in (extra_headers or {}).items()}}
    signed = ";".join(sorted(headers))
    canonical_headers = "".join(f"{name}:{headers[name].strip()}\n" for name in sorted(headers))
    query = "&".join(f"{urllib.parse.quote(k, safe='-_.~')}={urllib.parse.quote(v, safe='-_.~')}"
                     for k, v in sorted(urllib.parse.parse_qsl(parts.query, keep_blank_values=True)))
    canonical = "\n".join([method, urllib.parse.quote(parts.path or "/", safe="/-_.~%"), query, canonical_headers,
                           signed, payload_sha256])
    scope = f"{day}/{region}/s3/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()])

    def mac(key: bytes, message: str) -> bytes:
        return hmac.new(key, message.encode(), hashlib.sha256).digest()

    signing_key = mac(mac(mac(mac(("AWS4" + secret_key).encode(), day), region), "s3"), "aws4_request")
    signature = hmac.new(signing_key, to_sign.encode(), hashlib.sha256).hexdigest()
    out = {name: value for name, value in headers.items() if name != "host"}
    out["Authorization"] = (f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, SignedHeaders={signed}, "
                            f"Signature={signature}")
    return out


# --------------------------------------------------------------------------------------------------------- serving
class SweepStore:
    """One store: a URL prefix, a local directory capped at max_bytes, and the locks that keep 20-40 people safe."""

    def __init__(self, url: str, directory: Path, max_bytes: int, *, access_key: str = "", secret_key: str = "",
                 region: str = "us-east-1") -> None:
        self.url = url.rstrip("/") + "/"
        self.directory = Path(directory)
        self.max_bytes = int(max_bytes)
        self.access_key, self.secret_key, self.region = access_key, secret_key, region
        self.directory.mkdir(parents=True, exist_ok=True)
        for stale in self.directory.glob("*.part*"):
            stale.unlink(missing_ok=True)
        self._guard = threading.Lock()
        self._manifest_lock = threading.Lock()
        self._file_locks: dict[str, threading.Lock] = {}
        self._downloads = threading.BoundedSemaphore(MAX_PARALLEL_DOWNLOADS)
        self._manifest: Optional[dict[str, Any]] = None
        self._manifest_failed_at = 0.0
        self._last_use: dict[str, float] = {}
        self._pins: collections.Counter = collections.Counter()  # readers of each file right now: never evicted
        self._reserved = 0  # bytes of downloads in flight, counted against the cap
        self._templates: "collections.OrderedDict[str, tuple[float, bytes, bytes]]" = collections.OrderedDict()
        self.stats: collections.Counter = collections.Counter()

    # -- fetching
    def _fetch(self, name: str, destination: Path) -> str:
        """Copy one object to `destination` in 256 KB pieces and return its sha256 (never the whole file in memory)."""
        url = self.url + name
        digest = hashlib.sha256()
        if url.startswith("file://"):
            reader = open(Path(urllib.parse.unquote(urllib.parse.urlsplit(url).path)), "rb")
        else:
            headers = (sigv4_headers("GET", url, access_key=self.access_key, secret_key=self.secret_key,
                                     region=self.region) if self.access_key and self.secret_key else {})
            reader = urllib.request.urlopen(urllib.request.Request(url, headers=headers, method="GET"),
                                            timeout=TIMEOUT_SECONDS)
        with reader, open(destination, "wb") as writer:
            while chunk := reader.read(1 << 18):
                digest.update(chunk)
                writer.write(chunk)
        return digest.hexdigest()

    def manifest(self) -> Optional[dict[str, Any]]:
        """The manifest, fetched once however many people ask at the same moment (and not retried for
        MANIFEST_RETRY_SECONDS after a failure)."""
        with self._guard:
            if self._manifest is not None:
                return self._manifest
        with self._manifest_lock:
            with self._guard:
                if self._manifest is not None:
                    return self._manifest
                if self._manifest_failed_at and time.monotonic() - self._manifest_failed_at < MANIFEST_RETRY_SECONDS:
                    return None
            return self._fetch_manifest()

    def _fetch_manifest(self) -> Optional[dict[str, Any]]:
        handle, name = tempfile.mkstemp(dir=self.directory, prefix="manifest.part")
        os.close(handle)
        try:
            self._fetch("manifest.json", Path(name))
            manifest = json.loads(Path(name).read_text())
            if manifest.get("schema") != SCHEMA or not isinstance(manifest.get("partitions"), dict):
                raise ValueError("not a TEA sweep manifest")
        except Exception as error:
            log.warning("TEA sweep store: manifest unavailable: %s", error)
            with self._guard:
                self._manifest_failed_at = time.monotonic()
            self.stats["manifest_error"] += 1
            return None
        finally:
            Path(name).unlink(missing_ok=True)
        with self._guard:
            self._manifest = manifest
        return manifest

    # -- the local directory
    def _lock_for(self, pid: str) -> threading.Lock:
        with self._guard:
            return self._file_locks.setdefault(pid, threading.Lock())

    def _make_room(self, need: int, keep: str) -> bool:
        """Reserve `need` bytes under the cap, deleting least recently used files that nobody is reading (files used
        in the last minute go last). Downloads in flight count against the cap. Call under self._guard."""
        sizes = {p.stem: p.stat().st_size for p in self.directory.glob("*.sqlite") if p.exists()}
        total = sum(sizes.values()) + self._reserved
        now = time.monotonic()
        order = sorted((pid for pid in sizes if pid != keep and not self._pins[pid]),
                       key=lambda pid: (now - self._last_use.get(pid, 0.0) < RECENT_USE_SECONDS,
                                        self._last_use.get(pid, 0.0)))
        for pid in order:
            if total + need <= self.max_bytes:
                break
            (self.directory / f"{pid}.sqlite").unlink(missing_ok=True)
            total -= sizes[pid]
            self._last_use.pop(pid, None)
            self._templates.pop(pid, None)
            self.stats["evicted"] += 1
        if total + need > self.max_bytes:
            return False
        self._reserved += need
        return True

    def _ensure(self, pid: str, entry: Mapping[str, Any]) -> Optional[Path]:
        """The local file, pinned for the caller (who must _unpin it), downloading it first if needed; or None."""
        path = self.directory / f"{pid}.sqlite"
        size = int(entry.get("bytes") or 0)
        if not size or size > self.max_bytes:
            return None
        with self._lock_for(pid):
            with self._guard:
                if path.exists() and path.stat().st_size == size:
                    self._pins[pid] += 1
                    self._last_use[pid] = time.monotonic()
                    return path
            with self._downloads:
                with self._guard:
                    room = self._make_room(size, keep=pid)
                if not room:  # every file is being read: this question runs live rather than break the cap
                    self.stats["no_room"] += 1
                    return None
                part = path.with_name(f"{pid}.part{threading.get_ident()}")
                try:
                    digest = self._fetch(f"{pid}.sqlite", part)
                    if digest != entry["sha256"]:
                        raise ValueError(f"checksum mismatch for {pid}")
                    with self._guard:
                        os.replace(part, path)
                        self._pins[pid] += 1
                        self._last_use[pid] = time.monotonic()
                except Exception as error:
                    log.warning("TEA sweep store: could not fetch %s: %s", pid, error)
                    self.stats["download_error"] += 1
                    return None
                finally:
                    part.unlink(missing_ok=True)
                    with self._guard:
                        self._reserved -= size
            self.stats["downloaded"] += 1
            return path

    def _unpin(self, pid: str) -> None:
        with self._guard:
            self._pins[pid] -= 1
            if self._pins[pid] <= 0:
                del self._pins[pid]

    def _template(self, pid: str, path: Path) -> tuple[bytes, bytes]:
        mtime = path.stat().st_mtime
        with self._guard:
            cached = self._templates.get(pid)
            if cached and cached[0] == mtime:
                self._templates.move_to_end(pid)
                return cached[1], cached[2]
        con = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
        try:
            meta = dict(con.execute("SELECT k, v FROM meta").fetchall())
        finally:
            con.close()
        if bytes(meta["schema"]).decode() != SCHEMA:
            raise ValueError(f"{pid} is not a {SCHEMA} file")
        template, zdict = zlib.decompress(bytes(meta["template"])), bytes(meta["zdict"])
        with self._guard:
            self._templates[pid] = (mtime, template, zdict)
            while len(self._templates) > _TEMPLATES_KEPT:
                self._templates.popitem(last=False)
        return template, zdict

    # -- the one thing callers use
    def lookup(self, key: str, fingerprint: Optional[Mapping[str, Any]], *, decode: bool = True) -> Optional[dict]:
        """The stored live result for exactly this key (without ``config``), or None. With decode=False, an empty
        dict when the plant is stored (a cheap existence check)."""
        try:
            manifest = self.manifest()
            if manifest is None:
                return None
            if fingerprint is None or manifest.get("fingerprint") != dict(fingerprint):
                self.stats["engine_mismatch"] += 1
                return None
            pid = partition_id(key)
            entry = manifest["partitions"].get(pid)
            if entry is None:
                self.stats["no_file"] += 1
                return None
            path = self._ensure(pid, entry)
            if path is None:
                return None
            try:
                template, zdict = self._template(pid, path)
                digest = key_hash(key)
                con = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
                try:
                    row = con.execute("SELECT key_hash, data FROM plant WHERE id = ?", (_row_id(digest),)).fetchone()
                finally:
                    con.close()
            finally:
                self._unpin(pid)
            if row is None or bytes(row[0]) != digest:
                self.stats["miss"] += 1
                return None
            row = (row[1],)
            self.stats["hit"] += 1
            if not decode:
                return {}
            unpacker = zlib.decompressobj(zdict=zdict)
            fields = json.loads(unpacker.decompress(bytes(row[0])) + unpacker.flush())
            return {**json.loads(template), **fields}
        except Exception as error:  # the store never takes an answer down: the caller runs the plant live
            log.warning("TEA sweep store: lookup failed: %s", error)
            self.stats["error"] += 1
            return None


_STORE: Optional[SweepStore] = None
_STORE_LOCK = threading.Lock()


def store() -> Optional[SweepStore]:
    """The store DISSOLVE_TEA_SWEEP_URL names, or None when none is configured."""
    global _STORE
    url = str(os.getenv(URL_ENV) or "").strip()
    if not url:
        return None
    with _STORE_LOCK:
        if _STORE is None or _STORE.url != url.rstrip("/") + "/":
            directory = Path(os.getenv(DIR_ENV) or Path(tempfile.gettempdir()) / "dissolve-tea-sweep").expanduser()
            max_mb = float(os.getenv(MAX_MB_ENV) or DEFAULT_MAX_MB)
            _STORE = SweepStore(url, directory, int(max_mb * 1024 * 1024),
                                access_key=str(os.getenv(ACCESS_KEY_ENV) or ""),
                                secret_key=str(os.getenv(SECRET_KEY_ENV) or ""),
                                region=str(os.getenv(REGION_ENV) or "us-east-1"))
        return _STORE


def reset() -> None:
    """Forget the configured store (tests, or after changing the environment)."""
    global _STORE
    with _STORE_LOCK:
        _STORE = None
