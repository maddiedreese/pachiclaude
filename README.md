# pachiclaude

A real pachislot machine that decides Claude Code's tool permissions.

The machine is an unmodified, button-only *Matsuri no Tatsujin* (Taiko) pachislot. A 4K camera watches the reels from a few feet back, a Python + OpenCV loop reads where they stop, and the result approves or denies Claude's Write/Edit/Bash calls through the Agent SDK.

| Reels | Outcome |
|---|---|
| 7-7-7 / BAR | YOLO: the next 5 calls are auto-approved |
| Replay | Spin again |
| Bell | Approve |
| Watermelon | Deny: "write tests first" |
| Cherry on reel 1 | Approve, with a roast |
| Miss | Deny |
| Misread | Spin again |

## Status

Camera capture and reel cropping work. Stop detection, symbol matching, the WebSocket bridge and the terminal app are next.

## Setup

macOS, an Elgato Facecam 4K over USB, Python 3.

```bash
python3 -m venv .venv
.venv/bin/pip install opencv-python numpy
```

## Capture a frame

```bash
.venv/bin/python capture_4k.py
```

The script finds the camera that delivers native 3840x2160, waits ~2s for exposure, and saves `reels_4k_full.jpg`, a 1280px preview, and one crop per reel (`reel_1.png`–`reel_3.png`) using the boxes in `reels.json`. The camera's digital zoom is never used; if the tripod moves, update `reels.json`.

Run it from a terminal app that has camera access (System Settings → Privacy & Security → Camera). Close Photo Booth, OBS or Camera Hub first if one of them is holding the camera.
