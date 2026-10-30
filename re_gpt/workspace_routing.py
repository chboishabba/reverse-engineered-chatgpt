"""Explicit conversation route parser; provider mentions are never sent as prose."""
from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class Route:
    targets: tuple[str,...]
    prompt: str
    explicit: bool = False

def parse_route(prompt: str) -> Route:
    if not isinstance(prompt,str) or not prompt.strip():
        raise ValueError("empty prompt")
    text=prompt.strip()
    head,sep,body=text.partition(" ")
    targets={
        "@chatgpt":("chatgpt",),
        "@local":("local",),
        "@both":("chatgpt","local"),
        "@ao":("ao",),
    }
    if head.casefold() not in targets:
        return Route(("chatgpt",),text,False)
    if not body.strip():
        raise ValueError("route requires a nonempty task")
    return Route(targets[head.casefold()],body.strip(),True)
