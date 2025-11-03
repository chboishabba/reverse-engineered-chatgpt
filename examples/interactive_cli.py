"""Interactive command line interface for ChatGPT sessions.

This module simply re-exports :func:`re_gpt.cli.main` so that the example
remains usable while the real implementation lives within the package.  Refer
to :mod:`re_gpt.cli` for the full source code and documentation.
"""

from __future__ import annotations

import sys

from re_gpt.cli import main


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted. Goodbye!")
        sys.exit(1)
