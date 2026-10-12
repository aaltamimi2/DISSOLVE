"""Shared set-up for campaign jobs: the warm fork server behind DISSOLVE's own TEA path, and a log of every wash costed."""
import gzip, json, os, socket, subprocess, tempfile, time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def start(wash_log_path=None):
    """Start the fork server, route DISSOLVE_TEA_PYTHON to it, and (optionally) append every costed wash to wash_log_path."""
    real_python = os.environ["TEA_REAL_PYTHON"]
    os.environ.setdefault("DISSOLVE_NO_OPEN", "1")
    os.environ["DISSOLVE_TEA_PYTHON"] = str(HERE / "tea_python_forked")
    from dissolve import tea, tea_worker, waste_pathway_data as data_layer
    sock = os.path.join(tempfile.mkdtemp(prefix="teafs-"), "s")
    os.environ["TEA_FORKSERVER_SOCK"] = sock
    server = subprocess.Popen([real_python, str(HERE / "tea_forkserver.py"), sock, str(Path(tea_worker.__file__).resolve())],
                              stdout=subprocess.PIPE, text=True, env=tea._tea_worker_environment(),
                              cwd=str(Path(tea.__file__).resolve().parents[1]))
    if server.stdout.readline().strip() != "ready":
        raise SystemExit("fork server did not start")
    if wash_log_path is not None:
        inner = data_layer.run_process_config

        def logged(config, *, confirm_live_tea):
            started = time.monotonic()
            try:
                row = inner(config, confirm_live_tea=confirm_live_tea)
            except data_layer.StageUnavailable as error:
                append_gz(wash_log_path, {"config": dict(config), "ok": False, "error": [str(a) for a in error.args],
                                          "wall_s": round(time.monotonic() - started, 3)})
                raise
            if not row.get("session_reused"):
                append_gz(wash_log_path, {"config": dict(config), "ok": True, "row": row,
                                          "wall_s": round(time.monotonic() - started, 3)})
            return row

        data_layer.run_process_config = logged
    return server


def stop(server):
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.connect(os.environ["TEA_FORKSERVER_SOCK"])
    client.sendall(b'{"stop": true}')
    client.close()
    server.wait(timeout=60)


def append_gz(path, record):
    """One gzip member per record, so a killed job leaves every finished record readable."""
    with gzip.open(path, "ab") as sink:
        sink.write((json.dumps(record, default=str) + "\n").encode())


def read_gz(path):
    """Records of a file written by append_gz; a truncated last member is ignored."""
    if not Path(path).exists():
        return
    try:
        with gzip.open(path, "rt") as handle:
            for line in handle:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    return
    except (EOFError, OSError):
        return


def done_ids(path):
    return {record["id"] for record in read_gz(path) if "id" in record}
