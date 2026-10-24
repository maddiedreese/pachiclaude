#!/usr/bin/env python3
"""Grab one native 3840x2160 frame from the Elgato Facecam 4K.

Probes AVFoundation camera indices, keeps the one that actually delivers 4K,
lets auto-exposure settle, and writes reels_4k_full.jpg plus a 1280px preview.
Never touches the camera's digital zoom: reel windows are cropped in software
using the boxes in reels.json and written as reel_<id>.png.
"""
import json
import sys
import time

import cv2

WIDTH, HEIGHT = 3840, 2160
SETTLE_SECONDS = 2.0
MAX_INDEX = 5
FULL_PATH = "reels_4k_full.jpg"
PREVIEW_PATH = "reels_4k_preview.jpg"
PREVIEW_WIDTH = 1280
REELS_CONFIG = "reels.json"


def open_4k(index):
    cap = cv2.VideoCapture(index, cv2.CAP_AVFOUNDATION)
    if not cap.isOpened():
        return None
    # Resolution must be requested before the first read or AVFoundation
    # locks in its default format.
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, HEIGHT)
    ok, frame = cap.read()
    if not ok or frame is None:
        cap.release()
        return None
    h, w = frame.shape[:2]
    print(f"  index {index}: {w}x{h}")
    if (w, h) != (WIDTH, HEIGHT):
        cap.release()
        return None
    return cap


def find_camera():
    for index in range(MAX_INDEX):
        cap = open_4k(index)
        if cap is not None:
            return index, cap
    return None, None


def crop_reels(frame):
    with open(REELS_CONFIG) as f:
        reels = json.load(f)["reels"]
    for reel in reels:
        x0, y0, x1, y1 = reel["box"]
        path = f"reel_{reel['id']}.png"
        cv2.imwrite(path, frame[y0:y1, x0:x1])
        print(f"Saved {path} ({x1 - x0}x{y1 - y0})")


def main():
    print("Probing cameras for native 4K...")
    index, cap = find_camera()
    if cap is None:
        sys.exit("No camera delivered 3840x2160. Is the Facecam plugged in "
                 "and free (Photo Booth / OBS / Camera Hub closed)?")
    print(f"Using index {index}; letting exposure settle {SETTLE_SECONDS:.0f}s")

    frame = None
    deadline = time.monotonic() + SETTLE_SECONDS
    while time.monotonic() < deadline:
        ok, f = cap.read()
        if ok:
            frame = f
    ok, f = cap.read()
    if ok:
        frame = f
    cap.release()
    if frame is None:
        sys.exit("Camera opened but stopped returning frames.")

    cv2.imwrite(FULL_PATH, frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
    scale = PREVIEW_WIDTH / frame.shape[1]
    preview = cv2.resize(frame, None, fx=scale, fy=scale,
                         interpolation=cv2.INTER_AREA)
    cv2.imwrite(PREVIEW_PATH, preview, [cv2.IMWRITE_JPEG_QUALITY, 90])
    print(f"Saved {FULL_PATH} ({frame.shape[1]}x{frame.shape[0]}) "
          f"and {PREVIEW_PATH} ({preview.shape[1]}x{preview.shape[0]})")
    crop_reels(frame)


if __name__ == "__main__":
    main()
