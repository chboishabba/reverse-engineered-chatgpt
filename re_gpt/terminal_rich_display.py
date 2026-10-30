"""Presentation-only terminal rich text. Canonical PG keeps exact source."""
from __future__ import annotations
import html
import re
from .terminal_math_layout import unicode_math

CITE = re.compile(r"<Cite\b[^>]*?/>", re.I)
LINK = re.compile(r'<Link\b[^>]*?\burl="([^"]+)"[^>]*>(.*?)</Link>', re.I | re.S)
MATH = re.compile(r"\\\[(.*?)\\\]|\\\((.*?)\\\)", re.S)
COMMANDS = {r"\qquad": "    ", r"\quad": "  ", r"\cdot": "·",
            r"\times": "×", r"\equiv": "≡", r"\leq": "≤",
            r"\geq": "≥", r"\infty": "∞", r"\to": "→",
            r"\lambda": "λ", r"\alpha": "α", r"\beta": "β"}

def _math_unicode(source: str) -> str:
    result = source.strip()
    for command in sorted(COMMANDS, key=len, reverse=True):
        result = result.replace(command, COMMANDS[command])
    result = re.sub(r"\\(?:begin|end)\{(?:aligned|align\*?|gathered)\}", "", result)
    return unicode_math(result)

def terminal_display(source: str) -> str:
    """Improve Rich's display of MDX/math; never mutate persisted source.

    Unknown macros remain visible. This is a Unicode fallback, not TeX typesetting.
    """
    if not source:
        return ""
    fence = chr(96) * 3
    fragments = re.split("(" + re.escape(fence) + r"[\s\S]*?" + re.escape(fence) + ")", source)
    for idx in range(0, len(fragments), 2):
        body = html.unescape(fragments[idx])
        body = LINK.sub(lambda m: "[" + m.group(2).strip() + "](" + html.unescape(m.group(1)) + ")", body)
        body = CITE.sub(" [source citation]", body)
        fragments[idx] = MATH.sub(lambda m: ("\n" + _math_unicode(m.group(1)) + "\n")
            if m.group(1) is not None else _math_unicode(m.group(2)), body)
    return "".join(fragments)
