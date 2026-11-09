"""Secrets never enter Git: .env must stay ignored, untracked and unstaged (internet-meetings rule 5)."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


@pytest.fixture(autouse=True)
def repo():
    if not shutil.which("git") or git("rev-parse", "--is-inside-work-tree").stdout.strip() != "true":
        pytest.skip("not a git checkout")


def test_env_files_are_ignored_untracked_and_unstaged():
    assert git("check-ignore", "-q", ".env").returncode == 0, ".env must be git-ignored"
    tracked = [f for f in git("ls-files").stdout.splitlines() if Path(f).name.startswith(".env") and Path(f).name != ".env.example"]
    assert tracked == [], f"secret files tracked by Git: {tracked}"
    staged = [f for f in git("diff", "--cached", "--name-only").stdout.splitlines() if Path(f).name.startswith(".env") and Path(f).name != ".env.example"]
    assert staged == [], f"secret files staged: {staged}"


def test_env_example_holds_placeholders_only():
    text = (ROOT / ".env.example").read_text()
    for line in text.splitlines():
        if any(k in line for k in ("API_TOKEN", "API_KEY", "TURN_KEY_ID")) and "=" in line:
            value = line.split("=", 1)[1].strip()
            assert value == "" or value.startswith("<"), f"real-looking secret in .env.example: {line.split('=')[0]}"
