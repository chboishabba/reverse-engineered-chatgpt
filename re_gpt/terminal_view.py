"""Read-only terminal projection entry point; no authentication or database.

Examples:
  re-gpt-terminal < transcript.md
  re-gpt-terminal --format segments < transcript.md
  re-gpt-terminal --format math-source < transcript.md
The protocol for graphics integration is separate from source identity.
"""
from __future__ import annotations

import argparse
import json
import sys

from .terminal_projection import split_math, recover_source


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", choices=("source", "segments", "math-source"),
                        default="source")
    args = parser.parse_args(argv)
    source = sys.stdin.read()
    parts = split_math(source)
    if recover_source(parts) != source:
        raise RuntimeError("terminal projection violated exact source preservation")
    if args.format == "source":
        sys.stdout.write(source)
    elif args.format == "math-source":
        for p in parts:
            if p.kind == "math":
                sys.stdout.write(p.source + "\n")
    else:
        for p in parts:
            print(json.dumps({"kind": p.kind, "source": p.source, "start": p.start,
                              "end": p.end, "display": p.display},
                             ensure_ascii=False))


if __name__ == "__main__":
    main()
