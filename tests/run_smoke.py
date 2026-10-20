"""Start an isolated local app, run the HTTP smoke test, and clean up its DB."""
import os
from pathlib import Path
import runpy
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
with tempfile.NamedTemporaryFile(prefix="lingua-smoke-",suffix=".sqlite3",dir=Path(__file__).parent,delete=False) as handle:
    database_path=Path(handle.name)
with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as probe:
    probe.bind(("127.0.0.1",0))
    port=probe.getsockname()[1]
env=os.environ.copy()
env.update({"LINGUAAI_ENV":"development","LINGUAAI_SESSION_SECRET":"smoke-only-secret-not-for-deployment","LINGUAAI_DATABASE":str(database_path),"LINGUAAI_AI_BASE_URL":"","LINGUAAI_AI_API_KEY":"","LINGUAAI_AI_MODEL":"","LINGUAAI_TEST_BASE":f"http://127.0.0.1:{port}"})
process=None
try:
    process=subprocess.Popen([sys.executable,"-m","uvicorn","app.main:app","--host","127.0.0.1","--port",str(port)],cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
    ready=False
    for _ in range(60):
        if process.poll() is not None: raise RuntimeError("The local app exited before it became ready.")
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ready",timeout=1) as response:
                if response.status==200: ready=True; break
        except Exception:
            time.sleep(.25)
    if not ready: raise TimeoutError("The local app did not become ready within 15 seconds.")
    # runpy executes smoke_api in this process, so pass the ephemeral port into this
    # process too; the subprocess environment above does not propagate back to us.
    previous_test_base = os.environ.get("LINGUAAI_TEST_BASE")
    os.environ["LINGUAAI_TEST_BASE"] = env["LINGUAAI_TEST_BASE"]
    try:
        runpy.run_path(str(Path(__file__).with_name("smoke_api.py")),run_name="__main__")
    finally:
        if previous_test_base is None:
            os.environ.pop("LINGUAAI_TEST_BASE", None)
        else:
            os.environ["LINGUAAI_TEST_BASE"] = previous_test_base
finally:
    if process and process.poll() is None:
        process.terminate()
        try: process.wait(timeout=8)
        except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=4)
    for suffix in ("","-shm","-wal"):
        candidate=Path(str(database_path)+suffix)
        try: candidate.unlink()
        except FileNotFoundError: pass
