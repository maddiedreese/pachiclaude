"""Turn slot machine outcomes into Claude Code permission decisions.

Gate.can_use_tool is the Agent SDK's permission callback: for every tool call
Claude Code asks about, it waits for the next spin that starts after the
request, reads the outcome off the bridge, and allows or denies the call.
Replays and misreads mean spin again; YOLO approves this call and the next
YOLO_CALLS without a spin.
"""
import asyncio
import json
import os
import random

from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, WebSocketException

from bridge import PROTOCOL

YOLO_CALLS = 5

ROASTS = (
    "Approved. A cherry, like the one on top of this pile of untested code.",
    "Approved, but the machine has seen your commit history.",
    "Approved. Even the reels think this needed a second look.",
    "Approved. Bold of you to gamble on this diff.",
    "Approved. The cherry is the only thing here that's ripe.",
)

DENY_MESSAGE = ("The pachislot machine that approves tool calls missed, so "
                "this {tool} call was denied. You can try again (that's "
                "another spin) or take a different approach.")
TESTS_MESSAGE = ("The pachislot machine landed on watermelon: write tests "
                 "first. Add or update tests that cover this change before "
                 "trying this {tool} call again.")


def decide(outcome):
    """Map a machine outcome to ("allow" | "deny" | "retry", note)."""
    if outcome in ("yolo", "approve"):
        return "allow", None
    if outcome == "approve_roast":
        return "allow", random.choice(ROASTS)
    if outcome == "deny_tests":
        return "deny", TESTS_MESSAGE
    if outcome == "deny":
        return "deny", DENY_MESSAGE
    return "retry", None  # replay, misread, or anything unknown


def describe(tool_name, tool_input, cwd=None):
    """One line saying what the tool call would do; paths inside cwd are
    shown relative to it."""
    if tool_name == "Bash":
        return tool_input.get("command", "")
    for key in ("file_path", "notebook_path"):
        if key in tool_input:
            path = tool_input[key]
            if cwd and os.path.abspath(path).startswith(os.path.abspath(cwd) + os.sep):
                return os.path.relpath(path, cwd)
            return path
    text = json.dumps(tool_input)
    return text if len(text) <= 120 else text[:117] + "..."


class MachineClient:
    """Stays connected to the machine's bridge and fans events out to
    subscribers. last_spin_id is the newest spin the machine has started."""

    def __init__(self, url, ui):
        self.url = url
        self.ui = ui
        self.ws = None
        self.last_spin_id = 0
        self.connected = asyncio.Event()
        self._subscribers = set()

    async def run(self):
        self._announced = self._warned = False
        while True:
            try:
                await self._connect_loop()
            except (WebSocketException, OSError):
                # Something answered that doesn't speak WebSocket at all;
                # connect() gives up on those, so keep retrying here.
                self._warn_not_machine()
                await asyncio.sleep(2)

    def _warn_not_machine(self):
        if not self._warned:
            self.ui.status(f"{self.url} isn't a pachiclaude machine (another "
                           "program on that port?); still looking...")
        self._warned = True

    async def _connect_loop(self):
        async for ws in connect(self.url, proxy=None):
            try:
                # Nothing is sent until the other end proves it's a machine:
                # requests name commands and files.
                if not await self._handshake(ws):
                    self._warn_not_machine()
                    await ws.close()
                    await asyncio.sleep(2)
                    continue
                self.ws = ws
                self.connected.set()
                if not self._announced:
                    self.ui.status(f"Connected to the machine at {self.url}")
                self._announced = True
                async for raw in ws:
                    event = json.loads(raw)
                    self.last_spin_id = max(self.last_spin_id, event["spin_id"])
                    for queue in self._subscribers:
                        queue.put_nowait(event)
            except (ConnectionClosed, ValueError, KeyError, TypeError):
                pass
            if self.ws is not None:
                self.ui.status("Lost the machine; reconnecting...")
            self.ws = None
            self.connected.clear()

    async def _handshake(self, ws):
        try:
            hello = json.loads(await asyncio.wait_for(ws.recv(), 3))
        except (asyncio.TimeoutError, ConnectionClosed, ValueError, TypeError):
            return False
        if not (isinstance(hello, dict) and hello.get("type") == "hello"
                and hello.get("protocol") == PROTOCOL):
            return False
        self.last_spin_id = max(self.last_spin_id, hello.get("spin_id", 0))
        return True

    async def send(self, message):
        if self.ws is not None and self.connected.is_set():
            try:
                await self.ws.send(json.dumps(message))
            except ConnectionClosed:
                pass

    def subscribe(self):
        queue = asyncio.Queue()
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue):
        self._subscribers.discard(queue)


class Gate:
    def __init__(self, machine, ui, cwd=None):
        self.machine = machine
        self.ui = ui
        self.cwd = cwd
        self.yolo_left = 0
        self._lock = asyncio.Lock()  # one spin decides one call

    async def can_use_tool(self, tool_name, tool_input, context):
        async with self._lock:
            summary = describe(tool_name, tool_input, self.cwd)
            if self.yolo_left:
                self.yolo_left -= 1
                self.ui.decision(tool_name, summary, "allow",
                                 f"YOLO ({self.yolo_left} free calls left)")
                return PermissionResultAllow()
            return await self._spin_for(tool_name, summary)

    async def _spin_for(self, tool_name, summary):
        queue = self.machine.subscribe()
        try:
            if not self.machine.connected.is_set():
                self.ui.status(f"Waiting for the machine at {self.machine.url}...")
                await self.machine.connected.wait()
            # Only a spin that starts after this point counts; one already
            # under way could have been aimed before the request existed.
            floor = self.machine.last_spin_id
            self.ui.request(tool_name, summary)
            await self.machine.send({"type": "request", "tool": tool_name,
                                     "summary": summary})
            while True:
                event = await queue.get()
                if event["spin_id"] <= floor:
                    continue
                self.ui.event(event)
                if event["type"] != "outcome":
                    continue
                outcome = event["outcome"]
                action, note = decide(outcome)
                if action == "retry":
                    self.ui.retry(outcome, event.get("reason"))
                    floor = event["spin_id"]
                    continue
                if outcome == "yolo":
                    self.yolo_left = YOLO_CALLS
                    note = f"YOLO: the next {YOLO_CALLS} calls are free"
                if action == "deny":
                    note = note.format(tool=tool_name)
                self.ui.decision(tool_name, summary, action, note or outcome)
                if action == "allow":
                    return PermissionResultAllow()
                return PermissionResultDeny(message=note)
        finally:
            self.machine.unsubscribe(queue)
            await self.machine.send({"type": "done"})
