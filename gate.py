"""Turn slot machine outcomes into Claude Code permission decisions.

One spin per prompt: Gate.spin_for_turn waits for the next spin that starts
after the user submits a prompt (replays and misreads mean spin again) and
sets what Claude may do until the next prompt. Gate.can_use_tool, the Agent
SDK's permission callback, then answers every request that turn straight
from that outcome:

    approve, approve_roast   everything is allowed
    yolo                     everything, and the next YOLO_PROMPTS prompts
                             skip the spin
    deny_tests               only test files and test runs ("write tests first")
    deny                     nothing that needs permission

prompt_for() tells Claude every turn what this spin allows, so it doesn't try
things that will be denied, and doesn't think an earlier denial still holds.
"""
import asyncio
import json
import os
import random
import re

from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, WebSocketException

from bridge import PROTOCOL

YOLO_PROMPTS = 5

ROASTS = (
    "Approved. A cherry, like the one on top of this pile of untested code.",
    "Approved, but the machine has seen your commit history.",
    "Approved. Even the reels think this needed a second look.",
    "Approved. Bold of you to gamble on this prompt.",
    "Approved. The cherry is the only thing here that's ripe.",
)

ALLOW = ("approve", "approve_roast", "yolo")
RETRY = ("replay", "misread")

DENY_MESSAGE = ("The pachislot machine that approves tool calls missed for "
                "this prompt, so you can't change files or run commands until "
                "the user's next prompt. Don't get it done through a "
                "different tool; do what you can without it.")
TESTS_MESSAGE = ("The pachislot machine landed on watermelon for this prompt: "
                 "write tests first. Until the user's next prompt you may only "
                 "create or edit test files and run tests.")
APPROVED_NOTE = ("[The pachislot machine that approves your tool calls approved "
                 "this prompt: you may write files and run commands as needed, "
                 "whatever earlier prompts allowed.]")
NOTES = {  # put in front of every prompt so Claude knows before it starts
    "approve": APPROVED_NOTE,
    "approve_roast": APPROVED_NOTE,
    "yolo": APPROVED_NOTE,
    "deny": ("[The pachislot machine that approves your tool calls missed for "
             "this prompt: you can read files, but writing files and running "
             "commands will be denied. Do what you can without them.]"),
    "deny_tests": ("[The pachislot machine that approves your tool calls "
                   "landed on watermelon for this prompt: write tests first. "
                   "You may only create or edit test files and run tests; "
                   "other edits and commands will be denied.]"),
}

TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|spec)/|(^|/)test_[^/]*$|"
                       r"_test\.[^/]+$|\.(test|spec)\.[^/]+$")
TEST_COMMAND = re.compile(r"^\s*(\S*python3?\s+-m\s+(pytest|unittest)|pytest|"
                          r"npm\s+(run\s+)?test|yarn\s+test|pnpm\s+test|"
                          r"npx\s+(jest|vitest)|jest|vitest|go\s+test|"
                          r"cargo\s+test)\b")


def is_test_call(tool_name, tool_input):
    """Whether a call only touches tests: editing a test file, or running a
    test command with nothing chained after it."""
    if tool_name == "Bash":
        command = tool_input.get("command", "")
        return bool(TEST_COMMAND.match(command)) and not re.search(r"[;&|`$<>]", command)
    path = tool_input.get("file_path") or tool_input.get("notebook_path")
    return bool(path) and bool(TEST_PATH.search(path))


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
        self.outcome = None    # this turn's outcome; None until a spin
        self.yolo_left = 0     # prompts that skip the spin
        self._waiting = None   # the queue of a spin in progress

    def interrupt(self):
        """Stop waiting for a spin (the user hit Ctrl-C)."""
        if self._waiting is not None:
            self._waiting.put_nowait({"type": "_interrupt"})

    async def spin_for_turn(self, prompt):
        """Decide what Claude may do this turn. Returns the outcome, or None
        if the user interrupted the wait."""
        self.outcome = None
        if self.yolo_left:
            self.yolo_left -= 1
            self.outcome = "yolo"
            self.ui.free_turn(self.yolo_left)
            return self.outcome
        summary = " ".join(prompt.split())
        queue = self.machine.subscribe()
        self._waiting = queue
        try:
            if not self.machine.connected.is_set():
                self.ui.status(f"Waiting for the machine at {self.machine.url}...")
                await self.machine.connected.wait()
            # Only a spin that starts after this point counts; one already
            # under way could have been aimed before the prompt existed.
            floor = self.machine.last_spin_id
            self.ui.request(prompt)
            await self.machine.send({"type": "request", "tool": "prompt",
                                     "summary": summary})
            while True:
                event = await queue.get()
                if event["type"] == "_interrupt":
                    self.ui.decision(None)
                    return None
                if event["spin_id"] <= floor:
                    continue
                self.ui.event(event)
                if event["type"] != "outcome":
                    continue
                outcome = event["outcome"]
                if outcome not in ALLOW + ("deny", "deny_tests"):
                    floor = event["spin_id"]  # replay, misread: pull again
                    continue
                self.outcome = outcome
                if outcome == "yolo":
                    self.yolo_left = YOLO_PROMPTS
                roast = random.choice(ROASTS) if outcome == "approve_roast" else None
                self.ui.decision(outcome, roast)
                return outcome
        finally:
            self._waiting = None
            self.machine.unsubscribe(queue)
            await self.machine.send({"type": "done"})

    def prompt_for(self, prompt):
        """The prompt to send Claude, with what this turn allows in front."""
        note = NOTES.get(self.outcome)
        return f"{note}\n\n{prompt}" if note else prompt

    async def can_use_tool(self, tool_name, tool_input, context):
        if self.outcome in ALLOW:
            return PermissionResultAllow()
        if self.outcome == "deny_tests":
            if is_test_call(tool_name, tool_input):
                return PermissionResultAllow()
            return PermissionResultDeny(message=TESTS_MESSAGE)
        return PermissionResultDeny(message=DENY_MESSAGE)
