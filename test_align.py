#!/usr/bin/env python3
"""Checks for align.py: run with `.venv/bin/python test_align.py`.

Rebuilds a 4K frame from fixtures/align/ (the region around the machine),
fills the reel windows with another spin's symbols so the anchor can't just
match the symbols it was cut with, puts noise over the video screen, then
moves, tilts and relights the whole frame. The located boxes must land on the
reels and read the symbols that were put there.
"""
import json

import cv2
import numpy as np

import align
import read_reels as rr

FRAME = "fixtures/align/frame_20261024-162751"
LAMPS_ON = "fixtures/align/frame_20261024-163646_lamps_on"  # a real later spin
FILL = "fixtures/reels/spin_20261024-161451"  # symbols painted into the reels
TOLERANCE = 3  # px


def calibration_frame(path=FRAME):
    with open(path + ".json") as f:
        ox, oy = json.load(f)["origin"]
    region = cv2.imread(path + ".jpg")
    frame = np.zeros((2160, 3840, 3), np.uint8)
    frame[oy:oy + region.shape[0], ox:ox + region.shape[1]] = region
    return frame


def next_spin(calibration, cfg):
    frame = calibration.copy()
    for reel, crop in zip(cfg["reels"], rr.load_crops(FILL)):
        x0, y0, x1, y1 = reel["box"]
        frame[y0:y1, x0:x1] = cv2.resize(crop, (x1 - x0, y1 - y0))
    # Different video on the screen above the reels.
    rng = np.random.default_rng(0)
    xs = [r["box"][0] for r in cfg["reels"]] + [r["box"][2] for r in cfg["reels"]]
    top = min(r["box"][1] for r in cfg["reels"]) - align.SCREEN_GAP
    frame[top - 200:top, min(xs):max(xs)] = rng.integers(
        0, 256, (200, max(xs) - min(xs), 3), dtype=np.uint8)
    return frame


def transform(frame, dx, dy, angle, gain, cfg):
    ax0, ay0, ax1, ay1 = align.anchor_box(cfg)
    m = cv2.getRotationMatrix2D(((ax0 + ax1) / 2, (ay0 + ay1) / 2), angle, 1)
    m[:, 2] += (dx, dy)
    out = cv2.warpAffine(frame, m, (frame.shape[1], frame.shape[0]))
    out = np.clip(out.astype(np.float32) * gain
                  + np.random.default_rng(1).normal(0, 3, out.shape), 0, 255)
    return out.astype(np.uint8), m


def center(box):
    x0, y0, x1, y1 = box
    return np.array([(x0 + x1) / 2, (y0 + y1) / 2])


def main():
    with open(FRAME + ".json") as f:
        cfg = json.load(f)["reels_json_at_capture"]
    calibration = calibration_frame()
    ax0, ay0, ax1, ay1 = align.anchor_box(cfg)
    anchor = calibration[ay0:ay1, ax0:ax1]
    frame = next_spin(calibration, cfg)
    templates = rr.load_templates()
    with open("fixtures/reels/labels.json") as f:
        want = json.load(f)[FILL.split("/")[-1]]

    cases = [  # dx, dy, degrees, brightness gain
        (0, 0, 0, 1.0),
        (60, -40, 0, 1.0),
        (-120, 90, 0, 1.0),
        (-15, 32, 0.6, 0.6),  # roughly what the camera did at 16:25
        (19, 34, -1.0, 1.0),  # and at 16:27
        (80, 80, 1.5, 1.4),
    ]
    for dx, dy, angle, gain in cases:
        moved, m = transform(frame, dx, dy, angle, gain, cfg)
        found = align.locate(moved, cfg, anchor)
        assert found is not None, (dx, dy, angle, gain)
        errors = []
        for (reel_id, box), reel in zip(found["reels"], cfg["reels"]):
            truth = m[:, :2] @ center(reel["box"]) + m[:, 2]
            errors.append(float(np.abs(center(box) - truth).max()))
        crops = [moved[y0:y1, x0:x1] for _, (x0, y0, x1, y1) in found["reels"]]
        got = [c[0] for reel in rr.read_grid(crops, templates) for c in reel]
        print(f"moved {dx:+4d},{dy:+4d} {angle:+.1f}deg x{gain}: "
              f"box error {max(errors):.1f}px, read {got}")
        assert max(errors) <= TOLERANCE, errors
        wrong = [(w, g) for w, g in zip(want, got) if g is not None and g != w]
        assert not wrong, wrong

    # A real spin 9 minutes later with the reel lamps on, after the camera
    # moved about 40px: the lamps-off calibration anchor must still find it.
    with open(LAMPS_ON + ".json") as f:
        checked = json.load(f)["reels_checked_by_eye"]
    found = align.locate(calibration_frame(LAMPS_ON), cfg, anchor)
    assert found is not None
    for (reel_id, box), (_, want) in zip(found["reels"], checked):
        error = float(np.abs(np.array(box) - want).max())
        print(f"real lamps-on frame, reel {reel_id}: {box} vs {tuple(want)}")
        assert error <= TOLERANCE, (box, want)

    # No machine in view: the reels must be reported lost, not guessed.
    blank = np.random.default_rng(2).integers(0, 256, frame.shape, np.uint8)
    assert align.locate(blank, cfg, anchor) is None
    print("ok")


if __name__ == "__main__":
    main()
