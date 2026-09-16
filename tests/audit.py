"""Run the regression suite; missing optional tools are reported as skips."""

import subprocess
import sys
from pathlib import Path


def main():
    raise SystemExit(subprocess.call([sys.executable, "-m", "pytest", "-ra"], cwd=Path(__file__).resolve().parents[1]))


if __name__ == "__main__":
    main()
