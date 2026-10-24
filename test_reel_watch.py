#!/usr/bin/env python3
"""Synthetic check for ReelWatcher: run with `.venv/bin/python test_reel_watch.py`.

Each reel is a tall strip of random blobs scrolled through its window. Reels
idle, all spin together, stop one at a time, and every frame gets sensor noise.
"""
import cv2
import numpy as np

from reel_watch import ReelWatcher, load_boxes

STEP = 37          # px a spinning strip moves per frame
NOISE_SIGMA = 4.0  # per-pixel camera noise


def make_strip(rng, w, h):
    strip = rng.integers(0, 256, (h // 8, w // 8, 3), dtype=np.uint8)
    strip = cv2.resize(strip, (w, h), interpolation=cv2.INTER_CUBIC)
    return cv2.GaussianBlur(strip, (9, 9), 0)


def run(stop_at, frames=120, seed=0):
    rng = np.random.default_rng(seed)
    boxes = load_boxes()
    base = np.full((2160, 3840, 3), 40, np.uint8)
    strips = {}
    for reel_id, (x0, y0, x1, y1) in boxes:
        strips[reel_id] = make_strip(rng, x1 - x0, (y1 - y0) * 8)
    offsets = {reel_id: 0 for reel_id, _ in boxes}

    watcher = ReelWatcher(boxes)
    log = []
    for t in range(frames):
        frame = base.copy()
        for reel_id, (x0, y0, x1, y1) in boxes:
            if 20 <= t < stop_at[reel_id]:
                offsets[reel_id] += STEP
            strip = strips[reel_id]
            rows = (np.arange(y1 - y0) + offsets[reel_id]) % strip.shape[0]
            window = strip[rows].astype(np.float32)
            window += rng.normal(0, NOISE_SIGMA, window.shape)
            frame[y0:y1, x0:x1] = np.clip(window, 0, 255).astype(np.uint8)
        for event in watcher.update(frame):
            log.append((t, event))
    return log


def main():
    log = run({1: 50, 2: 70, 3: 90})
    print(log)
    events = [e for _, e in log]
    assert events == [("spin",), ("stop", 1), ("stop", 2), ("stop", 3),
                      ("settled",)], events
    times = {e: t for t, e in log}
    assert times[("spin",)] == 20
    assert 50 <= times[("stop", 1)] <= 60
    assert 90 <= times[("settled",)] <= 100

    # Idle footage with noise alone must never trigger a spin.
    assert run({1: 0, 2: 0, 3: 0}, frames=60) == []
    print("ok")


if __name__ == "__main__":
    main()
