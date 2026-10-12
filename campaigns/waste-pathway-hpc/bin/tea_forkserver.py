"""Fork server for DISSOLVE's live-TEA worker: BioSTEAM is imported once, every run happens in a fresh forked child.

The parent loads the admitted tea_worker.py exactly as the product's bootstrap does (runpy, so __file__ is the admitted path)
and imports the scientific stack. It never simulates, so a child starts from the state a fresh interpreter would have after
its imports. One request per connection: {"worker_path", "config_json"} -> {"stdout", "stderr", "code"}; the stdout is what
`python -c BOOTSTRAP tea_worker.py JSON` would have printed.

    tea_forkserver.py SOCKET WORKER_PATH
"""
import io, json, os, runpy, socket, sys, traceback

sock_path, worker_path = sys.argv[1], os.path.abspath(sys.argv[2])
sys.argv = [worker_path]
namespace = runpy.run_path(worker_path, run_name="tea_worker_forkserver")
import biosteam  # noqa: E402,F401  the import the worker does lazily; paid once here
try:
    namespace["_polymer_parameters"]()
except Exception:
    traceback.print_exc()

if os.path.exists(sock_path):
    os.unlink(sock_path)
server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
server.bind(sock_path)
server.listen(8)
print("ready", flush=True)


def recv_all(conn):
    chunks = []
    while True:
        chunk = conn.recv(1 << 16)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


while True:
    conn, _ = server.accept()
    try:
        request = json.loads(recv_all(conn))
    except Exception:
        conn.close()
        continue
    if request.get("stop"):
        conn.close()
        break
    out_r, out_w = os.pipe()
    err_r, err_w = os.pipe()
    pid = os.fork()
    if pid == 0:
        try:
            server.close(); conn.close(); os.close(out_r); os.close(err_r)
            os.dup2(out_w, 1); os.dup2(err_w, 2)
            sys.stdout = io.TextIOWrapper(os.fdopen(1, "wb", closefd=False), write_through=True)
            sys.stderr = io.TextIOWrapper(os.fdopen(2, "wb", closefd=False), write_through=True)
            sys.argv = [worker_path, request["config_json"]]
            code = namespace["main"]()
            sys.stdout.flush(); sys.stderr.flush()
        except BaseException:
            traceback.print_exc()
            code = 70
        os._exit(int(code or 0))
    os.close(out_w); os.close(err_w)
    # drain both pipes without deadlock: stderr is small (a traceback), stdout is one JSON line
    import selectors
    selector = selectors.DefaultSelector()
    buffers = {out_r: [], err_r: []}
    for fd in buffers:
        selector.register(fd, selectors.EVENT_READ)
    open_fds = set(buffers)
    while open_fds:
        for key, _ in selector.select():
            data = os.read(key.fd, 1 << 16)
            if data:
                buffers[key.fd].append(data)
            else:
                selector.unregister(key.fd); os.close(key.fd); open_fds.discard(key.fd)
    _, status = os.waitpid(pid, 0)
    code = os.waitstatus_to_exitcode(status)
    reply = {"stdout": b"".join(buffers[out_r]).decode("utf-8", "replace"),
             "stderr": b"".join(buffers[err_r]).decode("utf-8", "replace")[-4000:], "code": code}
    try:
        conn.sendall(json.dumps(reply).encode())
    finally:
        conn.close()
server.close()
os.unlink(sock_path)
