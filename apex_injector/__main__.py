"""
Apex Meta-Injector — Entry point.

Usage:
    python -m apex_injector              # Launch GUI (default)
    python -m apex_injector --cli ...    # CLI mode
    python -m apex_injector --gui        # Explicit GUI mode
"""

import sys
import argparse


def main():
    """Main entry point — routes to CLI or GUI based on arguments."""
    parser = argparse.ArgumentParser(
        prog="apex-injector",
        description="Apex Meta-Injector: High-speed batch metadata injection for media containers.",
        add_help=False,
    )
    parser.add_argument("--gui", action="store_true", default=False, help="Launch the desktop GUI")
    parser.add_argument("--cli", action="store_true", default=False, help="Use CLI mode")

    # Parse only known args to avoid conflicts with subcommand parsers
    args, remaining = parser.parse_known_args()

    if args.cli:
        from apex_injector.cli import cli_main
        cli_main(remaining)
    else:
        main_gui()


def main_gui():
    """Launch the desktop GUI application."""
    from apex_injector.gui import launch_gui
    launch_gui()


if __name__ == "__main__":
    main()
