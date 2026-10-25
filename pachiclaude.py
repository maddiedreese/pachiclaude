#!/usr/bin/env python3
"""Claude Code, but a pachislot machine decides its tool permissions.

Runs Claude Code through the Agent SDK in a Claude Code-style terminal
(tui.py). After each prompt you pull the lever, and the spin decides what
Claude may do that turn (writing files, running commands, ...) before it
starts: see gate.py for the outcome rules. Start reel_watch.py (or
fake_machine.py) first.

    pachiclaude.py                          chat until Ctrl-D or /exit
    pachiclaude.py "add a --dry-run flag"   one task, then exit
"""
import argparse
import asyncio
import os
import signal
import sys

from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions,
                              ClaudeSDKClient, ResultMessage, TextBlock,
                              ToolResultBlock, ToolUseBlock, UserMessage)
from rich.text import Text

import bridge
from gate import Gate, MachineClient
from tui import TerminalUI

MODEL = "claude-opus-5-5"


def options(args, gate):
    return ClaudeAgentOptions(
        can_use_tool=gate.can_use_tool,
        permission_mode="default",
        model=args.model,
        cwd=args.cwd,
        # Allow rules in settings files would approve calls without asking the
        # machine, so by default none are loaded.
        setting_sources=["user", "project", "local"] if args.use_settings else [],
    )


def show(ui, msg):
    if isinstance(msg, AssistantMessage):
        for block in msg.content:
            if isinstance(block, TextBlock) and block.text.strip():
                ui.assistant_text(block.text)
            elif isinstance(block, ToolUseBlock):
                ui.tool_use(block.id, block.name, block.input)
    elif isinstance(msg, UserMessage) and isinstance(msg.content, list):
        for block in msg.content:
            if isinstance(block, ToolResultBlock):
                ui.tool_result(block.tool_use_id, block.content, block.is_error)
    elif isinstance(msg, ResultMessage):
        ui.end_turn()
        ui.done(msg.num_turns, msg.total_cost_usd)


async def turn(client, ui, gate, prompt):
    """Spin for one prompt, then run it. Ctrl-C during the spin drops the
    prompt; during the run it interrupts Claude."""
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGINT, gate.interrupt)
    try:
        outcome = await gate.spin_for_turn(prompt)
    finally:
        loop.remove_signal_handler(signal.SIGINT)
    if outcome is None:
        return

    interrupted = False

    def on_sigint():
        nonlocal interrupted
        interrupted = True
        asyncio.ensure_future(client.interrupt())

    loop.add_signal_handler(signal.SIGINT, on_sigint)
    ui.start_turn()
    try:
        await client.query(gate.prompt_for(prompt))
        async for msg in client.receive_response():
            show(ui, msg)
    finally:
        loop.remove_signal_handler(signal.SIGINT)
        ui.end_turn()
        if interrupted:
            ui.interrupted()


async def run(args):
    ui = TerminalUI(args.cwd, args.model, args.machine)
    machine = MachineClient(args.machine, ui)
    gate = Gate(machine, ui, args.cwd)
    ui.yolo_left = lambda: gate.yolo_left
    listener = asyncio.create_task(machine.run())
    ui.welcome()
    client = ClaudeSDKClient(options(args, gate))
    await client.connect()
    try:
        if args.task:
            ui.print(Text(f"> {args.task}\n", "grey50"))
            await turn(client, ui, gate, args.task)
            return
        loop = asyncio.get_running_loop()
        while True:
            # input() runs on a worker thread that Ctrl-C can't reach, and
            # asyncio would wait for it on the way out: quit right here.
            loop.add_signal_handler(signal.SIGINT, quit_now)
            try:
                prompt = await asyncio.to_thread(ui.read_prompt)
            finally:
                loop.remove_signal_handler(signal.SIGINT)
            if prompt is None:
                return
            command = prompt.strip()
            if not command:
                continue
            if command in ("/exit", "/quit"):
                return
            if command == "/help":
                ui.help()
            elif command == "/status":
                ui.show_status()
            elif command == "/clear":
                await client.disconnect()
                client = ClaudeSDKClient(options(args, gate))
                await client.connect()
                ui.print("  (new conversation)\n")
            else:
                await turn(client, ui, gate, prompt)
    finally:
        await client.disconnect()
        listener.cancel()
        try:
            await listener
        except asyncio.CancelledError:
            pass


def quit_now():
    sys.stdout.write("\x1b[2B\r\n")
    sys.stdout.flush()
    os._exit(0)


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("task", nargs="?", help="run one task and exit")
    p.add_argument("--cwd", default=os.getcwd(),
                   help="directory Claude works in (default: here)")
    p.add_argument("--model", default=MODEL)
    p.add_argument("--machine", default=bridge.URL,
                   help=f"bridge URL (default {bridge.URL})")
    p.add_argument("--use-settings", action="store_true",
                   help="load ~/.claude and project settings; their allow "
                        "rules skip the machine")
    args = p.parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass  # Ctrl-C at the prompt quits
    print()


if __name__ == "__main__":
    main()
