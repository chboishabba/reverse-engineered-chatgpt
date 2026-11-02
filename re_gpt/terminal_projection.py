"""Pure, source-preserving terminal segmentation of Markdown mathematics.

Produces display hints only. Never mutates canonical conversation content.
"""
from dataclasses import dataclass


_COMMAND_WORDS = {
    r"\alpha": "alpha", r"\beta": "beta", r"\gamma": "gamma",
    r"\lambda": "lambda", r"\infty": "infinity", r"\le": "less than or equal to",
    r"\ge": "greater than or equal to", r"\cdot": " times ",
    r"\times": " times ", r"\pm": " plus or minus ", r"\sum": "sum",
    r"\prod": "product", r"\sqrt": "square root of ",
}


def render_math_accessibly(source: str) -> str:
    """Return a deterministic spoken-style rendering without changing source."""
    body = source.strip()
    for delimiter in ("$$", r"\[", r"\]", r"\(", r"\)", "$"):
        body = body.replace(delimiter, "")
    body = body.replace(r"\frac", " fraction ")
    body = body.replace("{", " of ").replace("}", "")
    for command, word in _COMMAND_WORDS.items():
        body = body.replace(command, word)
    body = body.replace("^", " to the power of ").replace("_", " subscript ")
    body = " ".join(body.split())
    return body or "empty mathematical expression"


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
