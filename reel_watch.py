#!/usr/bin/env python3
"""Watch the reels and report when a spin starts, each reel stops, and the
whole spin settles.

Motion is measured per reel as the mean absolute difference between
consecutive frames of that reel's crop (grayscale, blurred, downscaled so
sensor noise averages out). A spin starts once every reel's difference has
been above --spin for --start-frames frames in a row, and a reel is stopped
once its difference stays below --still for --still-frames frames in a row.
When every reel has stopped, the settled frame's reel crops are saved to
captures/<timestamp>/ for symbol matching.

Guards keep bumps and lamp flashes from counting as motion. A reference box
on the cabinet (reels.json "reference") never moves on its own, so while its
difference is above --shake the camera itself moved: reels can't start or
stop on those frames. The same hold applies when a reel that already stopped
appears to move mid-spin: reels only restart on a lever pull, so that's the
reel lamps flashing for a win. And a spin that settles less than --min-spin
seconds after it started is reported as a false spin instead of settling.

At startup and on every settle the reels are located with align.py (so the
boxes follow the camera if it moves) and read with read_reels.py, printing
the nine symbols and the outcome. The settled frame is saved next to the crops.

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

import align
import read_reels
from capture_4k import REELS_CONFIG, find_camera

SAMPLE_SIZE = (32, 40)  # (w, h) each reel crop is shrunk to before diffing
CAPTURES_DIR = "captures"


def load_boxes(path=REELS_CONFIG):
    with open(path) as f:
        return [(r["id"], tuple(r["box"])) for r in json.load(f)["reels"]]


def load_reference(path=REELS_CONFIG):
    with open(path) as f:
        return tuple(json.load(f)["reference"]["box"])


def sample(frame, box):
    x0, y0, x1, y1 = box
    gray = cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    small = cv2.resize(gray, SAMPLE_SIZE, interpolation=cv2.INTER_AREA)
    return small.astype(np.float32)


class Region:
    def __init__(self, reel_id, box):
        self.id = reel_id
        self.box = box
        self.prev = None
        self.spinning = False
        self.still_count = 0
        self.moving_count = 0
        self.diff = 0.0

    def move_to(self, box):
        if tuple(box) != tuple(self.box):
            self.box = tuple(box)
            self.prev = None
            self.diff = 0.0

    def measure(self, frame):
        cur = sample(frame, self.box)
        if self.prev is not None:
            self.diff = float(np.mean(np.abs(cur - self.prev)))
        self.prev = cur


class ReelWatcher:
    """Feed frames in order; update() returns the events each frame caused.

    Events are tuples: ("spin",), ("stop", reel_id), and then ("settled",)
    or, for a spin shorter than min_spin seconds, ("false_spin",).
    """

    def __init__(self, boxes, reference, spin=8.0, still=3.0, still_frames=4,
                 start_frames=3, shake=2.5, min_spin=1.0, fps=30.0):
        self.reels = [Region(i, b) for i, b in boxes]
        self.reference = Region("ref", reference)
        self.spin = spin
        self.still = still
        self.still_frames = still_frames
        self.start_frames = start_frames
        self.shake = shake
        self.min_spin_frames = round(min_spin * fps)
        self.in_spin = False
        self.spin_start = 0
        self.frame_index = -1
        self.shaking = False
        self.flashing = False

    def move_to(self, found):
        """Follow the camera: use the boxes align.locate() found."""
        for reel, (_, box) in zip(self.reels, found["reels"]):
            reel.move_to(box)
        self.reference.move_to(found["reference"])

    def boxes(self):
        return [(r.id, r.box) for r in self.reels]

    def update(self, frame):
        self.reference.measure(frame)
        for reel in self.reels:
            reel.measure(frame)
        return self.step([r.diff for r in self.reels], self.reference.diff)

    def step(self, diffs, ref_diff):
        """Advance one frame given each reel's diff and the reference diff."""
        self.frame_index += 1
        self.reference.diff = ref_diff
        for reel, diff in zip(self.reels, diffs):
            reel.diff = diff

        self.shaking = ref_diff > self.shake
        # Reels only start on a lever pull, so a reel that already stopped
        # this spin can't really move: the reel lamps flashed (win effects).
        self.flashing = self.in_spin and any(
            not r.spinning and r.diff > self.still for r in self.reels)
        if self.shaking or self.flashing:
            # Every reel looks like it moved. Hold all states and make
            # stopping reels prove stillness afresh.
            for reel in self.reels:
                reel.still_count = 0
                reel.moving_count = 0
            return []

        if not self.in_spin:
            # A lever pull starts all reels together and keeps them moving;
            # lamp flashes last a frame or two.
            for reel in self.reels:
                reel.moving_count = (reel.moving_count + 1
                                     if reel.diff > self.spin else 0)
            if all(r.moving_count >= self.start_frames for r in self.reels):
                self.in_spin = True
                self.spin_start = self.frame_index - self.start_frames + 1
                for reel in self.reels:
                    reel.spinning = True
                    reel.still_count = 0
                return [("spin",)]
            return []

        events = []
        for reel in self.reels:
            if not reel.spinning:
                continue
            if reel.diff > self.spin:
                reel.still_count = 0
            elif reel.diff < self.still:
                reel.still_count += 1
                if reel.still_count >= self.still_frames:
                    reel.spinning = False
                    events.append(("stop", reel.id))
            # Between the thresholds: hold the current state.

        if not any(r.spinning for r in self.reels):
            self.in_spin = False
            for reel in self.reels:
                reel.moving_count = 0
            if self.frame_index - self.spin_start < self.min_spin_frames:
                events.append(("false_spin",))
            else:
                events.append(("settled",))
        return events


def save_settled(frame, boxes):
    out = os.path.join(CAPTURES_DIR, time.strftime("%Y%m%d-%H%M%S"))
    os.makedirs(out, exist_ok=True)
    for reel_id, (x0, y0, x1, y1) in boxes:
        cv2.imwrite(os.path.join(out, f"reel_{reel_id}.png"),
                    frame[y0:y1, x0:x1])
    cv2.imwrite(os.path.join(out, "frame.jpg"), frame,
                [cv2.IMWRITE_JPEG_QUALITY, 90])
    return out


class Reader:
    """Finds and reads the reels; without anchors it falls back to the fixed
    boxes in reels.json."""

    def __init__(self):
        self.cfg = align.load_config()
        self.anchors = align.load_anchors()
        if not self.anchors:
            print(f"No anchors in {align.ANCHORS_DIR}/: using fixed boxes. "
                  "Run `align.py calibrate` so they follow the camera.")
        self.templates = read_reels.load_templates()
        if not self.templates:
            print(f"No templates in {read_reels.TEMPLATES_DIR}/: every spin "
                  "will be a misread.")

    def locate(self, frame, watcher):
        """Move the watcher's boxes onto the reels. False if they're lost."""
        if not self.anchors:
            return True
        found = align.locate(frame, self.cfg, self.anchors)
        if found is None:
            return False
        watcher.move_to(found)
        return True

    def read(self, frame, watcher):
        """Return (outcome, reason, grid or None)."""
        if not self.templates:
            return "misread", "no templates", None
        if not self.locate(frame, watcher):
            return "misread", "couldn't find the reels; recalibrate", None
        crops = [frame[y0:y1, x0:x1] for _, (x0, y0, x1, y1) in watcher.boxes()]
        grid = read_reels.read_grid(crops, self.templates)
        result, reason = read_reels.outcome(read_reels.symbols_only(grid))
        return result, reason, grid


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
    p.add_argument("--still-frames", type=int, default=4,
                   help="consecutive still frames before a reel is stopped")
    p.add_argument("--start-frames", type=int, default=3,
                   help="consecutive moving frames on every reel to start a spin")
    p.add_argument("--shake", type=float, default=2.5,
                   help="reference diff above which the camera itself moved")
    p.add_argument("--min-spin", type=float, default=1.0,
                   help="seconds a spin must last to count")
    p.add_argument("--no-save", action="store_true",
                   help="don't write settled reel crops to captures/")
    p.add_argument("--verbose", action="store_true",
                   help="print per-reel diffs every frame")
    args = p.parse_args()

    boxes = load_boxes()
    cap = open_source(args.video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    watcher = ReelWatcher(boxes, load_reference(), args.spin, args.still,
                          args.still_frames, args.start_frames, args.shake,
                          args.min_spin, fps)
    reader = Reader()
    print("Watching reels (Ctrl-C to quit)")
    first = True
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if first:
                first = False
                if not reader.locate(frame, watcher):
                    print("Couldn't find the reels: using the boxes in "
                          "reels.json. Recalibrate if the camera moved.")
            events = watcher.update(frame)
            if args.verbose:
                print("  ".join(f"r{r.id}:{r.diff:5.1f}{'*' if r.spinning else ' '}"
                                for r in watcher.reels)
                      + f"  ref:{watcher.reference.diff:5.1f}"
                      + ("  SHAKE" if watcher.shaking else "")
                      + ("  FLASH" if watcher.flashing else ""))
            for event in events:
                if event[0] == "spin":
                    print("SPIN")
                elif event[0] == "stop":
                    print(f"  reel {event[1]} stopped")
                elif event[0] == "settled":
                    result, reason, grid = reader.read(frame, watcher)
                    msg = "SETTLED"
                    if not args.no_save:
                        msg += f" -> {save_settled(frame, watcher.boxes())}"
                    print(msg)
                    if grid:
                        print(read_reels.format_grid(grid))
                    print(f"OUTCOME {result} ({reason})")
                elif event[0] == "false_spin":
                    print("FALSE SPIN (too short, ignored)")
    except KeyboardInterrupt:
        pass
    finally:
        cap.release()


if __name__ == "__main__":
    main()
