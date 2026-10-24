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

Camera capture, reel cropping and stop detection work. Symbol matching, the WebSocket bridge and the terminal app are next.

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

## Watch spins

```bash
.venv/bin/python reel_watch.py
```

Prints `SPIN` when the reels start, `reel N stopped` as each one halts, and `SETTLED` once they've all stopped, saving that frame's reel crops to `captures/<timestamp>/` (these become the symbol templates). A spin starts once every reel's frame-to-frame difference has been above `--spin` (default 8) for `--start-frames` (3) frames in a row, and a reel counts as stopped after `--still-frames` (4) frames below `--still` (3).

Guards stop camera bumps and lamp flashes from counting as motion:

- `reels.json` has a `reference` box on a static part of the cabinet (the gold figure left of reel 1). While its difference is above `--shake` (2.5) the camera itself moved, so reels can't start or stop on those frames (`SHAKE` in `--verbose`).
- Reels only restart on a lever pull, so if a reel that already stopped seems to move mid-spin, the reel lamps are flashing for a win. Those frames are held the same way (`FLASH`).
- A spin that settles within `--min-spin` seconds (1.0) of starting prints `FALSE SPIN` and is ignored.

If the tripod moves, update the reference box along with the reel boxes.

Add `--verbose` to print the per-reel and reference differences and tune those against the real machine, or `--video clip.mov` to run on a recording.

`test_reel_watch.py` checks the detector on synthetic spins, bumps and lamp flashes, and replays the real `--verbose` logs in `fixtures/`:

```bash
.venv/bin/python test_reel_watch.py
```
