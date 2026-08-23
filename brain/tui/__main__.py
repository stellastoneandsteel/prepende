"""Entrypoint: `python -m tui`.

Prefer the Textual TUI; fall back to the plain stdlib REPL if Textual isn't
installed (or fails to start), so Prepende always runs.
"""

from __future__ import annotations


def main() -> None:
    try:
        import textual  # noqa: F401
        have_textual = True
    except ImportError:
        have_textual = False

    if have_textual:
        try:
            from tui.app import run as run_app
            run_app()
            return
        except Exception as exc:  # fall back rather than die
            print(f"(TUI failed to start: {exc}\n falling back to the plain REPL)\n")

    from tui.repl import run as run_repl
    if not have_textual:
        print("(textual not installed — using the plain REPL. `pip install textual` for the full TUI.)")
    run_repl()


if __name__ == "__main__":
    main()
