#!/usr/bin/env python3
"""Find the reels in a frame even after the camera has moved.

Calibration saves anchors/calibration.png: the cabinet around the reels in a
frame whose boxes in reels.json are known to be right. More anchors of the same
region under other lighting (reel lamps on or off) can be added from frames
where the reels were found. To locate the reels in a new frame, each anchor is
matched (with the symbol areas masked out, since they change
every spin) over +/-SEARCH px at quarter scale, then refined at full scale,
and finally each reel's own rim is matched within +/-REFINE px so a slight
tilt doesn't throw the outer reels off. The best-scoring anchor wins.

    align.py calibrate [frame.jpg]   start over: save anchors/calibration.png
                                     from a frame whose reels.json boxes are
                                     right (removes other anchors)
    align.py add-anchor frame.jpg    add that frame's lighting as an anchor,
                                     if the reels can be found in it
    align.py [frame.jpg]             report where the reels are now
"""
import glob
import json
import os
import sys

import cv2
import numpy as np

from capture_4k import FULL_PATH, REELS_CONFIG

ANCHORS_DIR = "anchors"
CALIBRATION_ANCHOR = os.path.join(ANCHORS_DIR, "calibration.png")
ANCHOR_PAD = (20, 35, 40, 40)  # left, top, right, bottom px around the boxes
SEARCH = 150      # px the camera may have moved since calibration
COARSE = 4        # downscale factor for the global search
REFINE = 12       # px each reel may differ from the global shift
RIM = 25          # px of cabinet around a reel used to refine it
INSET = 6         # px of a reel box's edge kept when masking its symbols
SCREEN_GAP = 35   # rows above the reels' tops still on the cabinet; above
                  # that, over the reels, is the video screen
# The right spot scored 0.52 for a lamps-off anchor on lamps-on frames, where
# nothing else in the search beat 0.33. So a match counts from MIN_SCORE if it
# also beats everything more than DISTINCT_PX away by MIN_DISTINCT.
MIN_SCORE = 0.45
MIN_DISTINCT = 0.1
DISTINCT_PX = 20
MIN_REEL_SCORE = 0.4


def prep(image):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)


def load_config(path=REELS_CONFIG):
    with open(path) as f:
        return json.load(f)


def anchor_box(cfg):
    boxes = [r["box"] for r in cfg["reels"]] + [cfg["reference"]["box"]]
    left, top, right, bottom = ANCHOR_PAD
    return (min(b[0] for b in boxes) - left, min(b[1] for b in boxes) - top,
            max(b[2] for b in boxes) + right, max(b[3] for b in boxes) + bottom)


def symbol_mask(shape, origin, boxes, screen=False):
    """255 everywhere except inside the reel boxes (less INSET px) and, with
    screen=True, the video screen above them."""
    mask = np.full(shape[:2], 255, np.uint8)
    ox, oy = origin
    for x0, y0, x1, y1 in boxes:
        mask[max(y0 - oy + INSET, 0):y1 - oy - INSET,
             max(x0 - ox + INSET, 0):x1 - ox - INSET] = 0
    if screen:
        top = min(b[1] for b in boxes) - SCREEN_GAP - oy
        mask[:max(top, 0), boxes[0][0] - ox:boxes[-1][2] - ox] = 0
    return mask


def match(window, template, mask):
    """Best (x, y, score) of a masked template in a window."""
    r = cv2.matchTemplate(window, template, cv2.TM_CCOEFF_NORMED, mask=mask)
    r[~np.isfinite(r)] = -1
    _, score, _, (x, y) = cv2.minMaxLoc(r)
    return x, y, score


def crop(frame, x0, y0, x1, y1):
    """frame[y0:y1, x0:x1] clipped to the frame, plus the clipped origin."""
    h, w = frame.shape[:2]
    cx0, cy0 = max(x0, 0), max(y0, 0)
    return frame[cy0:min(y1, h), cx0:min(x1, w)], (cx0, cy0)


def save_anchor(name, image, reels, reference):
    """Write anchors/<name>.png and .json; boxes are relative to the image."""
    os.makedirs(ANCHORS_DIR, exist_ok=True)
    path = os.path.join(ANCHORS_DIR, f"{name}.png")
    cv2.imwrite(path, image)
    with open(os.path.join(ANCHORS_DIR, f"{name}.json"), "w") as f:
        json.dump({"reels": [[i, list(b)] for i, b in reels],
                   "reference": list(reference)}, f)
    return path


def relative(box, ox, oy):
    x0, y0, x1, y1 = box
    return (x0 - ox, y0 - oy, x1 - ox, y1 - oy)


def calibrate(frame, cfg):
    for path in glob.glob(os.path.join(ANCHORS_DIR, "*.*")):
        os.remove(path)
    x0, y0, x1, y1 = anchor_box(cfg)
    save_anchor("calibration", frame[y0:y1, x0:x1],
                [(r["id"], relative(r["box"], x0, y0)) for r in cfg["reels"]],
                relative(cfg["reference"]["box"], x0, y0))
    return (x0, y0, x1, y1)


def add_anchor(frame, cfg, anchors, name):
    """Cut the anchor region from a frame where the reels can be found,
    keeping where each reel sat in it (the camera may also have tilted)."""
    found = locate(frame, cfg, anchors)
    if found is None:
        return None
    dx, dy = found["shift"]
    x0, y0, x1, y1 = anchor_box(cfg)
    ox, oy = x0 + dx, y0 + dy
    path = save_anchor(name, frame[oy:y1 + dy, ox:x1 + dx],
                       [(i, relative(b, ox, oy)) for i, b in found["reels"]],
                       relative(found["reference"], ox, oy))
    return path, found


def calibration_geometry(cfg, image):
    x0, y0, _, _ = anchor_box(cfg)
    return {"image": image,
            "reels": [(r["id"], relative(r["box"], x0, y0)) for r in cfg["reels"]],
            "reference": relative(cfg["reference"]["box"], x0, y0)}


def locate(frame, cfg, anchors):
    """Return {"shift": (dx, dy), "score": s, "reels": [(id, box)...],
    "reference": box} for the best-matching anchor, or None if no anchor
    finds the reels. anchors comes from load_anchors(); a bare image is
    taken as a calibration anchor for cfg."""
    if isinstance(anchors, np.ndarray):
        anchors = [calibration_geometry(cfg, anchors)]
    results = [r for r in (locate_one(frame, cfg, a) for a in anchors) if r]
    return max(results, key=lambda r: r["score"], default=None)


def locate_one(frame, cfg, anchor):
    ax0, ay0, _, _ = anchor_box(cfg)
    a = prep(anchor["image"])
    h, w = a.shape
    rel = [box for _, box in anchor["reels"]]
    mask = symbol_mask(a.shape, (0, 0), rel, screen=True)

    # Coarse: the whole anchor over +/-SEARCH at 1/COARSE scale.
    window, (wx, wy) = crop(frame, ax0 - SEARCH, ay0 - SEARCH,
                            ax0 + w + SEARCH, ay0 + h + SEARCH)
    small = lambda im: cv2.resize(im, None, fx=1 / COARSE, fy=1 / COARSE,
                                  interpolation=cv2.INTER_AREA)
    r = cv2.matchTemplate(small(prep(window)), small(a), cv2.TM_CCOEFF_NORMED,
                          mask=(small(mask) > 127).astype(np.uint8) * 255)
    r[~np.isfinite(r)] = -1
    _, score, _, (x, y) = cv2.minMaxLoc(r)
    k = DISTINCT_PX // COARSE
    r[max(y - k, 0):y + k + 1, max(x - k, 0):x + k + 1] = -1
    if score < MIN_SCORE or score - r.max() < MIN_DISTINCT:
        return None
    tx, ty = wx + x * COARSE, wy + y * COARSE

    # Fine: full scale within one coarse step.
    p = COARSE + 2
    window, (wx, wy) = crop(frame, tx - p, ty - p, tx + w + p, ty + h + p)
    if window.shape[0] < h or window.shape[1] < w:
        return None
    x, y, score = match(prep(window), a, mask)
    if score < MIN_SCORE:
        return None
    tx, ty = wx + x, wy + y

    # Per reel: that reel's rim within +/-REFINE of where the anchor puts it.
    reels = []
    for reel_id, (x0, y0, x1, y1) in anchor["reels"]:
        rx0, ry0 = max(x0 - RIM, 0), max(y0 - RIM, 0)
        rim = a[ry0:y1 + RIM, rx0:x1 + RIM]
        rim_mask = symbol_mask(rim.shape, (rx0, ry0), [(x0, y0, x1, y1)])
        window, (wx, wy) = crop(frame, tx + rx0 - REFINE, ty + ry0 - REFINE,
                                tx + rx0 + rim.shape[1] + REFINE,
                                ty + ry0 + rim.shape[0] + REFINE)
        ox, oy = tx, ty
        if window.shape[0] >= rim.shape[0] and window.shape[1] >= rim.shape[1]:
            mx, my, rscore = match(prep(window), rim, rim_mask)
            if rscore >= MIN_REEL_SCORE:
                ox, oy = wx + mx - rx0, wy + my - ry0
        reels.append((reel_id, (ox + x0, oy + y0, ox + x1, oy + y1)))

    fx0, fy0, fx1, fy1 = anchor["reference"]
    return {"shift": (tx - ax0, ty - ay0), "score": score, "reels": reels,
            "reference": (tx + fx0, ty + fy0, tx + fx1, ty + fy1)}


def load_anchors(directory=ANCHORS_DIR):
    """All anchors (calibration first) as {"image", "reels", "reference"};
    [] if not calibrated."""
    paths = sorted(glob.glob(os.path.join(directory, "*.png")),
                   key=lambda p: p != CALIBRATION_ANCHOR)
    anchors = []
    for path in paths:
        with open(os.path.splitext(path)[0] + ".json") as f:
            geom = json.load(f)
        anchors.append({"image": cv2.imread(path),
                        "reels": [(i, tuple(b)) for i, b in geom["reels"]],
                        "reference": tuple(geom["reference"])})
    return anchors


def require_anchors():
    anchors = load_anchors()
    if not anchors:
        sys.exit(f"No anchors in {ANCHORS_DIR}/; run `align.py calibrate` on "
                 "a frame whose reels.json boxes are right.")
    return anchors


def main():
    args = sys.argv[1:]
    cfg = load_config()
    if args and args[0] == "calibrate":
        path = args[1] if len(args) > 1 else FULL_PATH
        print(f"Saved {CALIBRATION_ANCHOR} from {path}, box "
              f"{calibrate(cv2.imread(path), cfg)}")
        return
    if len(args) == 2 and args[0] == "add-anchor":
        name = os.path.basename(os.path.dirname(os.path.abspath(args[1])))
        added = add_anchor(cv2.imread(args[1]), cfg, require_anchors(), name)
        if added is None:
            sys.exit("Couldn't find the reels in that frame; not adding it.")
        print(f"Saved {added[0]} (camera had moved {added[1]['shift']})")
        return
    frame = cv2.imread(args[0] if args else FULL_PATH)
    found = locate(frame, cfg, require_anchors())
    if found is None:
        sys.exit("Couldn't find the reels: recalibrate.")
    print(f"camera moved {found['shift']} (score {found['score']:.2f})")
    for reel_id, box in found["reels"]:
        print(f"  reel {reel_id}: {box}")


if __name__ == "__main__":
    main()
