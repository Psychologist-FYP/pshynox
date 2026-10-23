"""Never run persistence tests against the developer's database or audio directory."""
import os
import tempfile

_TEST_DATA = tempfile.TemporaryDirectory(prefix="pshynox-tests-")
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DATA.name}/test.db"
os.environ["LOCAL_DATA_DIR"] = _TEST_DATA.name
os.environ["SPEECH_WORKER_ENABLED"] = "false"
os.environ["FIXTURE_MODE"] = "true"
