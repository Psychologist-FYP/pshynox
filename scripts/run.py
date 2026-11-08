#!/usr/bin/env python3
"""Start the whole Pshynox project in one terminal.

    ./scripts/run.py               # everything; internet meetings too when MEETING_PUBLIC_ORIGIN (https) is in .env
    ./scripts/run.py --local       # workspace only (no public meeting server, no Funnel)
    ./scripts/run.py --no-funnel   # internet servers, but you expose them yourself

Local mode starts the backend (with the speech worker) and the frontend on 127.0.0.1, as before.

Internet mode, chosen automatically when .env sets MEETING_PUBLIC_ORIGIN=https://<name>.ts.net and Tailscale is
installed (or with --internet), also:

1. builds the patient-only meeting page;
2. starts the clinician API (127.0.0.1:8000) and the public meeting-only server (127.0.0.1:8443) in one process;
3. turns on Tailscale Funnel for the public server only:
   `tailscale funnel --bg --yes --https=443 http://127.0.0.1:8443`;
4. checks through the public address that /healthz answers and that NO clinician route is served there; on any
   leak it turns Funnel off and stops;
5. turns Funnel off again when you press Ctrl+C or the terminal closes.

Ctrl+C stops everything. Same-Wi-Fi HTTPS meetings still use scripts/run_lan.py.
"""
import argparse
import json
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
VENV_PYTHON = ROOT / ".venv/bin/python"


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
                "choose another --api-port / --web-port / --public-port."
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


# ---------------------------------------------------------------- internet mode helpers

def file_env(env_file: Path) -> dict:
    try:
        from dotenv import dotenv_values
    except ImportError:
        return {}
    return {k: v for k, v in dotenv_values(env_file).items() if v is not None} if env_file.exists() else {}


def choose_mode(args, settings: dict, tailscale_found: bool) -> tuple[str, str]:
    """('local' | 'internet', reason). Exported shell variables take precedence over .env."""
    origin = settings.get("MEETING_PUBLIC_ORIGIN", "").strip()
    if args.local:
        return "local", "--local"
    if args.internet:
        return "internet", "--internet"
    if settings.get("MEETING_MODE", "").strip().lower() == "lan":
        return "local", "MEETING_MODE=lan in .env"
    if not origin.startswith("https://"):
        return "local", "no https MEETING_PUBLIC_ORIGIN in .env"
    if not tailscale_found and not args.no_funnel:
        return "local", "MEETING_PUBLIC_ORIGIN is set but the tailscale command is not installed"
    return "internet", f"MEETING_PUBLIC_ORIGIN={origin}"


def tailscale(*args, runner=subprocess.run):
    return runner(["tailscale", *args], capture_output=True, text=True, timeout=60)


def funnel_on_command(public_port: int) -> list[str]:
    return ["funnel", "--bg", "--yes", "--https=443", f"http://127.0.0.1:{public_port}"]


def funnel_off_commands() -> list[list[str]]:
    # Turn off only the HTTPS 443 handler we created; `reset` (clears all funnel/serve config) is the fallback.
    return [["funnel", "--https=443", "off"], ["funnel", "reset"]]


def tailscale_connected(runner=subprocess.run) -> tuple[bool, str]:
    result = tailscale("status", "--json", runner=runner)
    if result.returncode:
        return False, (result.stderr or result.stdout).strip()[:300]
    try:
        state = json.loads(result.stdout).get("BackendState")
    except ValueError:
        return False, "unreadable tailscale status"
    return state == "Running", f"Tailscale state: {state}"


def funnel_on(public_port: int, runner=subprocess.run) -> None:
    result = tailscale(*funnel_on_command(public_port), runner=runner)
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()[:500]
        hint = ("\nOn Linux, allow your user to change Tailscale settings once with:  sudo tailscale set --operator=$USER"
                if "access denied" in detail.lower() or "permission" in detail.lower() else "")
        raise RuntimeError(f"Could not turn on Tailscale Funnel:\n{detail}{hint}")


def funnel_off(runner=subprocess.run) -> bool:
    for command in funnel_off_commands():
        try:
            if tailscale(*command, runner=runner).returncode == 0:
                return True
        except (OSError, subprocess.TimeoutExpired):
            pass
    return False


def wait_public(origin: str, get, seconds: float = 60, sleep=time.sleep) -> list:
    """Retry until /healthz answers through Funnel (DNS and certificates can take a moment), then run every check."""
    from scripts.run_internet import public_checks
    deadline = time.monotonic() + seconds
    results = public_checks(origin, get)
    while not results[0][1] and time.monotonic() < deadline:
        sleep(3)
        results = public_checks(origin, get)
    return results


def leaks(results: list) -> list[str]:
    """Clinician routes that actually ANSWERED publicly (any HTTP status except 404). Connection errors are not leaks."""
    return [check for check, passed, detail in results[1:] if not passed and str(detail).startswith("HTTP ")]


def wait_local(urls: list[str], children, seconds: float = 90) -> None:
    from scripts.run_internet import http_get
    deadline = time.monotonic() + seconds
    pending = list(urls)
    while pending and time.monotonic() < deadline:
        for name, child in children:
            if child.poll() is not None:
                raise RuntimeError(f"{name} exited with status {child.returncode}. Check its output above.")
        try:
            if http_get(pending[0])[0] == 200:
                pending.pop(0)
                continue
        except Exception:
            pass
        time.sleep(0.5)
    if pending:
        raise RuntimeError(f"A server did not start in time ({pending[0]}). Check the output above.")


# ---------------------------------------------------------------- main

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api-port", type=port_number, default=8000)
    parser.add_argument("--web-port", type=port_number, default=5173)
    parser.add_argument("--public-port", type=port_number, default=None, help="public meeting server port (default MEETING_PUBLIC_PORT or 8443)")
    parser.add_argument("--no-reload", action="store_true", help="Disable backend automatic reload (local mode)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--local", action="store_true", help="workspace only: no public meeting server, no Funnel")
    mode.add_argument("--internet", action="store_true", help="internet meetings even if not detected from .env")
    parser.add_argument("--no-funnel", action="store_true", help="internet servers without turning Funnel on (expose them yourself)")
    parser.add_argument("--skip-build", action="store_true", help="reuse the existing patient page build (internet mode)")
    parser.add_argument("--diag-headers", action="store_true", help="log the NAMES of headers Funnel adds (internet mode)")
    args = parser.parse_args(argv)
    if args.api_port == args.web_port:
        parser.error("Backend and frontend must use different ports.")

    python = VENV_PYTHON
    web = ROOT / "apps/web"
    if not python.is_file():
        raise RuntimeError(
            "Missing .venv/bin/python. From the repository root, run:\n"
            "  ./scripts/setup.sh   (or: python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt)"
        )
    dependencies = subprocess.run([str(python), "-c", "import uvicorn, dotenv, httpx"], cwd=ROOT, capture_output=True, text=True)
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

    env_file = ROOT / ".env"
    if not env_file.exists():
        with (ROOT / ".env.example").open("rb") as source:
            try:
                with env_file.open("xb") as target:
                    shutil.copyfileobj(source, target)
                print("Created .env from .env.example.", flush=True)
            except FileExistsError:
                pass

    settings = {**file_env(env_file), **os.environ}
    chosen, reason = choose_mode(args, settings, shutil.which("tailscale") is not None)
    public_port = args.public_port or int(settings.get("MEETING_PUBLIC_PORT") or 8443)
    check_port(args.api_port)
    check_port(args.web_port)

    env = os.environ.copy()
    env["VITE_API_TARGET"] = f"http://{HOST}:{args.api_port}"
    # The clinician UI is plain http on loopback (a secure context); run_lan.py owns LAN HTTPS setup.
    env.pop("VITE_HTTPS_CERT", None)
    env.pop("VITE_HTTPS_KEY", None)
    web_args = [npm, "run", "dev", "--", "--host", HOST, "--port", str(args.web_port), "--strictPort"]
    origin = ""
    if chosen == "internet":
        from scripts.run_internet import check_origin
        try:
            origin = check_origin(settings.get("MEETING_PUBLIC_ORIGIN", ""))
        except ValueError as exc:
            raise RuntimeError(f"Internet mode needs MEETING_PUBLIC_ORIGIN=https://<name>.ts.net in .env ({exc}). Use --local to skip.") from None
        if public_port in (args.api_port, args.web_port):
            parser.error("The public meeting port must differ from the API and frontend ports.")
        check_port(public_port)
        if not args.no_funnel:
            connected, detail = tailscale_connected()
            if not connected:
                raise RuntimeError(f"Tailscale is not connected ({detail}). Run `tailscale up`, or start with --local or --no-funnel.")
        env.update(MEETING_MODE="internet", MEETING_PUBLIC_ORIGIN=origin, MEETING_PUBLIC_PORT=str(public_port))
        if args.diag_headers:
            env["MEETING_DIAG_HEADERS"] = "1"
        if not args.skip_build:
            print("Building the patient-only meeting page…", flush=True)
            if subprocess.run([npm, "run", "build:meeting", "--silent"], cwd=web, env=env).returncode:
                raise RuntimeError("The patient meeting page build failed. See the output above.")
        api_args = [str(python), "-m", "services.meeting_public.serve", "--api-host", HOST, "--api-port", str(args.api_port),
                    "--public-host", HOST, "--public-port", str(public_port)]
    else:
        env["MEETING_MODE"] = "lan"
        api_args = [str(python), "-m", "uvicorn", "services.api.main:app",
                    "--env-file", str(env_file), "--host", HOST, "--port", str(args.api_port)]
        if not args.no_reload:
            api_args += ["--reload", "--reload-dir", str(ROOT / "services")]

    print(f"Starting Pshynox ({chosen} mode: {reason})", flush=True)
    children = []
    funnel_enabled = False
    try:
        for name, command, cwd in (("Backend", api_args, ROOT), ("Frontend", web_args, web)):
            children.append((name, subprocess.Popen(command, cwd=cwd, env=env, start_new_session=True)))
        if chosen == "internet":
            wait_local([f"http://{HOST}:{args.api_port}/api/v1/health", f"http://{HOST}:{public_port}/healthz"], children)
            if not args.no_funnel:
                print(f"Turning on Tailscale Funnel for the public meeting server only (127.0.0.1:{public_port})…", flush=True)
                funnel_on(public_port)
                funnel_enabled = True
                from scripts.run_internet import http_get
                results = wait_public(origin, http_get, seconds=float(os.environ.get("PSHYNOX_FUNNEL_WAIT", "60")))
                problems = leaks(results)
                if problems:
                    for check in problems:
                        print(f"  FAIL  {check}", flush=True)
                    raise RuntimeError("A clinician route answered through the public address. Funnel has been turned off; "
                                       "check `tailscale funnel status` before trying again.")
                if results[0][1]:
                    print(f"  PASS  public address answers; {len(results) - 1} clinician routes are not served there", flush=True)
                else:
                    print(f"WARNING: {origin} did not answer yet ({results[0][2]}). Patients may not reach you until it does; "
                          "check `tailscale funnel status` (new names can take a few minutes).", flush=True)
        print(
            f"\nPshynox is running\n"
            f"  Workspace: http://{HOST}:{args.web_port}/workspace\n"
            f"  API docs:  http://{HOST}:{args.api_port}/docs (this computer only)\n"
            + (f"  Internet meetings: patient invitations use {origin}/meeting/…\n"
               + ("  Funnel: ON for the public meeting server only; it is turned off when you stop.\n" if funnel_enabled
                  else f"  Funnel: not started by this script; expose it yourself with: tailscale funnel --bg {public_port}\n")
               if chosen == "internet" else "")
            + "Keep this terminal open. Ctrl+C stops everything.\n",
            flush=True,
        )
        while True:
            for name, child in children:
                status = child.poll()
                if status is not None:
                    raise RuntimeError(f"{name} exited with status {status}. Check its output above.")
            time.sleep(0.25)
    finally:
        if funnel_enabled:
            print("\nTurning off Tailscale Funnel…", flush=True)
            if not funnel_off():
                print("Could not turn Funnel off automatically. Run now:  tailscale funnel reset", file=sys.stderr, flush=True)
        if children:
            print("Stopping Pshynox…", flush=True)
        stop_servers(children)


def handle_signal(_signum, _frame):
    raise KeyboardInterrupt


def ensure_venv() -> None:
    """Re-run under the project virtualenv so the launcher can use project helpers (httpx, dotenv)."""
    # .venv/bin/python is usually a symlink to the system interpreter, so compare prefixes, not executables.
    in_venv = Path(sys.prefix).resolve() == (ROOT / ".venv").resolve()
    if VENV_PYTHON.is_file() and not in_venv and not os.environ.get("PSHYNOX_RUN_REEXEC"):
        os.environ["PSHYNOX_RUN_REEXEC"] = "1"
        os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]])
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))


if __name__ == "__main__":
    ensure_venv()
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):     # Ctrl+C, kill, or closing the terminal: Funnel is turned off
        signal.signal(sig, handle_signal)
    try:
        main()
    except KeyboardInterrupt:
        pass
    except (OSError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
