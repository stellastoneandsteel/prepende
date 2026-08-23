"""The goal-centric TUI (Textual) — Prepende's product surface.

Design intent (the calm, premium, dark-terminal aesthetic):
  - Near-black canvas, warm off-white type, a single amber accent (the Prepende
    singularity). Monospace, quiet, confident — never busy.
  - CALM BY DEFAULT: the conversation is the focus. The machinery (which tactic,
    memory recall, tool calls) lives in a dim side panel you can hide (ctrl+b) —
    depth on demand, never in your face. Respects technical and non-technical
    users alike.
  - Layout: a slim masthead, the conversation (left, dominant), a collapsible
    "brain" panel (right: model · memory · knowledge · what it's doing now), and
    one dominant input bar docked at the bottom.

Requires `textual` (optional dep). tui.__main__ falls back to the plain REPL if
it isn't installed, so Prepende always runs.
"""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Input, RichLog, Static

from kernel.core.brain import build_brain

# --- palette (the brand) -----------------------------------------------------
BG = "#08090b"       # near-black canvas
PANEL = "#0c0e12"    # raised surface
INK = "#ece6d8"      # warm off-white type
DIM = "#6b7280"      # quiet/secondary
AMBER = "#e8a13a"    # the singularity accent
LINE = "#1b1f27"     # hairline rules


class PrependeApp(App):
    CSS = f"""
    Screen {{ background: {BG}; color: {INK}; layers: base; }}

    #masthead {{
        dock: top; height: 1; padding: 0 2;
        background: {PANEL}; color: {INK};
        border-bottom: solid {LINE};
    }}

    #body {{ height: 1fr; }}

    #conversation {{
        width: 2fr; padding: 1 2; background: {BG};
    }}
    #stream {{ height: 1fr; background: {BG}; color: {INK}; }}

    #brain {{
        width: 38; padding: 1 2; background: {PANEL};
        border-left: solid {LINE}; color: {DIM};
    }}
    #brain.-hidden {{ display: none; }}
    .brain-title {{ color: {AMBER}; text-style: bold; }}
    .brain-row {{ color: {DIM}; }}

    #composer {{
        dock: bottom; height: 3; padding: 0 1;
        background: {PANEL}; border-top: solid {LINE};
    }}
    #goal {{
        background: {PANEL}; color: {INK};
        border: none; padding: 1 1;
    }}
    #goal:focus {{ border: none; }}
    """

    TITLE = "Prepende"
    SUB_TITLE = "every session leaves a trace"

    BINDINGS = [
        Binding("ctrl+b", "toggle_brain", "brain panel"),
        Binding("ctrl+l", "toggle_verbose", "verbose"),
        Binding("ctrl+c", "quit", "quit"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.loop, self.cfg, self.gateway = build_brain(memory_policy="auto")  # interactive dev surface: auto memory writes
        self._answer = ""
        self._verbose = False
        self._busy = False  # a goal is in flight; don't let a new one cancel it
        self._history: list[dict] = []  # the live conversation (last turns), so follow-ups work

    # --- layout --------------------------------------------------------------
    def compose(self) -> ComposeResult:
        model = getattr(self.gateway, "name", "?")
        yield Static(f"[{AMBER}]✦[/] [{INK}]Prepende[/]  [{DIM}]· the AI that learns you and helps you achieve your goals[/]", id="masthead")
        with Horizontal(id="body"):
            with Vertical(id="conversation"):
                yield RichLog(id="stream", wrap=True, markup=True, highlight=False)
            with VerticalScroll(id="brain"):
                yield Static("✦ brain", classes="brain-title")
                yield Static("", id="brain-body", classes="brain-row")
        with Vertical(id="composer"):
            yield Input(placeholder=f"state a goal…   (model: {model} · ctrl+b panel · ctrl+l verbose)", id="goal")

    async def on_mount(self) -> None:
        log = self.query_one("#stream", RichLog)
        log.write(f"[{AMBER}]✦[/] [{INK}]Welcome.[/] [#6b7280]Type a goal and press enter. It remembers across sessions.[/]")
        self.query_one("#goal", Input).focus()
        await self._refresh_brain()

    # --- actions -------------------------------------------------------------
    def action_toggle_brain(self) -> None:
        self.query_one("#brain").toggle_class("-hidden")

    def action_toggle_verbose(self) -> None:
        self._verbose = not self._verbose
        self.query_one("#stream", RichLog).write(
            f"[#6b7280]· {'verbose — showing how it thinks' if self._verbose else 'quiet — just the answer'}[/]"
        )

    # --- the turn ------------------------------------------------------------
    async def on_input_submitted(self, message: Input.Submitted) -> None:
        goal = message.value.strip()
        if not goal:
            return
        log = self.query_one("#stream", RichLog)
        # Don't let a new goal cancel one already in flight — that read as "frozen".
        if self._busy:
            log.write(f"[{DIM}]· still working on the last goal — one at a time[/]")
            return
        self.query_one("#goal", Input).value = ""
        log.write("")
        log.write(f"[{DIM}]────────────────────────────────────────[/]")
        log.write(f"[{AMBER}]you ›[/] [{INK}]{goal}[/]")
        log.write("")
        self._answer = ""
        self.run_worker(self._pursue(goal), exclusive=False)

    async def _pursue(self, goal: str) -> None:
        self._busy = True
        log = self.query_one("#stream", RichLog)
        spinner = self.query_one("#masthead", Static)
        spinner.update(f"[{AMBER}]✦[/] [{INK}]Prepende[/]  [{DIM}]· thinking…[/]")
        # Immediate, visible feedback so it never looks frozen during long runs.
        log.write(f"[{AMBER}]prepende ›[/] [{DIM}]thinking…[/]")

        async def on_event(ev: dict) -> None:
            t = ev["type"]
            if t == "status":
                # Always show a brief activity pulse so multi-step goals show progress;
                # full detail only in verbose.
                if self._verbose:
                    log.write(f"[#6b7280]· {ev['text']}[/]")
                elif ev["text"].startswith(("strategist", "step", "plan", "council", "exploring")):
                    log.write(f"[#6b7280]· {ev['text']}[/]")
            elif t == "token":
                self._answer += ev["text"]  # rendered as one block on done (RichLog is append-only)
            elif t == "artifact":
                if self._verbose:
                    log.write(f"[#6b7280]✎ {ev['text']}[/]")
            elif t == "error":
                log.write(f"[#d9665b]✗ {ev['text']}[/]")
            elif t == "done":
                log.write("")
                log.write(f"[{INK}]{self._answer.strip()}[/]")
                # record the turn so the next message has conversational context
                self._history.append({"role": "user", "content": goal})
                self._history.append({"role": "assistant", "content": self._answer.strip()})
                self._history = self._history[-12:]  # keep the last ~6 exchanges

        try:
            await self.loop.run(goal, on_event, history=self._history)
        except Exception as exc:
            log.write(f"[#d9665b]✗ {type(exc).__name__}: {exc}[/]")
        finally:
            self._busy = False
            spinner.update(f"[{AMBER}]✦[/] [{INK}]Prepende[/]  [{DIM}]· the AI that learns you and helps you achieve your goals[/]")
            await self._refresh_brain()

    async def _refresh_brain(self) -> None:
        try:
            from kernel.core.introspect import brain_state
            st = await brain_state(self.loop, self.cfg.memory_scope)
        except Exception:
            return
        mem = st.get("memory", {}) or {}
        kn = st.get("knowledge", {}) or {}
        cn = st.get("connectors", {}) or {}
        rn = st.get("runs", {}) or {}
        lines = [
            f"[{DIM}]model[/]      [{INK}]{st.get('model','?')}[/]",
            f"[{DIM}]memory[/]     [{INK}]{mem.get('recent_count',0)}[/] [{DIM}]recent · {mem.get('backend','?')}[/]",
            f"[{DIM}]knowledge[/]  [{INK}]{kn.get('pages',0)}[/] [{DIM}]wiki pages[/]",
            f"[{DIM}]goals[/]      [{INK}]{rn.get('recent_count',0)}[/] [{DIM}]recent[/]",
            f"[{DIM}]connectors[/] [{INK}]{cn.get('ready',0)}/{cn.get('tools',0)}[/] [{DIM}]ready[/]",
        ]
        recents = mem.get("recent", [])[:3]
        if recents:
            lines.append("")
            lines.append(f"[{DIM}]remembers[/]")
            for r in recents:
                lines.append(f"[{DIM}]· {r[:46]}[/]")
        self.query_one("#brain-body", Static).update("\n".join(lines))


def run() -> None:
    PrependeApp().run()


# Import compatibility for extensions that referenced the old class name.
EngramApp = PrependeApp


if __name__ == "__main__":
    run()
