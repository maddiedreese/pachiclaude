#!/usr/bin/env python3
"""Watch the reels and report when a spin starts, each reel stops, and the
whole spin settles.

Motion is measured per reel as the mean absolute difference between
consecutive frames of that reel's crop (grayscale, blurred, downscaled so
sensor noise averages out). A reel is spinning once the difference jumps above
--spin, and stopped once it stays below --still for --still-frames frames in a
row. When every reel that spun has stopped, the settled frame's reel crops are
saved to captures/<timestamp>/ for symbol matching.

Reads the Facecam by default, or a recorded clip with --video. Use --verbose
to print the per-reel differences and tune the thresholds.
"""
import argparse
import json
import os
import sys
import time

import cv2
import numpy as np

from capture_4k import REELS_CONFIG, find_camera

SAMPLE_SIZE = (32, 40)  # (w, h) each reel crop is shrunk to before diffing
CAPTURES_DIR = "captures"


def load_boxes(path=REELS_CONFIG):
    with open(path) as f:
        return [(r["id"], tuple(r["box"])) for r in json.load(f)["reels"]]


def sample(frame, box):
    x0, y0, x1, y1 = box
    gray = cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    small = cv2.resize(gray, SAMPLE_SIZE, interpolation=cv2.INTER_AREA)
    return small.astype(np.float32)


class Reel:
    def __init__(self, reel_id, box):
        self.id = reel_id
        self.box = box
        self.prev = None
        self.spinning = False
        self.still_count = 0
        self.diff = 0.0


class ReelWatcher:
    """Feed frames in order; update() returns the events each frame caused.

    Events are tuples: ("spin",), ("stop", reel_id) and ("settled",).
    """

    def __init__(self, boxes, spin=8.0, still=3.0, still_frames=6):
        self.reels = [Reel(i, b) for i, b in boxes]
        self.spin = spin
        self.still = still
        self.still_frames = still_frames
        self.in_spin = False

    def update(self, frame):
        events = []
        for reel in self.reels:
            cur = sample(frame, reel.box)
            if reel.prev is not None:
                reel.diff = float(np.mean(np.abs(cur - reel.prev)))
            reel.prev = cur

            if reel.diff > self.spin:
                reel.still_count = 0
                if not reel.spinning:
                    reel.spinning = True
                    if not self.in_spin:
                        self.in_spin = True
                        events.append(("spin",))
            elif reel.diff < self.still:
                reel.still_count += 1
                if reel.spinning and reel.still_count >= self.still_frames:
                    reel.spinning = False
                    events.append(("stop", reel.id))
            # Between the thresholds: hold the current state.

        if self.in_spin and not any(r.spinning for r in self.reels):
            self.in_spin = False
            events.append(("settled",))
        return events


def save_settled(frame, boxes):
    out = os.path.join(CAPTURES_DIR, time.strftime("%Y%m%d-%H%M%S"))
    os.makedirs(out, exist_ok=True)
    for reel_id, (x0, y0, x1, y1) in boxes:
        cv2.imwrite(os.path.join(out, f"reel_{reel_id}.png"),
                    frame[y0:y1, x0:x1])
    return out


def open_source(video):
    if video:
        cap = cv2.VideoCapture(video)
        if not cap.isOpened():
            sys.exit(f"Can't open {video}")
        return cap
    print("Probing cameras for native 4K...")
    index, cap = find_camera()
    if cap is None:
        sys.exit("No camera delivered 3840x2160. Is the Facecam plugged in "
                 "and free (Photo Booth / OBS / Camera Hub closed)?")
    print(f"Using index {index}")
    return cap


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--video", help="read a recorded clip instead of the camera")
    p.add_argument("--spin", type=float, default=8.0,
                   help="diff above which a reel counts as spinning")
    p.add_argument("--still", type=float, default=3.0,
                   help="diff below which a reel counts as still")
    p.add_argument("--still-frames", type=int, default=6,
                   help="consecutive still frames before a reel is stopped")
    p.add_argument("--no-save", action="store_true",
                   help="don't write settled reel crops to captures/")
    p.add_argument("--verbose", action="store_true",
                   help="print per-reel diffs every frame")
    args = p.parse_args()

    boxes = load_boxes()
    watcher = ReelWatcher(boxes, args.spin, args.still, args.still_frames)
    cap = open_source(args.video)
    print("Watching reels (Ctrl-C to quit)")
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            events = watcher.update(frame)
            if args.verbose:
                print("  ".join(f"r{r.id}:{r.diff:5.1f}{'*' if r.spinning else ' '}"
                                for r in watcher.reels))
            for event in events:
                if event[0] == "spin":
                    print("SPIN")
                elif event[0] == "stop":
                    print(f"  reel {event[1]} stopped")
                elif event[0] == "settled":
                    msg = "SETTLED"
                    if not args.no_save:
                        msg += f" -> {save_settled(frame, boxes)}"
                    print(msg)
    except KeyboardInterrupt:
        pass
    finally:
        cap.release()


if __name__ == "__main__":
    main()
