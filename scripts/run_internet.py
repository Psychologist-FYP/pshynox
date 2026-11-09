#!/usr/bin/env python3
"""Run internet meetings: clinician workspace on loopback, patient page on a separate public meeting-only server.

    .venv/bin/python scripts/run_internet.py

Requires MEETING_PUBLIC_ORIGIN=https://<name>.<tailnet>.ts.net (in .env or the environment). The launcher:

1. refuses to start unless every server binds to 127.0.0.1 and the origin is https://;
2. builds the patient-only page (npm run build:meeting);
3. starts the clinician API (127.0.0.1:8000) and the public meeting server (127.0.0.1:8443) in one process, and the
   clinician UI (Vite on http://127.0.0.1:5173);
4. prints the Tailscale Funnel command for YOU to run (it never runs tailscale itself), then checks through the public
   origin that /healthz answers and that no clinician route is served there;
5. reminds you to turn Funnel off on exit.

Same-Wi-Fi meetings keep using scripts/run_lan.py.
"""
from __future__ import annotations

import argparse
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Paths that must never answer through the public origin (the public app returns 404 for anything unlisted).
FORBIDDEN = ["/api/v1/health", "/api/v1/patients", "/api/v1/sessions", "/api/v1/progress/registry", "/api/v1/research/datasets",
             "/docs", "/openapi.json", "/workspace", "/"]


def check_binds(**hosts: str) -> list[str]:
    from services.api.meeting_core import is_loopback
    return [f"{name.replace('_', ' ')} must bind to 127.0.0.1 in internet mode (got {host})." for name, host in hosts.items() if not is_loopback(host)]


def check_origin(value: str) -> str:
    from services.api.meeting_core import normalise_origin
    return normalise_origin(value, require_https=True)


def public_checks(origin: str, get) -> list[tuple[str, bool, str]]:
    """(check, passed, detail) through the public origin. ``get(url) -> (status, json_or_None)``."""
    results = []
    try:
        status, body = get(f"{origin}/healthz")
        results.append(("public /healthz answers", status == 200 and body == {"status": "ok"}, f"HTTP {status}"))
    except Exception as exc:  # network errors are reported, not raised
        results.append(("public /healthz answers", False, type(exc).__name__))
    for path in FORBIDDEN:
        try:
            status, _ = get(f"{origin}{path}")
            results.append((f"clinician route {path} not served publicly", status == 404, f"HTTP {status}"))
        except Exception as exc:
            results.append((f"clinician route {path} not served publicly", False, type(exc).__name__))
    return results


def http_get(url: str):
    import httpx
    response = httpx.get(url, timeout=10, follow_redirects=False)
    try:
        return response.status_code, response.json()
    except ValueError:
        return response.status_code, None


def free_port(port: int) -> None:
    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            raise RuntimeError(f"Port {port} is already in use. Stop the old server or choose another port.") from None


def wait_for(url: str, seconds: float = 60) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if http_get(url)[0] == 200:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api-host", default="127.0.0.1")
    parser.add_argument("--api-port", type=int, default=8000)
    parser.add_argument("--web-host", default="127.0.0.1")
    parser.add_argument("--web-port", type=int, default=5173)
    parser.add_argument("--public-host", default="127.0.0.1")
    parser.add_argument("--public-port", type=int, default=None)
    parser.add_argument("--skip-build", action="store_true", help="reuse an existing apps/web/dist-meeting build")
    parser.add_argument("--diag-headers", action="store_true", help="log the NAMES of headers Funnel adds (values never logged)")
    args = parser.parse_args(argv)
    try:
        from dotenv import dotenv_values
        file_env = {k: v for k, v in dotenv_values(ROOT / ".env").items() if v is not None}
    except ImportError:
        file_env = {}
    env = {**file_env, **os.environ, "MEETING_MODE": "internet"}
    public_port = args.public_port or int(env.get("MEETING_PUBLIC_PORT", "8443"))
    problems = check_binds(clinician_API=args.api_host, clinician_UI=args.web_host, public_server=args.public_host)
    try:
        origin = check_origin(env.get("MEETING_PUBLIC_ORIGIN", ""))
    except ValueError as exc:
        problems.append(str(exc))
        origin = ""
    if problems:
        print("Refusing to start internet mode:\n  " + "\n  ".join(problems), file=sys.stderr)
        return 2
    if not shutil.which("npm") or not (ROOT / "apps/web/node_modules").is_dir():
        print("Run npm ci in apps/web first (or ./scripts/setup.sh).", file=sys.stderr)
        return 2
    for port in (args.api_port, args.web_port, public_port):
        free_port(port)
    env.update(MEETING_PUBLIC_ORIGIN=origin, MEETING_PUBLIC_PORT=str(public_port), VITE_API_TARGET=f"http://127.0.0.1:{args.api_port}")
    for key in ("VITE_HTTPS_CERT", "VITE_HTTPS_KEY"):
        env.pop(key, None)            # the clinician UI is plain http on localhost (a secure context)
    if args.diag_headers:
        env["MEETING_DIAG_HEADERS"] = "1"
    if not args.skip_build:
        print("Building the patient-only meeting page…", flush=True)
        if subprocess.run(["npm", "run", "build:meeting", "--silent"], cwd=ROOT / "apps/web", env=env).returncode:
            print("The meeting page build failed.", file=sys.stderr)
            return 1
    python = str(ROOT / ".venv/bin/python") if (ROOT / ".venv/bin/python").exists() else sys.executable
    children = []
    try:
        children.append(subprocess.Popen([python, "-m", "services.meeting_public.serve", "--api-host", args.api_host, "--api-port", str(args.api_port),
                                          "--public-host", args.public_host, "--public-port", str(public_port)], cwd=ROOT, env=env, start_new_session=True))
        children.append(subprocess.Popen(["npm", "run", "dev", "--", "--host", args.web_host, "--port", str(args.web_port), "--strictPort"],
                                         cwd=ROOT / "apps/web", env=env, start_new_session=True))
        if not (wait_for(f"http://127.0.0.1:{args.api_port}/api/v1/health") and wait_for(f"http://127.0.0.1:{public_port}/healthz")):
            raise RuntimeError("The servers did not start. Check the output above.")
        print(f"""
Clinician workspace (this laptop only): http://localhost:{args.web_port}/workspace
Patient invitations will use:           {origin}/meeting/…

Now expose ONLY the public meeting server with Tailscale Funnel, in another terminal:

    tailscale funnel --bg {public_port}

Stop it after the call with:

    tailscale funnel reset
""", flush=True)
        if sys.stdin.isatty():
            try:
                input("Press Enter once Funnel is running to check the public address (Ctrl+C to stop everything)… ")
                print("Checking the public address…", flush=True)
                results = public_checks(origin, http_get)
                for name, passed, detail in results:
                    print(f"  {'PASS' if passed else 'FAIL'}  {name} ({detail})", flush=True)
                if all(p for _, p, _ in results):
                    print("Public checks passed. Create the meeting from the clinician workspace.", flush=True)
                else:
                    print("Some public checks FAILED. Do not invite a patient; run `tailscale funnel reset` and check the port.", flush=True)
            except EOFError:
                pass
        while all(child.poll() is None for child in children):
            time.sleep(0.5)
        raise RuntimeError("A server stopped. Check the output above.")
    finally:
        for child in children:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)
        for child in children:
            try:
                child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
        print("\nServers stopped. If Funnel is still on, turn it off now:  tailscale funnel reset", flush=True)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(0)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
