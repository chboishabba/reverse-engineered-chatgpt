"""Pure, source-preserving terminal segmentation of Markdown mathematics.

Produces display hints only. Never mutates canonical conversation content.
"""
from dataclasses import dataclass


_COMMAND_SYMBOLS = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ",
    "epsilon": "ε", "theta": "θ", "lambda": "λ", "mu": "μ",
    "pi": "π", "sigma": "σ", "phi": "φ", "omega": "ω",
    "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ",
    "Pi": "Π", "Sigma": "Σ", "Phi": "Φ", "Omega": "Ω",
    "infty": "∞", "le": "≤", "leq": "≤", "ge": "≥", "geq": "≥",
    "ne": "≠", "approx": "≈", "equiv": "≡", "cdot": "·",
    "times": "×", "pm": "±", "rightarrow": "→", "to": "→",
    "sum": "∑", "prod": "∏",
}

_SUPERSCRIPTS = str.maketrans("0123456789+-=()nilmst", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿⁱˡᵐˢᵗ")
_SUBSCRIPTS = str.maketrans("0123456789+-=aehijklmnoprstuvx", "₀₁₂₃₄₅₆₇₈₉₊₋₌ₐₑₕᵢⱼₖₗₘₙₒₚᵣₛₜᵤᵥₓ")


class _UnsafeMath(ValueError):
    """The small pretty-printer does not claim to understand this expression."""


def _group(source: str, index: int) -> tuple[str, int]:
    if index >= len(source) or source[index] != "{":
        raise _UnsafeMath
    value, end = _parse_sequence(source, index + 1, "}")
    return value, end + 1


def _atom(source: str, index: int) -> tuple[str, int]:
    if index >= len(source):
        raise _UnsafeMath
    if source[index] == "{":
        return _group(source, index)
    if source[index] == "\\":
        return _command(source, index)
    if source[index] in "{}^_":
        raise _UnsafeMath
    return source[index], index + 1


def _command(source: str, index: int) -> tuple[str, int]:
    end = index + 1
    while end < len(source) and source[end].isalpha():
        end += 1
    name = source[index + 1:end]
    if name == "frac":
        numerator, end = _group(source, end)
        denominator, end = _group(source, end)
        return f"{numerator} ⁄ {denominator}", end
    if name == "sqrt":
        radicand, end = _group(source, end)
        return f"√{radicand}", end
    if name in _COMMAND_SYMBOLS:
        return _COMMAND_SYMBOLS[name], end
    raise _UnsafeMath


def _parse_sequence(source: str, index: int = 0, closing: str | None = None) -> tuple[str, int]:
    output = []
    while index < len(source):
        char = source[index]
        if closing and char == closing:
            return "".join(output), index
        if char == "\\":
            value, index = _command(source, index)
            output.append(value)
            continue
        if char in "^_":
            value, index = _atom(source, index + 1)
            value = value.strip()
            if not value or any(c.isspace() for c in value):
                raise _UnsafeMath
            translated = value.translate(_SUPERSCRIPTS if char == "^" else _SUBSCRIPTS)
            if translated == value and len(value) != 1:
                raise _UnsafeMath
            output.append(translated)
            continue
        if char in "{}":
            raise _UnsafeMath
        output.append(char)
        index += 1
    if closing:
        raise _UnsafeMath
    return "".join(output), index


def render_math_accessibly(source: str) -> str:
    """Return a deterministic Unicode rendering, or the source on uncertainty."""
    body = source.strip()
    for opener, closer in (("$$", "$$"), (r"\[", r"\]"), (r"\(", r"\)"), ("$", "$")):
        if body.startswith(opener) and body.endswith(closer):
            body = body[len(opener):-len(closer)]
            break
    if not body:
        return "empty mathematical expression"
    try:
        rendered, end = _parse_sequence(body)
        if end != len(body):
            raise _UnsafeMath
    except _UnsafeMath:
        return body
    return " ".join(rendered.split()) or "empty mathematical expression"


def render_terminal_accessibly(source: str) -> str:
    """Render common math as accessible terminal text while preserving source elsewhere."""
    rendered = []
    for part in split_math(source):
        if part.kind == "math":
            prefix = "display math: " if part.display else "math: "
            rendered.append(prefix + render_math_accessibly(part.source))
        else:
            rendered.append(part.source)
    return "".join(rendered)


@dataclass(frozen=True)
class Segment:
    kind: str
    source: str
    start: int
    end: int
    display: bool = False


def split_math(source: str) -> tuple[Segment, ...]:
    """Locate complete TeX delimiters; leave code and unfinished input intact.

    Source offsets use Python string indexes (not UTF-8 byte indexes).
    No TeX evaluation, IO, DOM, or graphics dependencies.
    """
    results = []
    i = last = 0
    n = len(source)

    def add(kind, a, b, display=False):
        if b > a:
            results.append(Segment(kind, source[a:b], a, b, display))

    while i < n:
        if i == 0 or source[i - 1] == "\n":
            stripped = source[i:].lstrip(" ")
            indent = len(source[i:]) - len(stripped)
            if indent <= 3 and stripped.startswith(("~~~", "```")):
                # A fenced code block is opaque, including contained math.
                fence = stripped[:3]
                beginning = i
                start = source.find("\n", i)
                end = n
                if start >= 0:
                    k = start + 1
                    while k < n:
                        line_end = source.find("\n", k)
                        line_end = n if line_end < 0 else line_end + 1
                        if source[k:line_end].lstrip(" ").startswith(fence):
                            end = line_end
                            break
                        k = line_end
                add("text", last, beginning)
                add("code", beginning, end)
                i = last = end
                continue
        if source[i] == "`":
            end = source.find("`", i + 1)
            if end >= 0:
                add("text", last, i)
                add("code", i, end + 1)
                i = last = end + 1
                continue
        if i and source[i-1] == "\\" and source[i] not in "[(":
            i += 1
            continue
        candidate = None
        for opener, closer, display in (("$$", "$$", True), (r"\[", r"\]", True),
                                        (r"\(", r"\)", False), ("$", "$", False)):
            if source.startswith(opener, i):
                candidate = (opener, closer, display)
                break
        if candidate is not None:
            opener, closer, display = candidate
            k = i + len(opener)
            while True:
                end = source.find(closer, k)
                if end < 0:
                    break
                if end and source[end - 1] == "\\":
                    k = end + len(closer)
                    continue
                if opener == "$" and (end == i + 1 or "\n" in source[i + 1:end] or
                                       (end + 1 < n and source[end + 1] == "$")):
                    k = end + 1
                    continue
                add("text", last, i)
                stop = end + len(closer)
                add("math", i, stop, display)
                i = last = stop
                break
            else:
                pass
            if end >= 0 and i == end + len(closer):
                continue
        i += 1
    add("text", last, n)
    return tuple(results)


def recover_source(segments: tuple[Segment, ...]) -> str:
    """Fail closed on discontinuous projection coordinates."""
    out = []
    offset = 0
    for item in segments:
        if item.start != offset or item.end - item.start != len(item.source):
            raise ValueError("non-contiguous source projection")
        offset = item.end
        out.append(item.source)
    return "".join(out)
