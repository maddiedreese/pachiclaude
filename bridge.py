"""WebSocket bridge between the slot machine and the agent.

The machine side (reel_watch.py, or fake_machine.py) runs a MachineServer and
publishes JSON events to every connected client:

    {"type": "hello", "protocol": "pachiclaude/1", "spin_id": n,
     "spinning": bool}                                   on connect
    {"type": "spin", "spin_id": n}                       reels started
    {"type": "stop", "spin_id": n, "reel": 1}            a reel stopped
    {"type": "outcome", "spin_id": n, "outcome": "approve", "reason": "...",
     "grid": [["bell", ...], ...]}                       spin settled and read
    {"type": "false_spin", "spin_id": n}                 too short, ignored

Clients must check the hello's protocol before sending anything: another
program could be listening on the port. spin_id increases with every spin, so a client can tell a spin that started
after it asked for a decision from one that was already under way.

Clients may send {"type": "request", "tool": "Bash", "summary": "..."} so the
machine's terminal can show what the next spin decides; {"type": "done"}
clears it.
"""
import asyncio
import json
import threading

from websockets.asyncio.server import broadcast, serve
from websockets.exceptions import ConnectionClosed

HOST = "127.0.0.1"
PORT = 47777
PROTOCOL = "pachiclaude/1"
URL = f"ws://{HOST}:{PORT}"

OUTCOMES = ("yolo", "replay", "approve", "deny_tests", "approve_roast",
            "deny", "misread")


class PortInUse(RuntimeError):
    pass


class MachineServer:
    """Serves machine events from a background thread, so a synchronous
    camera loop can call publish() between frames."""

    def __init__(self, host=HOST, port=PORT, on_request=None):
        self.host, self.port = host, port
        self.on_request = on_request  # called with each client request dict
        self.spin_id = 0
        self.spinning = False
        self._loop = None
        self._server = None
        self._ready = threading.Event()
        self._error = None

    # Called from the machine's own thread.

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()
        self._ready.wait()
        if isinstance(self._error, OSError):
            raise PortInUse(f"Can't serve on {self.host}:{self.port} "
                            f"({self._error.strerror or self._error}). Another "
                            "program may be using it: pass --port, and the "
                            "same port to pachiclaude.py --machine.")
        if self._error:
            raise self._error
        return self

    def spin(self):
        self.spin_id += 1
        self.spinning = True
        self.publish({"type": "spin"})

    def stop(self, reel):
        self.publish({"type": "stop", "reel": reel})

    def outcome(self, outcome, reason, grid=None):
        self.spinning = False
        self.publish({"type": "outcome", "outcome": outcome, "reason": reason,
                      "grid": grid})

    def false_spin(self):
        self.spinning = False
        self.publish({"type": "false_spin"})

    def publish(self, event):
        message = json.dumps({**event, "spin_id": self.spin_id})
        self._loop.call_soon_threadsafe(
            lambda: broadcast(self._server.connections, message))

    def close(self):
        if self._loop:
            self._loop.call_soon_threadsafe(self._server.close)

    # The server's own thread.

    def _run(self):
        self._loop = asyncio.new_event_loop()
        try:
            self._loop.run_until_complete(self._serve())
        except Exception as e:  # e.g. the port is taken
            self._error = e
            self._ready.set()

    async def _serve(self):
        async with serve(self._handle, self.host, self.port) as server:
            self._server = server
            self._ready.set()
            await server.wait_closed()

    async def _handle(self, ws):
        await ws.send(json.dumps({"type": "hello", "protocol": PROTOCOL,
                                  "spin_id": self.spin_id,
                                  "spinning": self.spinning}))
        try:
            async for raw in ws:
                try:
                    request = json.loads(raw)
                except ValueError:
                    continue
                if self.on_request:
                    self.on_request(request)
        except ConnectionClosed:
            pass  # a client going away is normal
