"""Seed a temporary database with one fictional patient for the progress Playwright suite.

Usage (from the repository root, with DATABASE_URL/LOCAL_DATA_DIR pointing at a temp dir):
    python -m tests.progress.e2e_seed <output.json>
"""
import json
import sys

from fastapi.testclient import TestClient

from services.api.main import app
from tests.progress.helpers import add_ai_observation, add_ai_score, add_prediction, add_risk, add_segment, make_patient


def main(output: str) -> None:
    with TestClient(app) as client:
        pid, (s1, s2, s3) = make_patient(client, name="Fictional E2E Patient")
    low = add_segment(s1, "I have felt low and empty most days for the past month.", start_ms=65_000)
    sleep = add_segment(s2, "I wake at four every morning and cannot get back to sleep.", start_ms=12_000)
    better = add_segment(s3, "This week was a little lighter; I went out with a friend.", start_ms=30_000)
    add_ai_score(pid, s1, {str(i): 0 for i in range(1, 9)}, [low])
    add_ai_score(pid, s2, {str(i): 1 for i in range(1, 9)}, [sleep])
    add_ai_observation(pid, s1, "low_mood", 3, [low])
    sleep_obs = add_ai_observation(pid, s2, "sleep", 2, [sleep])
    add_ai_observation(pid, s3, "social_functioning", 1, [better])
    prediction = add_prediction(pid, s2, label="possible", segment_ids=[low])
    risk = add_risk(pid, s1, [low])
    with open(output, "w", encoding="utf-8") as handle:
        json.dump({"patient_id": pid, "sessions": [s1, s2, s3], "segments": {"low": low, "sleep": sleep, "better": better},
                   "sleep_observation": sleep_obs, "prediction": prediction, "risk": risk}, handle)


if __name__ == "__main__":
    main(sys.argv[1])
