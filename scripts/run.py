#!/usr/bin/env python3
"""Start the local backend, speech worker and frontend in one terminal."""
import argparse
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
HOST = "127.0.0.1"


def port_number(value):
    number = int(value)
    if not 1 <= number <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return number


def check_port(port):
    with socket.socket() as sock:
        # Match the servers' reuse setting so a clean restart can use ports
        # whose previous connections are still in TIME_WAIT.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((HOST, port))
        except OSError as exc:
            raise RuntimeError(
                f"Cannot use {HOST}:{port}: {exc}. Stop the existing server or "
                "choose another --api-port / --web-port."
            ) from exc


def stop_servers(children):
    # Signal entire process groups: npm/Vite and the Uvicorn reloader each
    # spawn children. The API's shutdown hook stops its speech worker.
    for _, child in children:
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 15
    for _, child in children:
        try:
            child.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            pass
    for _, child in children:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-port", type=port_number, default=8000)
    parser.add_argument("--web-port", type=port_number, default=5173)
    parser.add_argument("--no-reload", action="store_true", help="Disable backend automatic reload")
    args = parser.parse_args()
    if args.api_port == args.web_port:
        parser.error("Backend and frontend must use different ports.")

    python = ROOT / ".venv/bin/python"
    web = ROOT / "apps/web"
    if not python.is_file():
        raise RuntimeError(
            "Missing .venv/bin/python. From the repository root, run:\n"
            "  python3 -m venv .venv\n  .venv/bin/python -m pip install -r requirements.txt"
        )
    dependencies = subprocess.run(
        [str(python), "-c", "import uvicorn, dotenv"],
        cwd=ROOT, capture_output=True, text=True,
    )
    if dependencies.returncode:
        raise RuntimeError(
            "Backend launch dependencies are unavailable. Run:\n"
            "  .venv/bin/python -m pip install -r requirements.txt\n"
            + dependencies.stderr.strip()
        )
    npm = shutil.which("npm")
    if not npm or not shutil.which("node"):
        raise RuntimeError("Install Node.js and npm before starting the app. See README.md.")
    if not (web / "node_modules/.bin/vite").is_file():
        raise RuntimeError("Missing frontend dependencies. Run: npm ci --prefix apps/web")
    check_port(args.api_port)
    check_port(args.web_port)

    env_file = ROOT / ".env"
    if not env_file.exists():
        with (ROOT / ".env.example").open("rb") as source:
            try:
                with env_file.open("xb") as target:
                    shutil.copyfileobj(source, target)
                print("Created .env from .env.example.", flush=True)
            except FileExistsError:
                pass

    env = os.environ.copy()
    env["VITE_API_TARGET"] = f"http://{HOST}:{args.api_port}"
    # Local HTTP mode; the separate run_lan.py launcher owns HTTPS setup.
    env.pop("VITE_HTTPS_CERT", None)
    env.pop("VITE_HTTPS_KEY", None)
    api_args = [
        str(python), "-m", "uvicorn", "services.api.main:app",
        "--env-file", str(env_file), "--host", HOST, "--port", str(args.api_port),
    ]
    if not args.no_reload:
        api_args += ["--reload", "--reload-dir", str(ROOT / "services")]
    web_args = [npm, "run", "dev", "--", "--host", HOST,
                "--port", str(args.web_port), "--strictPort"]
    print(
        f"Starting Pshynox\n"
        f"  Workspace: http://{HOST}:{args.web_port}/workspace\n"
        f"  API docs:  http://{HOST}:{args.api_port}/docs\n"
        "Keep this terminal open. Ctrl+C stops both servers.\n",
        flush=True,
    )
    children = []
    try:
        for name, command, cwd in (("Backend", api_args, ROOT), ("Frontend", web_args, web)):
            children.append((name, subprocess.Popen(command, cwd=cwd, env=env, start_new_session=True)))
        while True:
            for name, child in children:
                status = child.poll()
                if status is not None:
                    raise RuntimeError(f"{name} exited with status {status}. Check its output above.")
            time.sleep(0.25)
    finally:
        if children:
            print("\nStopping Pshynox…", flush=True)
        stop_servers(children)


def handle_signal(_signum, _frame):
    raise KeyboardInterrupt


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, handle_signal)
    try:
        main()
    except KeyboardInterrupt:
        pass
    except (OSError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
