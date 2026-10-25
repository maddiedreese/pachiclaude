# pachiclaude

A real pachislot machine that decides Claude Code's tool permissions.

The machine is an unmodified, button-only *Matsuri no Tatsujin* (Taiko) pachislot. A 4K camera watches the reels from a few feet back, a Python + OpenCV loop reads where they stop, and the result approves or denies Claude's Write/Edit/Bash calls through the Agent SDK.

| Reels | Outcome |
|---|---|
| 7-7-7 / BAR | YOLO: approve, and the next 5 calls are auto-approved |
| Replay | Spin again |
| Bell | Approve |
| Watermelon | Deny: "write tests first" |
| Cherry on reel 1 | Approve, with a roast |
| Miss | Deny |
| Misread | Spin again |

## Status

Everything runs end to end: the camera watches the reels, reads each spin, and the terminal app runs Claude Code with the machine deciding its permissions.

## Setup

macOS, an Elgato Facecam 4K over USB, Python 3.

```bash
python3 -m venv .venv
.venv/bin/pip install opencv-python numpy websockets claude-agent-sdk
```

The terminal app runs Claude Code through the Agent SDK, so it uses your existing Claude Code login.

## Play

Two terminals. In the one with camera access:

```bash
.venv/bin/python reel_watch.py
```

In the other, from the repository Claude should work in:

```bash
~/pachiclaude/.venv/bin/python ~/pachiclaude/pachiclaude.py               # chat, Ctrl-D to quit
~/pachiclaude/.venv/bin/python ~/pachiclaude/pachiclaude.py "add a --dry-run flag"
```

Whenever Claude Code would ask permission (writing or editing a file, running a command), both terminals say what it wants and to pull the lever. The next spin decides, per the table above:

- Only a spin that starts after the request counts, so a spin already under way can't be aimed at it.
- Replay and misread mean spin again, as often as it takes.
- A denial goes back to Claude as the reason ("write tests first" on watermelon), so Claude can change course or try again.
- YOLO approves this call and the next 5 without spinning.
- Reading and searching don't need permission, so they never wait for the machine.
- Settings files aren't loaded: their allow rules would approve calls without asking the machine. `--use-settings` loads them anyway.

Options: `--cwd` (where Claude works, default here), `--model` (default `claude-opus-5-5`), `--machine` (bridge URL, default `ws://127.0.0.1:47777`).

### Without the machine

`fake_machine.py` serves the same bridge from the keyboard: run it instead of `reel_watch.py`, then type `y` (approve), `n` (deny), `w` (watermelon), `c` (cherry), `7` (YOLO), `r` (replay) or `m` (misread) and Enter to play a spin. `--script approve,deny,...` answers each request automatically.

### The bridge

`reel_watch.py` publishes every spin on a localhost WebSocket (`bridge.py`, port 47777, `--port` to change; `--no-serve` turns it off): `spin`, `stop` per reel, then `outcome` with the outcome, the reason and the nine symbols, or `false_spin`. Every event carries a `spin_id`, which is how the app tells new spins from old ones. Clients can send a `request` (tool and summary) for the machine's terminal to show, but only after the server's `hello` names the `pachiclaude/1` protocol, so nothing leaks to some other program that happens to hold the port.

## Capture a frame

```bash
.venv/bin/python capture_4k.py
```

The script finds the camera that delivers native 3840x2160, waits ~2s for exposure, and saves `reels_4k_full.jpg`, a 1280px preview, and one crop per reel (`reel_1.png`–`reel_3.png`) using the boxes in `reels.json`. The camera's digital zoom is never used.

Run it from a terminal app that has camera access (System Settings → Privacy & Security → Camera). Close Photo Booth, OBS or Camera Hub first if one of them is holding the camera.

## Watch spins

```bash
.venv/bin/python reel_watch.py
```

Prints `SPIN` when the reels start, `reel N stopped` as each one halts, and `SETTLED` once they've all stopped, then the nine symbols it read and an `OUTCOME` line. The settled frame and its reel crops are saved to `captures/<timestamp>/`. A spin starts once every reel's frame-to-frame difference has been above `--spin` (default 8) for `--start-frames` (3) frames in a row, and a reel counts as stopped after `--still-frames` (4) frames below `--still` (3).

Guards stop camera bumps and lamp flashes from counting as motion:

- `reels.json` has a `reference` box on a static part of the cabinet (the gold figure left of reel 1). While its difference is above `--shake` (2.5) the camera itself moved, so reels can't start or stop on those frames (`SHAKE` in `--verbose`).
- Reels only restart on a lever pull, so if a reel that already stopped seems to move mid-spin, the reel lamps are flashing for a win. Those frames are held the same way (`FLASH`).
- A spin that settles within `--min-spin` seconds (1.0) of starting prints `FALSE SPIN` and is ignored.

Add `--verbose` to print the per-reel and reference differences and tune those against the real machine, or `--video clip.mov` to run on a recording.

## Find the reels

The camera drifts: over one afternoon it moved up to 48px between captures. So the boxes in `reels.json` are only right for the calibration frame, and `align.py` finds the reels in every new frame from the anchors in `anchors/`: the cabinet around the reels, with the symbol areas and video screen masked out, plus where each reel sat in it. It searches ±150px at quarter scale, refines at full scale, then matches each reel's rim within ±12px to absorb a slight tilt; the best-matching anchor wins. A match counts from 0.45 if it also beats every other spot more than 20px away by 0.1; otherwise the reels are lost and the spin is a misread.

The reel lamps change how the cabinet looks: the lamps-off calibration anchor scores about 0.54 on lamps-on frames, and an anchor taken with the lamps on scores 0.95. So after a spin where the reels were found, add that frame's lighting:

```bash
.venv/bin/python align.py add-anchor captures/<timestamp>/frame.jpg
```

To recalibrate (after moving the camera on purpose, or if `couldn't find the reels` shows up): capture a frame, set the boxes in `reels.json` so each covers one reel window with three whole symbols, and the reference box on the gold figure, then

```bash
.venv/bin/python align.py calibrate     # replaces everything in anchors/
.venv/bin/python align.py               # where the reels are in reels_4k_full.jpg now
```

## Read the symbols

```bash
.venv/bin/python read_reels.py reels_4k_full.jpg          # find and read a whole frame
.venv/bin/python read_reels.py captures/<timestamp>       # read saved crops
```

Each reel shows three rows. Every cell is matched against labelled examples in `templates/` as chroma (colour with brightness divided out), so it still reads with the reel lamps off or flashing. A cell counts only if its best symbol scores at least 0.6 and beats the next symbol by 0.15; anything less is unreadable and the whole spin is a misread (spin again), never a guess. 7s and BAR also have to agree on plain lightness, because three of them auto-approves 5 calls.

Payouts are checked on five lines: the three rows and both diagonals.

To teach it a symbol it gets wrong or doesn't know, label a capture's nine cells reel by reel, top to bottom (`red7 blue7 bar replay bell watermelon cherry`):

```bash
.venv/bin/python read_reels.py learn captures/<timestamp> bell watermelon red7 cherry bell replay watermelon bar replay
```

## Tests

```bash
.venv/bin/python test_reel_watch.py   # stop detection: synthetic spins, bumps, flashes, real logs
.venv/bin/python test_align.py        # finding the reels after moves, tilts and relighting
.venv/bin/python test_read_reels.py   # labelled captures in fixtures/reels/ and the payline rules
.venv/bin/python test_gate.py         # the bridge and the permission rules, with a real WebSocket and no Claude
```
