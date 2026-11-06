"""Flushed, job-scoped console tracing; content is an explicit local debug option."""
from contextlib import contextmanager
from datetime import datetime
import json
import os
import threading
import time

_LOCK = threading.Lock()


def _safe(text):
    # Source data must not execute terminal escape/control sequences.
    return ''.join(c if (ord(c) >= 32 and not 127 <= ord(c) <= 159) or c in '\n\t' else f'\\u{ord(c):04x}' for c in text)


class TerminalTrace:
    def __init__(self, job):
        self.job = str(job)
        self.stage = 'starting'
        self.level = os.getenv('NOTE_LOG_LEVEL', 'events').lower()
        if self.level not in {'off', 'events', 'full'}:
            raise ValueError('NOTE_LOG_LEVEL must be off, events or full')
        self.started = time.monotonic()

    def _write(self, message):
        prefix = f'{datetime.now().astimezone().isoformat(timespec="seconds")} [notes:{self.job}] [{self.stage}]'
        try:
            with _LOCK:
                print(_safe(f'{prefix} {message}'), flush=True)
        except (OSError, UnicodeError):
            # A closed terminal/pipe must not discard a generated note.
            pass

    def event(self, name, **fields):
        if self.level != 'off':
            self._write(name+' '+json.dumps(fields, ensure_ascii=False, default=str))

    def block(self, title, value):
        if self.level == 'full':
            body = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2, default=str)
            self._write(f'BEGIN {title}\n{body}\nEND {title}')

    @contextmanager
    def waiting(self, call, interval=10):
        stopped = threading.Event()
        started = time.monotonic()
        def heartbeat():
            while not stopped.wait(interval):
                self.event('model.waiting', call=call, seconds=round(time.monotonic()-started,1))
        thread = None
        if self.level != 'off':
            thread = threading.Thread(target=heartbeat, daemon=True)
            thread.start()
        try:
            yield
        finally:
            stopped.set()
            if thread is not None:
                thread.join(timeout=1)


def trace_for(runtime, folder):
    if not hasattr(runtime, 'trace'):
        runtime.trace = TerminalTrace(folder.name)
    return runtime.trace
