#!/usr/bin/env python3
"""Checks for read_reels.py: run with `.venv/bin/python test_read_reels.py`.

Reads every labelled capture in fixtures/reels/ with the templates in
templates/, then checks the payline logic on hand-written grids.
"""
import json

import read_reels as rr

# Captures whose reel boxes were calibrated for that framing: every cell must
# read correctly. The others had boxes up to 29px off (symbols half outside
# the crop) and only need to fail safe: unreadable is fine, wrong is not.
FULLY_READABLE = {
    "calib_20261024-161301",
    "spin_20261024-161451",
    "idle_dim_20261024-162528",
    "spin_20261024-154850_oldframing",  # no templates come from this one
    "spin_20261024-163627",
    "spin_20261024-163646_holdout",  # no templates come from this one
}


def check_fixtures():
    with open("fixtures/reels/labels.json") as f:
        labels = json.load(f)
    templates = rr.load_templates()
    for name, want in labels.items():
        grid = rr.read_grid(rr.load_crops(f"fixtures/reels/{name}"),
                            templates)
        got = [cell[0] for reel in grid for cell in reel]
        wrong = [(w, g) for w, g in zip(want, got) if g is not None and g != w]
        print(f"{name:36} {got}")
        assert not wrong, f"{name}: misread as {wrong}"
        if name in FULLY_READABLE:
            assert got == want, f"{name}: {got} != {want}"


def grid(*reels):
    """grid("bell replay bell", ...) -> [[...], [...], [...]] per reel."""
    return [r.split() for r in reels]


def check_outcomes():
    cases = [
        (grid("bell red7 bell", "replay red7 bell", "cherry red7 bell"), "yolo"),
        (grid("blue7 bell bell", "replay blue7 bell", "cherry bell blue7"), "yolo"),
        (grid("bar bell bell", "bar replay bell", "bar bell cherry"), "yolo"),
        (grid("replay bell bell", "bell replay bell", "bell bell replay"), "replay"),
        (grid("bell bell replay", "bell replay bell", "replay bell bell"), "replay"),
        (grid("watermelon bell replay", "bell bell bar", "replay bell cherry"), "approve"),
        (grid("bell replay watermelon", "bell watermelon bar", "watermelon bell cherry"), "deny_tests"),
        (grid("watermelon cherry replay", "bell replay bar", "bell watermelon cherry"), "approve_roast"),
        # Mixed sevens don't make a line.
        (grid("red7 bell replay", "blue7 bell bar", "red7 replay cherry"), "deny"),
        (grid("bell watermelon red7", "cherry bell replay", "watermelon bar replay"), "deny"),
    ]
    for g, want in cases:
        got, reason = rr.outcome(g)
        assert got == want, f"{g}: {got} ({reason}) != {want}"
    # Any unreadable cell is a misread, even with a winning line elsewhere.
    g = grid("bell bell bell", "bell bell bell", "bell bell bell")
    g[2][2] = None
    assert rr.outcome(g)[0] == "misread"


def main():
    check_fixtures()
    check_outcomes()
    print("ok")


if __name__ == "__main__":
    main()
