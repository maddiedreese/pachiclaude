#!/usr/bin/env python3
"""Claude Code, but a pachislot machine decides its tool permissions.

Runs Claude Code through the Agent SDK. Whenever Claude Code would ask for
permission (writing or editing files, running commands, ...), the request is
sent to the machine over the bridge and the next spin decides: see gate.py
for the outcome rules. Start reel_watch.py (or fake_machine.py) first.

    pachiclaude.py                       chat until Ctrl-D
    pachiclaude.py "add a --dry-run flag"   one task, then exit
"""
import argparse
import asyncio
import os
import sys

from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions,
                              ClaudeSDKClient, ResultMessage, TextBlock,
                              ToolUseBlock)

import bridge
from gate import Gate, MachineClient, describe

MODEL = "claude-opus-5-5"

SYMBOLS = {"red7": "7", "blue7": "7", "bar": "BAR", "replay": "REPLAY",
           "bell": "BELL", "watermelon": "MELON", "cherry": "CHERRY",
           None: "???"}


def color(code, text):
    return f"\033[{code}m{text}\033[0m" if sys.stdout.isatty() else text


class TerminalUI:
    def __init__(self, cwd):
        self.cwd = cwd

    def status(self, text):
        print(color("2", f"[machine] {text}"), flush=True)

    def request(self, tool, summary):
        print(color("1;33", f"\n🎰 Claude wants {tool}: {summary}"))
        print(color("33", "   Pull the lever!"), flush=True)

    def event(self, event):
        kind = event["type"]
        if kind == "spin":
            print(color("33", "   spinning..."), end="", flush=True)
        elif kind == "stop":
            print(color("33", f" {event['reel']}"), end="", flush=True)
        elif kind == "false_spin":
            print(color("2", "  (too short, spin again)"), flush=True)
        elif kind == "outcome":
            print(flush=True)
            if event.get("grid"):
                for row in range(3):
                    print("   " + " ".join(f"{SYMBOLS.get(reel[row], reel[row]):>7}"
                                           for reel in event["grid"]))

    def retry(self, outcome, reason):
        print(color("36", f"   {outcome}: spin again ({reason})"), flush=True)

    def decision(self, tool, summary, action, note):
        mark = color("1;32", "✔ allowed") if action == "allow" else color("1;31", "✘ denied")
        print(f"   {mark} {tool}: {summary}")
        if note:
            print(color("2", f"   {note}"))
        print(flush=True)

    def message(self, msg):
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock) and block.text.strip():
                    print(block.text, flush=True)
                elif isinstance(block, ToolUseBlock):
                    summary = describe(block.name, block.input, self.cwd)
                    print(color("2", f"→ {block.name}: {summary}"),
                          flush=True)
        elif isinstance(msg, ResultMessage):
            cost = f", ${msg.total_cost_usd:.4f}" if msg.total_cost_usd else ""
            print(color("2", f"[{msg.num_turns} turns{cost}]"), flush=True)


async def prompts(task):
    if task:
        yield task
        return
    while True:
        try:
            line = await asyncio.to_thread(input, color("1", "\n> "))
        except EOFError:
            return
        if line.strip():
            yield line


async def run(args):
    ui = TerminalUI(args.cwd)
    machine = MachineClient(args.machine, ui)
    listener = asyncio.create_task(machine.run())
    gate = Gate(machine, ui, args.cwd)
    options = ClaudeAgentOptions(
        can_use_tool=gate.can_use_tool,
        permission_mode="default",
        model=args.model,
        cwd=args.cwd,
        # Allow rules in settings files would approve calls without asking the
        # machine, so by default none are loaded.
        setting_sources=["user", "project", "local"] if args.use_settings else [],
    )
    try:
        async with ClaudeSDKClient(options) as client:
            async for prompt in prompts(args.task):
                await client.query(prompt)
                async for msg in client.receive_response():
                    ui.message(msg)
    finally:
        listener.cancel()
        try:
            await listener
        except asyncio.CancelledError:
            pass


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
        print()


if __name__ == "__main__":
    main()
