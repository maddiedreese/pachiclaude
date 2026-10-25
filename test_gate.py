#!/usr/bin/env python3
"""Checks for the bridge and the permission gate: run with
`.venv/bin/python test_gate.py`.

Runs a real MachineServer on a spare port with the real MachineClient and
Gate, and plays spins from the test; nothing calls Claude.
"""
import asyncio
import socket

from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

import gate
from bridge import MachineServer, PortInUse


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

    def request(self, tool, summary):
        self.log.append(("request", tool, summary))
        self.asked.set()

    def event(self, event):
        self.log.append(("event", event["type"], event["spin_id"]))

    def retry(self, outcome, reason):
        self.log.append(("retry", outcome))

    def decision(self, tool, summary, action, note):
        self.log.append(("decision", action, note))


async def spin(server, outcome):
    server.spin()
    for reel in (1, 2, 3):
        server.stop(reel)
    server.outcome(outcome, "test")
    await asyncio.sleep(0.05)


async def ask(g, ui, tool="Bash", tool_input=None):
    """Start a permission request and wait until it is waiting for a spin."""
    ui.asked.clear()
    task = asyncio.create_task(
        g.can_use_tool(tool, tool_input or {"command": "ls"}, None))
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


def check_describe():
    assert gate.describe("Bash", {"command": "ls -la"}) == "ls -la"
    assert gate.describe("Write", {"file_path": "/repo/src/a.py"}, "/repo") == "src/a.py"
    assert gate.describe("Write", {"file_path": "/elsewhere/a.py"}, "/repo") == "/elsewhere/a.py"
    assert gate.describe("Edit", {"file_path": "/repo2/a.py"}, "/repo") == "/repo2/a.py"
    assert len(gate.describe("mcp__x", {"blob": "y" * 500})) == 120


def check_decide():
    assert gate.decide("approve") == ("allow", None)
    assert gate.decide("yolo") == ("allow", None)
    action, roast = gate.decide("approve_roast")
    assert action == "allow" and roast in gate.ROASTS
    assert gate.decide("deny_tests")[0] == "deny"
    assert gate.decide("deny")[0] == "deny"
    assert gate.decide("replay") == ("retry", None)
    assert gate.decide("misread") == ("retry", None)
    assert gate.decide("something new") == ("retry", None)


async def check_outcomes():
    requests = []
    server, ui, g, listener = await setup(requests)

    task = await ask(g, ui, "Bash", {"command": "rm -rf build"})
    await spin(server, "approve")
    assert isinstance(await result(task), PermissionResultAllow)
    await asyncio.sleep(0.1)  # the server reads client messages on its own thread
    assert {"type": "request", "tool": "Bash", "summary": "rm -rf build"} in requests
    assert {"type": "done"} in requests

    task = await ask(g, ui, "Write", {"file_path": "/tmp/x.py", "content": ""})
    await spin(server, "deny_tests")
    denied = await result(task)
    assert isinstance(denied, PermissionResultDeny)
    assert "write tests first" in denied.message and "Write" in denied.message

    task = await ask(g, ui)
    await spin(server, "deny")
    denied = await result(task)
    assert isinstance(denied, PermissionResultDeny) and "{tool}" not in denied.message

    task = await ask(g, ui)
    await spin(server, "approve_roast")
    assert isinstance(await result(task), PermissionResultAllow)
    assert ui.log[-1][1] == "allow" and ui.log[-1][2] in gate.ROASTS

    # Replays and misreads mean spin again, as many times as it takes.
    task = await ask(g, ui)
    for outcome in ("replay", "misread", "replay"):
        await spin(server, outcome)
        assert not task.done()
    await spin(server, "approve")
    assert isinstance(await result(task), PermissionResultAllow)
    await teardown(server, listener)


async def check_stale_spin():
    """A spin already under way when Claude asks doesn't decide the call."""
    server, ui, g, listener = await setup()
    server.spin()
    await asyncio.sleep(0.05)
    task = await ask(g, ui)
    server.outcome("approve", "aimed before the request")
    await asyncio.sleep(0.1)
    assert not task.done()
    await spin(server, "deny")
    assert isinstance(await result(task), PermissionResultDeny)
    await teardown(server, listener)


async def check_yolo():
    server, ui, g, listener = await setup()
    task = await ask(g, ui)
    await spin(server, "yolo")
    assert isinstance(await result(task), PermissionResultAllow)
    for _ in range(gate.YOLO_CALLS):
        allowed = await asyncio.wait_for(g.can_use_tool("Bash", {"command": "ls"}, None), 1)
        assert isinstance(allowed, PermissionResultAllow)
    task = await ask(g, ui)  # credits used up: this one needs a spin
    await asyncio.sleep(0.1)
    assert not task.done()
    await spin(server, "deny")
    assert isinstance(await result(task), PermissionResultDeny)
    await teardown(server, listener)


async def check_one_spin_per_call():
    """Two calls at once each get their own spin, in order."""
    server, ui, g, listener = await setup()
    first = await ask(g, ui, "Bash", {"command": "first"})
    second = asyncio.create_task(g.can_use_tool("Bash", {"command": "second"}, None))
    await spin(server, "approve")
    assert isinstance(await result(first), PermissionResultAllow)
    await asyncio.sleep(0.1)
    assert not second.done()
    await spin(server, "deny")
    assert isinstance(await result(second), PermissionResultDeny)
    await teardown(server, listener)


async def check_machine_comes_online_later():
    port = free_port()
    ui = RecordingUI()
    machine = gate.MachineClient(f"ws://127.0.0.1:{port}", ui)
    listener = asyncio.create_task(machine.run())
    g = gate.Gate(machine, ui)
    task = asyncio.create_task(g.can_use_tool("Bash", {"command": "ls"}, None))
    await asyncio.sleep(0.3)
    assert not task.done() and not ui.asked.is_set()
    server = MachineServer(port=port).start()
    await asyncio.wait_for(ui.asked.wait(), 30)  # client reconnect backoff
    await spin(server, "approve")
    assert isinstance(await result(task), PermissionResultAllow)
    await teardown(server, listener)


async def check_impostor():
    """Another program on the port must never receive a request."""
    import json
    from websockets.asyncio.server import serve

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
        task = asyncio.create_task(g.can_use_tool("Bash", {"command": "cat ~/.ssh/id_rsa"}, None))
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
    check_decide()
    for check in (check_outcomes, check_stale_spin, check_yolo,
                  check_one_spin_per_call, check_machine_comes_online_later,
                  check_impostor, check_not_websocket, check_port_in_use):
        await check()
        print(f"{check.__name__}: ok")
    print("ok")


if __name__ == "__main__":
    asyncio.run(main())
