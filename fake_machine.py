#!/usr/bin/env python3
"""A stand-in slot machine on the bridge, for using pachiclaude without the
camera and for tests.

Interactive: type a key and Enter to play a spin with that outcome.

    y approve    c cherry (approve, with a roast)    7 yolo
    n deny       w watermelon (write tests first)    r replay    m misread

--script approve,replay,deny,... answers each request from the app with the
next outcome in the list instead (a replay or misread plays the next one too).
"""
import argparse
import sys
import threading
import time

from bridge import OUTCOMES, PORT, MachineServer, PortInUse

KEYS = {"y": "approve", "n": "deny", "w": "deny_tests", "c": "approve_roast",
        "7": "yolo", "r": "replay", "m": "misread"}
STEP = 0.3  # seconds between a spin's events


def play(server, outcome, delay=STEP):
    server.spin()
    for reel in (1, 2, 3):
        time.sleep(delay)
        server.stop(reel)
    time.sleep(delay)
    server.outcome(outcome, "fake machine")


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--port", type=int, default=PORT)
    p.add_argument("--script", help="comma-separated outcomes to play, "
                   f"one per request ({', '.join(OUTCOMES)})")
    p.add_argument("--step", type=float, default=STEP,
                   help="seconds between a spin's events")
    args = p.parse_args()

    script = args.script.split(",") if args.script else None
    if script and any(o not in OUTCOMES for o in script):
        sys.exit(f"Outcomes must be from: {', '.join(OUTCOMES)}")
    lock = threading.Lock()

    def on_request(request):
        if request.get("type") != "request":
            return
        print(f"Claude wants {request['tool']}: {request['summary']}", flush=True)
        if script is None:
            print("  pick an outcome: " + "  ".join(f"{k}={v}" for k, v in KEYS.items()),
                  flush=True)
            return

        def answer():
            with lock:
                while script:
                    outcome = script.pop(0)
                    print(f"  spinning: {outcome}", flush=True)
                    play(server, outcome, args.step)
                    if outcome not in ("replay", "misread"):
                        return
                print("  script finished; nothing left to play", flush=True)
        threading.Thread(target=answer, daemon=True).start()

    try:
        server = MachineServer(port=args.port, on_request=on_request).start()
    except PortInUse as e:
        sys.exit(str(e))
    print(f"Fake machine on ws://127.0.0.1:{args.port}", flush=True)
    try:
        if script is not None:
            threading.Event().wait()
        for line in sys.stdin:
            outcome = KEYS.get(line.strip().lower()) or line.strip()
            if outcome in OUTCOMES:
                with lock:
                    play(server, outcome, args.step)
            elif line.strip():
                print("  keys: " + "  ".join(f"{k}={v}" for k, v in KEYS.items()))
    except KeyboardInterrupt:
        pass
    server.close()


if __name__ == "__main__":
    main()
