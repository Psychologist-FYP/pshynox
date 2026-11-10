"""Cut long audio into short windows for Parakeet and map its timestamps back to recording time.

Parakeet TDT handles roughly 20–30 s per pass, so a recording is first reduced to speech regions (Silero VAD through
whisper.cpp when installed, else an energy detector), the regions are packed into windows of at most ``max_ms``, and a
region longer than that is cut at its quietest point near the limit. Every timestamp the model returns is relative to
its window; ``map_window`` adds the window's start and clips to the window, so segments are in original recording time.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np

RATE = 16000
FRAME_MS = 30
MAX_WINDOW_MS = 25_000
MIN_WINDOW_MS = 15_000          # a cut inside a long region is searched in [MIN, MAX]
MERGE_GAP_MS = 1_200            # regions closer than this may share a window
PAD_MS = 150
SENTENCE_END = re.compile(r"[.?!؟۔]$")
SEGMENT_GAP_S = 1.0             # a pause this long inside a window starts a new segment
MAX_SEGMENT_MS = 12_000


@dataclass(frozen=True)
class Window:
    start_ms: int
    end_ms: int

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


def frame_energy(samples: np.ndarray, rate: int = RATE) -> np.ndarray:
    """RMS per 30 ms frame (float32 samples in [-1, 1])."""
    n = max(1, rate * FRAME_MS // 1000)
    usable = len(samples) // n * n
    if usable == 0:
        return np.zeros(0, dtype=np.float32)
    frames = samples[:usable].reshape(-1, n).astype(np.float32)
    return np.sqrt((frames ** 2).mean(axis=1))


def energy_regions(samples: np.ndarray, rate: int = RATE) -> list[tuple[int, int]]:
    """Fallback VAD: frames well above the noise floor, gaps under 300 ms bridged, padded by 150 ms."""
    energy = frame_energy(samples, rate)
    if not len(energy) or energy.max() <= 1e-4:
        return []
    floor = float(np.percentile(energy, 20))
    threshold = max(floor * 3, float(energy.max()) * 0.05, 1e-3)
    voiced = energy > threshold
    regions, start, quiet = [], None, 0
    for i, v in enumerate(voiced):
        if v:
            start = i if start is None else start
            quiet = 0
        elif start is not None:
            quiet += 1
            if quiet * FRAME_MS >= 300:
                regions.append((start * FRAME_MS, (i - quiet + 1) * FRAME_MS))
                start, quiet = None, 0
    if start is not None:
        regions.append((start * FRAME_MS, (len(voiced) - quiet) * FRAME_MS))
    total = len(samples) * 1000 // rate
    return [(max(0, a - PAD_MS), min(total, b + PAD_MS)) for a, b in regions if b > a]


def _quietest_cut(energy: np.ndarray | None, lo_ms: int, hi_ms: int) -> int:
    if energy is None or not len(energy):
        return hi_ms
    a, b = lo_ms // FRAME_MS, min(len(energy), max(lo_ms // FRAME_MS + 1, hi_ms // FRAME_MS))
    if b <= a:
        return hi_ms
    return int((a + int(np.argmin(energy[a:b]))) * FRAME_MS)


def plan_windows(regions: list[tuple[int, int]], *, energy: np.ndarray | None = None,
                 max_ms: int = MAX_WINDOW_MS, min_ms: int = MIN_WINDOW_MS, merge_gap_ms: int = MERGE_GAP_MS) -> list[Window]:
    """Pack speech regions (ms, sorted) into windows of at most ``max_ms``; long regions are cut at a quiet frame."""
    pieces: list[tuple[int, int]] = []
    for start, end in sorted(regions):
        while end - start > max_ms:
            cut = _quietest_cut(energy, start + min_ms, start + max_ms)
            cut = cut if start < cut <= start + max_ms else start + max_ms
            pieces.append((start, cut))
            start = cut
        if end > start:
            pieces.append((start, end))
    windows: list[Window] = []
    for start, end in pieces:
        if windows and start - windows[-1].end_ms <= merge_gap_ms and end - windows[-1].start_ms <= max_ms:
            windows[-1] = Window(windows[-1].start_ms, max(end, windows[-1].end_ms))
        else:
            windows.append(Window(start, end))
    return windows


def map_window(window: Window, tokens: list[str], timestamps_s: list[float]) -> list[tuple[int, int, str]]:
    """Parakeet tokens with window-relative start times (s) → [(start_ms, end_ms, text)] in recording time.

    Segments end at sentence punctuation, at a pause of ``SEGMENT_GAP_S`` or at ``MAX_SEGMENT_MS``. A segment ends at
    the next token's start (or the window end for the last one); everything is clipped to the window."""
    if len(tokens) != len(timestamps_s):
        raise ValueError("tokens and timestamps differ in length")
    out: list[tuple[int, int, str]] = []
    current: list[int] = []

    def close(next_start_s: float | None):
        if not current:
            return
        text = " ".join("".join(tokens[i] for i in current).replace("▁", " ").split())
        start = window.start_ms + round(timestamps_s[current[0]] * 1000)
        end = window.end_ms if next_start_s is None else window.start_ms + round(next_start_s * 1000)
        start = min(max(start, window.start_ms), window.end_ms)
        end = min(max(end, start), window.end_ms)
        if text:
            out.append((start, end, text))
        current.clear()

    for i, (tok, ts) in enumerate(zip(tokens, timestamps_s)):
        if current and (ts - timestamps_s[current[-1]] >= SEGMENT_GAP_S
                        or (ts - timestamps_s[current[0]]) * 1000 >= MAX_SEGMENT_MS and tok.startswith((" ", "▁"))):
            close(ts if ts - timestamps_s[current[-1]] < SEGMENT_GAP_S else timestamps_s[current[-1]] + 0.08)
        current.append(i)
        if SENTENCE_END.search(tok.strip()):
            nxt = timestamps_s[i + 1] if i + 1 < len(timestamps_s) else None
            close(nxt if nxt is None or nxt - ts < SEGMENT_GAP_S else ts + 0.08)
    close(None)
    return out
