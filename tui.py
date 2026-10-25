"""A Claude Code-style terminal for pachiclaude, with a slot machine deciding
each prompt.

Looks like the Claude Code CLI: a welcome box, a boxed "> " input, "⏺" before
Claude's messages and tool calls, "⎿" under tool results and a "✻ Thinking…"
spinner. When a prompt is submitted, a permission box like Claude Code's shows
it with three reels where "Do you want to proceed?" would be: they spin while
the real ones do, lock as each one stops and light up the paying line, and
the outcome decides what Claude may do that turn.
"""
import os
import random
import sys
import time

from rich import box
from rich.cells import cell_len
from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from gate import YOLO_PROMPTS, describe

ACCENT = "#d97757"       # Claude orange
PERMISSION = "#b1b9f9"   # Claude Code's permission box border
GOLD = "#e8b931"
HIT = "on #7a5a00"       # background behind the paying line
DIM = "grey50"

SPINNER = "·✢✳✶✻✽✻✶✳✢"
VERBS = ("Thinking", "Pondering", "Cogitating", "Clauding", "Working",
         "Noodling", "Puzzling", "Mulling")

GLYPHS = {
    "red7": ("7", "bold #ff453a"),
    "blue7": ("7", "bold #409cff"),
    "bar": ("祭", "bold white on #5b2a86"),
    "replay": ("↺", "bold #5ac8fa"),
    "bell": ("🔔", ""),
    "watermelon": ("🍉", ""),
    "cherry": ("🍒", ""),
    None: ("?", "bold yellow"),
}
SPIN_SYMBOLS = [s for s in GLYPHS if s]
CELL = 6
LINES = {"top": (0, 0, 0), "middle": (1, 1, 1), "bottom": (2, 2, 2),
         "down": (0, 1, 2), "up": (2, 1, 0)}

BANNERS = {
    "approve": ("✔ Approved", "bold green"),
    "approve_roast": ("🍒 Approved, with a roast", "bold green"),
    "yolo": (f"★ YOLO! ★ Approved, and the next {YOLO_PROMPTS} prompts are free",
             f"bold {GOLD}"),
    "deny_tests": ("🍉 Watermelon: write tests first", "bold red"),
    "deny": ("✘ Miss: denied", "bold red"),
    "replay": ("↺ Replay: pull again", "bold #5ac8fa"),
    "misread": ("? Couldn't read the reels: pull again", "bold yellow"),
}

NAMES = {"red7": "red 7", "blue7": "blue 7", "bar": "祭"}


def pretty(reason):
    for symbol, name in NAMES.items():
        reason = reason.replace(symbol, name)
    return reason


VERDICTS = {  # what Claude may do this turn
    "approve": ("✔ Approved: Claude may write and run things", "bold green"),
    "approve_roast": ("✔ Approved, with a roast", "bold green"),
    "yolo": (f"★ YOLO! This prompt and the next {YOLO_PROMPTS} are approved",
             f"bold {GOLD}"),
    "deny_tests": ("🍉 Write tests first: only test files this turn", "bold red"),
    "deny": ("✘ Denied: Claude can look but not touch this turn", "bold red"),
}

def home(path):
    """path with the home directory shown as ~."""
    user = os.path.expanduser("~")
    return "~" + path[len(user):] if path.startswith(user) else path


def glyph(symbol, style_extra="", width=CELL):
    text, style = GLYPHS.get(symbol, GLYPHS[None])
    left = (width - cell_len(text)) // 2
    cell = Text(" " * left)
    cell.append(text, f"{style} {style_extra}".strip())
    cell.append(" " * (width - cell_len(text) - left))
    return cell


class Slot:
    """The reels in the permission prompt; rendered fresh on every refresh."""

    def __init__(self):
        self.reels = [None, None, None]   # each: list of 3 symbols, or None
        self.spinning = [False, False, False]
        self.line = None
        self.banner = None                # (text, style)
        self.note = None                  # italic line under the banner
        self.detail = None                # dim line under that
        self.started = None

    def spin(self):
        self.reels = [None, None, None]
        self.spinning = [True, True, True]
        self.line = self.banner = self.note = None
        self.started = time.monotonic()

    def stop(self, reel, symbols):
        self.spinning[reel - 1] = False
        self.reels[reel - 1] = symbols or [None, None, None]

    def settle(self, grid, line):
        self.spinning = [False, False, False]
        if grid:
            self.reels = grid
        self.line = line

    def _cell(self, reel, row, tick):
        if self.spinning[reel]:
            rng = random.Random(tick * 7 + reel * 3 + row)
            return glyph(rng.choice(SPIN_SYMBOLS), "dim")
        if self.reels[reel] is None:
            return Text(" " * CELL)
        cell = glyph(self.reels[reel][row])
        if self.line and LINES[self.line][reel] == row:
            cell.stylize(HIT)
        return cell

    def __rich__(self):
        tick = int(time.monotonic() * 14)
        busy = any(self.spinning)
        lights = Text("  ")
        for i in range(11):
            on = (i + tick) % 3 == 0 if busy else i % 2 == 0
            lights.append("● " if on else "○ ", GOLD if on else DIM)
        frame = f"bold {GOLD}"
        rows = [lights, Text("  ╔" + "╦".join(["═" * CELL] * 3) + "╗", frame)]
        for row in range(3):
            mark = self.line and self.line in ("top", "middle", "bottom") \
                and LINES[self.line][0] == row
            line = Text()
            line.append("▶ " if mark else "  ", f"bold {GOLD}")
            line.append("║", frame)
            for reel in range(3):
                line.append(self._cell(reel, row, tick))
                line.append("║", frame)
            line.append(" ◀" if mark else "", f"bold {GOLD}")
            rows.append(line)
        rows.append(Text("  ╚" + "╩".join(["═" * CELL] * 3) + "╝", frame))
        if self.banner:
            rows.append(Text("  " + self.banner[0], self.banner[1]))
            if self.note:
                rows.append(Text("  " + self.note, "italic"))
            if self.detail:
                rows.append(Text("  " + self.detail, DIM))
        elif busy:
            ticks = "".join("■ " if not s else "□ " for s in self.spinning)
            rows.append(Text(f"  Spinning… {ticks}", ACCENT))
        elif self.started is not None:  # all stopped, outcome on its way
            rows.append(Text("  Reading the reels…", ACCENT))
        else:
            blink = "bold" if tick % 14 < 9 else "bold dim"
            rows.append(Text("  ↓ Pull the lever!", f"{blink} {ACCENT}"))
        return Group(*rows)


class Thinking:
    def __init__(self):
        self.started = time.monotonic()
        self.verb = random.choice(VERBS)

    def __rich__(self):
        elapsed = time.monotonic() - self.started
        frame = SPINNER[int(elapsed * 8) % len(SPINNER)]
        text = Text(f"{frame} {self.verb}… ", ACCENT)
        text.append(f"({int(elapsed)}s · ctrl+c to interrupt)", DIM)
        return text


class TerminalUI:
    def __init__(self, cwd, model, machine_url):
        self.console = Console(highlight=False)
        self.cwd = cwd
        self.model = model
        self.machine_url = machine_url
        self.machine_up = False
        self.yolo_left = lambda: 0
        self.live = None
        self.thinking = None
        self.slot = None
        self.box = None
        self.outcome = None   # the latest spin's outcome and reason
        self.reason = None
        self.tools = {}       # tool_use_id -> (name, input)
        self.spins = 0        # spins for the current request
        self.spins_total = 0

    # ---- layout pieces

    def _stop_live(self):
        if self.live:
            self.live.stop()
            self.live = None

    def _show(self, renderable, refresh=12):
        self._stop_live()
        self.live = Live(renderable, console=self.console, transient=True,
                         refresh_per_second=refresh)
        self.live.start()

    def print(self, *things):
        if self.live:
            self.live.console.print(*things)
        else:
            self.console.print(*things)

    def welcome(self):
        lines = Text()
        lines.append("✻ ", ACCENT)
        lines.append("Welcome to ", "bold")
        lines.append("Pachiclaude", f"bold {ACCENT}")
        lines.append("!\n\n", "bold")
        lines.append("  /help for help, /status for the machine\n\n", DIM)
        lines.append(f"  cwd: {home(self.cwd)}\n", DIM)
        lines.append(f"  model: {self.model}\n", DIM)
        lines.append("  🎰 a pachislot machine decides what Claude may do", DIM)
        self.console.print(Panel(lines, box=box.ROUNDED, border_style=ACCENT,
                                 expand=False, padding=(0, 1)))
        self.console.print()

    def _hint(self):
        left = Text("  /help · ctrl+d to quit", DIM)
        right = Text()
        if self.yolo_left():
            right.append(f"★ YOLO ×{self.yolo_left()}  ", f"bold {GOLD}")
        right.append("● machine" if self.machine_up else "○ no machine",
                     "green" if self.machine_up else "yellow")
        pad = max(self.console.width - left.cell_len - right.cell_len - 1, 1)
        return Text.assemble(left, " " * pad, right)

    def read_prompt(self):
        """The boxed input; returns the text, or None on Ctrl-D."""
        if not sys.stdin.isatty() or not self.console.is_terminal:
            try:
                return input("> ")
            except EOFError:
                return None
        width = self.console.width
        border = DIM
        self.console.print(Text("╭" + "─" * (width - 2) + "╮", border))
        self.console.print(Text("│ > " + " " * (width - 5) + "│", border))
        self.console.print(Text("╰" + "─" * (width - 2) + "╯", border))
        self.console.print(self._hint())
        out = sys.stdout
        out.write("\x1b[3A\r\x1b[4C")  # back into the box, after "> "
        out.flush()
        try:
            text = input()
        except EOFError:
            out.write("\x1b[2B\r\n")
            return None
        # Replace the box with the prompt as it reads in the transcript.
        rows = 1 + (4 + cell_len(text)) // width
        out.write(f"\x1b[{rows + 1}A\r\x1b[J")
        out.flush()
        self.console.print(Text("> ", DIM) + Text(text, DIM))
        self.console.print()
        return text

    # ---- a turn

    def start_turn(self):
        self.thinking = Thinking()
        self._show(self.thinking, refresh=10)

    def end_turn(self):
        self._stop_live()
        self.thinking = None

    def _resume_thinking(self):
        if self.thinking:
            self._show(self.thinking, refresh=10)

    def bullet(self, renderable, dot_style="white"):
        grid = Table.grid(padding=(0, 1))
        grid.add_column(width=1, no_wrap=True)
        grid.add_column()
        grid.add_row(Text("⏺", dot_style), renderable)
        self.print(grid)

    def result(self, text, style=DIM):
        grid = Table.grid(padding=(0, 0))
        grid.add_column(width=5, no_wrap=True)
        grid.add_column()
        grid.add_row(Text("  ⎿  ", DIM), Text(text, style))
        self.print(grid)

    def assistant_text(self, text):
        self.bullet(Markdown(text))
        self.print()

    def tool_use(self, tool_id, name, tool_input):
        self.tools[tool_id] = (name, tool_input)
        summary = " ".join(describe(name, tool_input, self.cwd).split())
        line = Text("⏺ ", "green")
        line.append(name, "bold")
        line.append(f"({summary})")
        line.truncate(self.console.width - 1, overflow="ellipsis")
        self.print(line)

    def tool_result(self, tool_id, content, is_error):
        name, tool_input = self.tools.get(tool_id, ("", {}))
        if isinstance(content, list):
            content = "\n".join(c.get("text", "") for c in content
                                if isinstance(c, dict))
        content = (content or "").rstrip()
        if "pachislot machine" in content:
            self.result("🎰 Not this turn: "
                        + ("write tests first" if "watermelon" in content
                           else "the machine said no"), "red")
        elif is_error:
            self.result(_clip(content) or "Error", "red")
        elif name == "Write":
            lines = len(tool_input.get("content", "").splitlines())
            self.result(f"Wrote {lines} line{'s' if lines != 1 else ''} to "
                        f"{describe(name, tool_input, self.cwd)}")
        elif name in ("Edit", "MultiEdit"):
            self.result(f"Updated {describe(name, tool_input, self.cwd)}")
        elif name == "Read":
            self.result(f"Read {len(content.splitlines())} lines")
        else:
            self.result(_clip(content) or "(no output)")
        self.print()

    def done(self, turns, cost):
        cost = f" · ${cost:.4f}" if cost else ""
        self.print(Text(f"  {turns} turns{cost}", DIM))
        self.print()

    def interrupted(self):
        self.end_turn()
        self.result("Interrupted by user", "red")
        self.print()

    # ---- machine + permission prompt (called by gate.Gate)

    def status(self, text):
        self.machine_up = text.startswith("Connected")
        self.print(Text(f"  🎰 {text}", DIM))

    def request(self, prompt):
        """Open the box for a turn's spin."""
        self.spins = 0
        self.outcome = self.reason = None
        self.slot = Slot()
        self.box = _Prompt(prompt, self.slot)
        self._show(self.box, refresh=14)

    def event(self, event):
        if not self.slot:
            return
        kind = event["type"]
        if kind == "spin":
            self.spins += 1
            self.spins_total += 1
            self.slot.spin()
        elif kind == "stop":
            self.slot.stop(event["reel"], event.get("symbols"))
        elif kind == "outcome":
            self.slot.settle(event.get("grid"), event.get("line"))
            self.slot.banner = BANNERS.get(event["outcome"])
            self.outcome = event["outcome"]
            self.reason = pretty(event.get("reason") or "")
        elif kind == "false_spin":
            self.slot.spinning = [False, False, False]
            self.slot.banner = ("Too short: pull again", DIM)

    def decision(self, outcome, roast=None):
        """Leave the box in the transcript with the verdict; outcome None
        means the user interrupted the spin."""
        slot, box_, self.slot, self.box = self.slot, self.box, None, None
        self._stop_live()
        if slot is None:
            return
        slot.spinning = [False, False, False]
        if outcome is None:
            slot.banner = ("✘ Interrupted: prompt dropped", "bold red")
        else:
            slot.banner = VERDICTS[outcome]
            slot.note = f"“{roast}”" if roast else None
            details = [self.reason] if self.reason else []
            if self.spins > 1:
                details.append(f"{self.spins} spins")
            slot.detail = " · ".join(details) or None
        self.print(box_)
        self.print()

    def free_turn(self, left):
        self.print(Text(f"  🎰 ★ YOLO: approved without a spin "
                        f"({left} free prompt{'s' if left != 1 else ''} left)",
                        f"bold {GOLD}"))
        self.print()

    # ---- slash commands

    def help(self):
        self.print(Panel(Text.assemble(
            ("Pachiclaude", f"bold {ACCENT}"),
            " is Claude Code, but a pachislot machine decides what it may do.\n",
            "Pull the lever after each prompt; the spin decides that turn.\n\n",
            ("Reels on a line\n", "bold"),
            f"  7-7-7 / 祭-祭-祭   approve, and the next {YOLO_PROMPTS} prompts skip the spin\n",
            "  🔔 🔔 🔔          approve: Claude may write and run things\n",
            "  🍉 🍉 🍉          write tests first: only test files and test runs\n",
            "  ↺ ↺ ↺            spin again\n",
            "  🍒 on reel 1      approve, with a roast\n",
            "  anything else    deny: Claude can read and answer, nothing more\n\n",
            ("Commands\n", "bold"),
            "  /status  the machine, YOLO credits and spins\n",
            "  /clear   start a new conversation\n",
            "  /exit    quit (or ctrl+d)\n",
        ), box=box.ROUNDED, border_style=DIM, padding=(0, 1)))

    def show_status(self):
        up = ("● connected", "green") if self.machine_up else ("○ not connected", "yellow")
        self.print(Panel(Text.assemble(
            ("Machine   ", "bold"), up, f"  {self.machine_url}\n",
            ("YOLO      ", "bold"), f"{self.yolo_left()} free prompts left\n",
            ("Spins     ", "bold"), f"{self.spins_total} this session\n",
            ("Model     ", "bold"), f"{self.model}\n",
            ("cwd       ", "bold"), home(self.cwd),
        ), box=box.ROUNDED, border_style=DIM, padding=(0, 1)))


class _Prompt:
    """Claude Code's permission box, with the reels where the options go."""

    def __init__(self, prompt, slot):
        self.slot = slot
        self.preview = Text("  " + _clip(prompt.strip(), 6).replace("\n", "\n  "))

    def __rich__(self):
        title = Text("Before Claude starts", f"bold {PERMISSION}")
        body = Group(title, Text(), self.preview, Text(),
                     Text("What may Claude do this turn? Let the machine decide.",
                          "bold"),
                     self.slot)
        return Panel(body, box=box.ROUNDED, border_style=PERMISSION,
                     padding=(0, 1))


def _clip(text, lines=3):
    rows = text.splitlines()
    if len(rows) <= lines:
        return text
    return "\n".join(rows[:lines]) + f"\n… +{len(rows) - lines} lines"
