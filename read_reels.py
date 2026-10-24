#!/usr/bin/env python3
"""Read the nine visible symbols off the three reel crops and turn them into
an outcome.

Each reel stops with a symbol centred on each of three rows (ROW_CENTERS, as
fractions of the crop height). A cell is classified by matching labelled
example patches from templates/ against a window around that row, allowing
SEARCH px of drift. Both sides are matched as chroma (each pixel's colour
divided by its brightness, then contrast-stretched), so the reel lamps being
on, off or flashing matters much less. A cell whose best match is weak, or too close to a
different symbol, is unreadable and makes the whole spin a misread.

    read_reels.py <frame.jpg>                find the reels (align.py) and read
    read_reels.py <capture_dir>              read captures/<ts>/reel_N.png
    read_reels.py learn <capture_dir> L1 ... L9
        save that capture's cells as templates; labels go reel by reel,
        top to bottom (reel 1 top, reel 1 middle, reel 1 bottom, reel 2 top...)
"""
import glob
import os
import sys

import cv2
import numpy as np

import align

SYMBOLS = ("red7", "blue7", "bar", "replay", "bell", "watermelon", "cherry")
# Three of these on a line auto-approves the next 5 calls, so a cell only
# reads as one of them if matching on lightness agrees with chroma.
HIGH_STAKES = {"red7", "blue7", "bar"}
ROW_NAMES = ("top", "middle", "bottom")
ROW_CENTERS = (0.19, 0.52, 0.84)
PATCH_HALF = (35, 22)  # (w, h) half-size of a template patch
SEARCH = 10            # px of drift allowed each way
# Held-out reads that were right won by >= 0.24 over the next symbol; a symbol
# with no template (blue 7) was confidently read as replay but only by 0.09.
MIN_SCORE = 0.6
MIN_MARGIN = 0.15
TEMPLATES_DIR = "templates"

# Paylines as (row of reel 1, row of reel 2, row of reel 3).
LINES = {
    "top": (0, 0, 0),
    "middle": (1, 1, 1),
    "bottom": (2, 2, 2),
    "down": (0, 1, 2),
    "up": (2, 1, 0),
}

# Three of a kind on any payline, checked in this order.
LINE_OUTCOMES = (
    (("red7", "red7", "red7"), "yolo"),
    (("blue7", "blue7", "blue7"), "yolo"),
    (("bar", "bar", "bar"), "yolo"),
    (("replay", "replay", "replay"), "replay"),
    (("bell", "bell", "bell"), "approve"),
    (("watermelon", "watermelon", "watermelon"), "deny_tests"),
)


def chroma(image):
    """Brightness-free colour: each channel over the pixel's channel sum,
    stretched to the 2nd-98th percentile."""
    f = image.astype(np.float32) + 1
    c = f / f.sum(axis=2, keepdims=True)
    out = np.empty(image.shape, np.uint8)
    for i in range(3):
        lo, hi = np.percentile(c[..., i], (2, 98))
        out[..., i] = np.clip((c[..., i] - lo) * 255 / max(hi - lo, 1e-6),
                              0, 255)
    return out


def lightness(image):
    """Grayscale stretched to the 2nd-98th percentile."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).astype(np.float32)
    lo, hi = np.percentile(gray, (2, 98))
    return np.clip((gray - lo) * 255 / max(hi - lo, 1e-6), 0, 255).astype(np.uint8)


def row_center(crop, row):
    return round(crop.shape[0] * ROW_CENTERS[row])


def cut_patch(crop, row):
    hw, hh = PATCH_HALF
    cy, cx = row_center(crop, row), crop.shape[1] // 2
    return crop[max(cy - hh, 0):cy + hh, cx - hw:cx + hw]


def load_templates(directory=TEMPLATES_DIR):
    templates = []
    for path in sorted(glob.glob(os.path.join(directory, "*.png"))):
        symbol = os.path.basename(path).split("_")[0]
        if symbol in SYMBOLS:
            image = cv2.imread(path)
            templates.append((symbol, chroma(image), lightness(image)))
    return templates


def best_match(window, templates):
    """Return (symbol, score, margin over the next symbol), unthresholded."""
    best = {}
    for symbol, tpl in templates:
        th, tw = tpl.shape[:2]
        if th > window.shape[0] or tw > window.shape[1]:
            tpl = tpl[:window.shape[0], :window.shape[1]]
        score = float(cv2.matchTemplate(window, tpl,
                                        cv2.TM_CCOEFF_NORMED).max())
        best[symbol] = max(best.get(symbol, -1.0), score)
    ranked = sorted(best.items(), key=lambda kv: kv[1], reverse=True)
    symbol, score = ranked[0]
    margin = score - (ranked[1][1] if len(ranked) > 1 else -1.0)
    return symbol, score, margin


def classify(crop, row, templates):
    """Return (symbol or None, best score, margin over the next symbol) for
    one cell of a raw BGR reel crop."""
    hh = PATCH_HALF[1] + SEARCH
    cy = row_center(crop, row)
    rows = slice(max(cy - hh, 0), cy + hh)
    symbol, score, margin = best_match(
        chroma(crop)[rows], [(s, c) for s, c, _ in templates])
    if score < MIN_SCORE or margin < MIN_MARGIN:
        return None, score, margin
    if symbol in HIGH_STAKES:
        second, _, _ = best_match(
            lightness(crop)[rows], [(s, l) for s, _, l in templates])
        if second != symbol:
            return None, score, margin
    return symbol, score, margin


def read_grid(crops, templates):
    """crops: the three reel crops. Returns grid[reel][row] of (symbol,
    score, margin), symbol None where unreadable."""
    return [[classify(crop, row, templates) for row in range(3)]
            for crop in crops]


def outcome(grid):
    """Map a grid of symbols (grid[reel][row], None = unreadable) to
    (outcome, reason)."""
    if any(s is None for reel in grid for s in reel):
        return "misread", "a symbol couldn't be read"
    for want, result in LINE_OUTCOMES:
        for name, rows in LINES.items():
            if tuple(grid[r][rows[r]] for r in range(3)) == want:
                return result, f"{want[0]} on the {name} line"
    if "cherry" in grid[0]:
        return "approve_roast", "cherry on reel 1"
    return "deny", "no win"


def read_frame(frame, cfg, anchor, templates):
    """Find the reels in a full frame and read them. Returns (found, grid,
    outcome, reason); found and grid are None when the reels can't be found,
    which is a misread."""
    found = align.locate(frame, cfg, anchor)
    if found is None:
        return None, None, "misread", "couldn't find the reels; recalibrate"
    crops = [frame[y0:y1, x0:x1] for _, (x0, y0, x1, y1) in found["reels"]]
    grid = read_grid(crops, templates)
    result, reason = outcome(symbols_only(grid))
    return found, grid, result, reason


def symbols_only(grid):
    return [[cell[0] for cell in reel] for reel in grid]


def format_grid(grid):
    lines = []
    for row in range(3):
        cells = []
        for reel in grid:
            symbol, score, _ = reel[row]
            cells.append(f"{(symbol or '???'):>10} {score:.2f}")
        lines.append("  ".join(cells))
    return "\n".join(lines)


def load_crops(capture_dir):
    return [cv2.imread(os.path.join(capture_dir, f"reel_{i}.png"))
            for i in (1, 2, 3)]


def learn(capture_dir, labels):
    if len(labels) != 9 or any(l not in SYMBOLS for l in labels):
        sys.exit(f"Need 9 labels from {', '.join(SYMBOLS)}")
    os.makedirs(TEMPLATES_DIR, exist_ok=True)
    tag = os.path.basename(os.path.normpath(capture_dir)) or "frame"
    for reel, crop in enumerate(load_crops(capture_dir)):
        for row in range(3):
            symbol = labels[reel * 3 + row]
            path = os.path.join(TEMPLATES_DIR, f"{symbol}_{tag}_r{reel + 1}"
                                f"_{ROW_NAMES[row]}.png")
            cv2.imwrite(path, cut_patch(crop, row))
            print(f"Saved {path}")


def main():
    args = sys.argv[1:]
    if len(args) >= 2 and args[0] == "learn":
        learn(args[1], args[2:])
        return
    if len(args) != 1:
        sys.exit(__doc__)
    templates = load_templates()
    if not templates:
        sys.exit(f"No templates in {TEMPLATES_DIR}/; add some with `learn`.")
    if os.path.isfile(args[0]):
        found, grid, result, reason = read_frame(
            cv2.imread(args[0]), align.load_config(), align.require_anchors(),
            templates)
        if found:
            print(f"camera moved {found['shift']} since calibration")
    else:
        grid = read_grid(load_crops(args[0]), templates)
        result, reason = outcome(symbols_only(grid))
    if grid:
        print(format_grid(grid))
    print(f"-> {result} ({reason})")


if __name__ == "__main__":
    main()
