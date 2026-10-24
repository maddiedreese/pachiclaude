#!/usr/bin/env python3
"""Synthetic check for ReelWatcher: run with `.venv/bin/python test_reel_watch.py`.

Each reel is a tall strip of random blobs scrolled through its window, and the
reference box gets a static texture. Every frame gets sensor noise, a camera
bump is a whole-frame shift for a few frames, and a lamp flash brightens all
three reel windows for one frame. Real --verbose logs in fixtures/ are
replayed through ReelWatcher.step() as well.
"""
import re

import cv2
import numpy as np

from reel_watch import ReelWatcher, load_boxes, load_reference

STEP = 37          # px a spinning strip moves per frame
NOISE_SIGMA = 4.0  # per-pixel camera noise
FPS = 30


def make_strip(rng, w, h):
    strip = rng.integers(0, 256, (h // 8, w // 8, 3), dtype=np.uint8)
    strip = cv2.resize(strip, (w, h), interpolation=cv2.INTER_CUBIC)
    return cv2.GaussianBlur(strip, (9, 9), 0)


def run(spins, frames=120, shakes=(), flashes=(), seed=0):
    """spins maps reel id -> (start, stop) frame; shakes lists bumped frames
    and flashes the frames where the reel lamps flash."""
    rng = np.random.default_rng(seed)
    boxes = load_boxes()
    reference = load_reference()
    base = np.full((2160, 3840, 3), 40, np.uint8)
    x0, y0, x1, y1 = reference
    base[y0:y1, x0:x1] = make_strip(rng, x1 - x0, y1 - y0)
    strips = {}
    for reel_id, (x0, y0, x1, y1) in boxes:
        strips[reel_id] = make_strip(rng, x1 - x0, (y1 - y0) * 8)
    offsets = {reel_id: 0 for reel_id, _ in boxes}

    watcher = ReelWatcher(boxes, reference, fps=FPS)
    log = []
    for t in range(frames):
        frame = base.copy()
        for reel_id, (x0, y0, x1, y1) in boxes:
            start, stop = spins.get(reel_id, (0, 0))
            if start <= t < stop:
                offsets[reel_id] += STEP
            strip = strips[reel_id]
            rows = (np.arange(y1 - y0) + offsets[reel_id]) % strip.shape[0]
            window = strip[rows]
            if t in flashes:
                window = cv2.convertScaleAbs(window, alpha=1.4, beta=20)
            frame[y0:y1, x0:x1] = window
        if t in shakes:
            frame = np.roll(frame, (6, 9), axis=(0, 1))
        for box in [reference] + [b for _, b in boxes]:
            x0, y0, x1, y1 = box
            region = frame[y0:y1, x0:x1].astype(np.float32)
            region += rng.normal(0, NOISE_SIGMA, region.shape)
            frame[y0:y1, x0:x1] = np.clip(region, 0, 255).astype(np.uint8)
        for event in watcher.update(frame):
            log.append((t, event))
    return log


def events(log):
    return [e for _, e in log]


def replay(path):
    """Feed a saved --verbose log through ReelWatcher.step()."""
    line_re = re.compile(r"r1:\s*([\d.]+)\*?\s+r2:\s*([\d.]+)\*?\s+"
                         r"r3:\s*([\d.]+)\*?\s+ref:\s*([\d.]+)")
    watcher = ReelWatcher(load_boxes(), load_reference(), fps=FPS)
    log = []
    t = 0
    with open(path) as f:
        for line in f:
            m = line_re.match(line)
            if not m:
                continue
            *diffs, ref = map(float, m.groups())
            for event in watcher.step(diffs, ref):
                log.append((t, event))
            t += 1
    return log


def main():
    # A normal spin: all reels start together and stop one at a time.
    log = run({1: (20, 50), 2: (20, 70), 3: (20, 90)})
    print("spin:      ", log)
    assert events(log) == [("spin",), ("stop", 1), ("stop", 2), ("stop", 3),
                           ("settled",)], log
    times = {e: t for t, e in log}
    assert 20 <= times[("spin",)] <= 23
    assert 50 <= times[("stop", 1)] <= 60
    assert 90 <= times[("settled",)] <= 100

    # Idle footage with noise alone must never trigger a spin.
    assert run({}, frames=60) == []

    # Camera bumps (one frame, and a three-frame wobble) are not spins.
    log = run({}, frames=60, shakes={15, 30, 31, 32})
    print("shake:     ", log)
    assert log == [], log

    # A bump in the middle of a real spin doesn't stop or restart it.
    log = run({1: (20, 50), 2: (20, 70), 3: (20, 90)}, shakes={60, 61})
    print("spin+shake:", log)
    assert events(log) == [("spin",), ("stop", 1), ("stop", 2), ("stop", 3),
                           ("settled",)], log

    # Movement that settles in under min_spin (1s = 30 frames) is a false spin.
    log = run({1: (20, 26), 2: (20, 26), 3: (20, 26)}, frames=60)
    print("short:     ", log)
    assert events(log)[0] == ("spin",) and events(log)[-1] == ("false_spin",)
    assert ("settled",) not in events(log)

    # Win lamp flashes as the last reel stops and after settling: no reel
    # restarts, nothing stops twice, and no new spin starts.
    flashes = {92, 97, 98, 103, 104, 115, 116, 125}
    log = run({1: (20, 50), 2: (20, 70), 3: (20, 90)}, frames=140,
              flashes=flashes)
    print("flashes:   ", log)
    assert events(log) == [("spin",), ("stop", 1), ("stop", 2), ("stop", 3),
                           ("settled",)], log

    # The real spin from 2026-10-24: lever pull, stops 1-2-3, then the win's
    # reel-lamp flashes. Each reel must stop exactly once, in order.
    log = replay("fixtures/spin_replay_win_20261024.txt")
    print("real win:  ", log)
    assert events(log) == [("spin",), ("stop", 1), ("stop", 2), ("stop", 3),
                           ("settled",)], log
    print("ok")


if __name__ == "__main__":
    main()
