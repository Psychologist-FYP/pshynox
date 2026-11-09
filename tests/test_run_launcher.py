"""scripts/run.py: mode selection and Funnel handling, with a fake command runner (tailscale is never called)."""
import importlib.util
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("pshynox_run", ROOT / "scripts/run.py")
run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run)


def args(**kw):
    return SimpleNamespace(**{"local": False, "internet": False, "no_funnel": False, **kw})


@pytest.mark.parametrize("flags,settings,tailscale,expected", [
    ({}, {}, True, "local"),                                                             # nothing configured
    ({}, {"MEETING_PUBLIC_ORIGIN": "https://x.ts.net"}, True, "internet"),               # detected from .env
    ({}, {"MEETING_PUBLIC_ORIGIN": "https://x.ts.net"}, False, "local"),                 # no tailscale installed
    ({"no_funnel": True}, {"MEETING_PUBLIC_ORIGIN": "https://x.ts.net"}, False, "internet"),
    ({}, {"MEETING_PUBLIC_ORIGIN": "http://192.168.1.5:5173"}, True, "local"),           # LAN origin is not internet mode
    ({}, {"MEETING_PUBLIC_ORIGIN": "https://x.ts.net", "MEETING_MODE": "lan"}, True, "local"),
    ({"local": True}, {"MEETING_PUBLIC_ORIGIN": "https://x.ts.net"}, True, "local"),
    ({"internet": True}, {}, True, "internet"),                                          # forced; the origin check then fails loudly
])
def test_mode_selection(flags, settings, tailscale, expected):
    assert run.choose_mode(args(**flags), settings, tailscale)[0] == expected


class Fake:
    def __init__(self, *results):
        self.calls, self.results = [], list(results)

    def __call__(self, command, **_):
        self.calls.append(command)
        code, out, err = self.results.pop(0) if self.results else (0, "", "")
        return subprocess.CompletedProcess(command, code, out, err)


def test_funnel_exposes_only_the_public_port_and_turns_off_just_that_handler():
    fake = Fake()
    run.funnel_on(8443, runner=fake)
    assert fake.calls == [["tailscale", "funnel", "--bg", "--yes", "--https=443", "http://127.0.0.1:8443"]]
    assert run.funnel_off(runner=fake) is True
    assert fake.calls[-1] == ["tailscale", "funnel", "--https=443", "off"]


def test_funnel_off_falls_back_to_reset_and_reports_failure():
    fake = Fake((1, "", "no such handler"), (0, "", ""))
    assert run.funnel_off(runner=fake) is True and fake.calls[-1] == ["tailscale", "funnel", "reset"]
    assert run.funnel_off(runner=Fake((1, "", "x"), (1, "", "y"))) is False


def test_funnel_errors_explain_the_operator_setting():
    with pytest.raises(RuntimeError, match="operator"):
        run.funnel_on(8443, runner=Fake((1, "", "Access denied: serve config denied")))


def test_tailscale_connection_check():
    assert run.tailscale_connected(runner=Fake((0, json.dumps({"BackendState": "Running"}), "")))[0] is True
    ok, detail = run.tailscale_connected(runner=Fake((0, json.dumps({"BackendState": "NeedsLogin"}), "")))
    assert not ok and "NeedsLogin" in detail
    assert run.tailscale_connected(runner=Fake((1, "", "failed to connect to local tailscaled")))[0] is False


def test_public_checks_retry_until_healthz_answers_and_flag_leaks():
    import sys
    sys.path.insert(0, str(ROOT))
    calls = {"n": 0}

    def slow_then_ok(url):
        if url.endswith("/healthz"):
            calls["n"] += 1
            return (200, {"status": "ok"}) if calls["n"] >= 3 else (502, None)
        return 404, None
    results = run.wait_public("https://x.ts.net", slow_then_ok, seconds=30, sleep=lambda _: None)
    assert results[0][1] and all(p for _, p, _ in results) and calls["n"] == 3

    leaky = lambda url: (200, {"status": "ok"}) if url.endswith(("/healthz", "/api/v1/sessions")) else (404, None)
    failed = [c for c, p, _ in run.wait_public("https://x.ts.net", leaky, seconds=0)[1:] if not p]
    assert failed == ["clinician route /api/v1/sessions not served publicly"]


def test_unreachable_public_address_is_a_warning_not_a_leak():
    down = lambda url: (_ for _ in ()).throw(ConnectionError("no route"))
    results = run.wait_public("https://x.ts.net", down, seconds=0)
    assert not results[0][1] and run.leaks(results) == []
    leaky = lambda url: (200, {"status": "ok"}) if url.endswith(("/healthz", "/docs")) else (404, None)
    assert run.leaks(run.wait_public("https://x.ts.net", leaky, seconds=0)) == ["clinician route /docs not served publicly"]
