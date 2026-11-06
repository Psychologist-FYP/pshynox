import fcntl
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path


def root():
    return Path(os.getenv('LOCAL_DATA_DIR','data')) / 'note-generation'


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2))
    temp.replace(path)


@contextmanager
def memory_slot(cancel=None, timeout=120):
    path = Path(os.getenv('LOCAL_DATA_DIR','data')) / 'large-model.lock'
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a') as lock:
        start=time.monotonic()
        while True:
            if cancel and cancel.is_set(): raise RuntimeError("Cancelled while waiting for ASR memory slot")
            try:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic()-start>timeout: raise RuntimeError("ASR/note memory slot busy for 120 seconds; wait for the current model job")
                time.sleep(.1)
        try: yield
        finally: fcntl.flock(lock,fcntl.LOCK_UN)
