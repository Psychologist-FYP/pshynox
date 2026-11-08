"""Run the clinician API and the public meeting server in ONE process, on two loopback sockets.

    python -m services.meeting_public.serve [--api-port 8000] [--public-port 8443]

The two apps share the in-memory signaling hub (live rooms), so a clinician on the clinician API and a patient on the
public app meet in the same room. The clinician app is never served on the public socket. Both bind to 127.0.0.1 only;
Tailscale Funnel forwards public HTTPS to the public port. Proxy headers are not trusted by either server.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import signal
import sys
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parents[2]


class _Server(uvicorn.Server):
    @contextlib.contextmanager
    def capture_signals(self):   # one shared handler below stops both servers
        yield


def check_config(api_host: str, public_host: str) -> list[str]:
    """Problems that must stop internet mode before anything binds."""
    from services.api.meeting_core import is_loopback, meeting_mode, normalise_origin
    problems = []
    for name, host in (("clinician API", api_host), ("public meeting server", public_host)):
        if not is_loopback(host):
            problems.append(f"The {name} must bind to 127.0.0.1 (got {host}).")
    if meeting_mode() != "internet":
        problems.append("Set MEETING_MODE=internet for the public meeting server.")
    try:
        normalise_origin(os.getenv("MEETING_PUBLIC_ORIGIN", ""), require_https=True)
    except ValueError as exc:
        problems.append(str(exc))
    return problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api-host", default="127.0.0.1")
    parser.add_argument("--api-port", type=int, default=8000)
    parser.add_argument("--public-host", default="127.0.0.1")
    parser.add_argument("--public-port", type=int, default=int(os.getenv("MEETING_PUBLIC_PORT", "8443")))
    args = parser.parse_args(argv)
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env", override=False)
    except ImportError:
        pass
    problems = check_config(args.api_host, args.public_host)
    if problems:
        print("Refusing to start internet mode:\n  " + "\n  ".join(problems), file=sys.stderr)
        return 2
    os.chdir(ROOT)
    from services.api.main import app as clinician_app, session_factory
    from services.meeting_public import gateway
    gateway.configure(session_factory)
    from services.meeting_public.app import app as public_app
    common = {"proxy_headers": False, "server_header": False, "log_level": os.getenv("MEETING_LOG_LEVEL", "info")}
    servers = [
        _Server(uvicorn.Config(clinician_app, host=args.api_host, port=args.api_port, lifespan="on", **common)),
        _Server(uvicorn.Config(public_app, host=args.public_host, port=args.public_port, lifespan="off", date_header=False, **common)),
    ]

    def stop(*_):
        for s in servers:
            s.should_exit = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    async def run():
        tasks = [asyncio.create_task(s.serve()) for s in servers]
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        stop()                       # if either server stops, stop the other too
        await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
