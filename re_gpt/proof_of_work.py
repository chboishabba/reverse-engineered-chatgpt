"""Pure Python solver for OpenAI Sentinel Proof-of-Work challenge.

Implements the FNV-1a (32-bit) with MurmurHash3/SplitMix finalizer hash loop
and telemetry payload generation matching OpenAI frontend attestation.
"""

from __future__ import annotations

import base64
import json
import random
import time
from typing import Any


def fnv_murmur3(s: str) -> str:
    """Compute OpenAI Sentinel 32-bit FNV-1a + MurmurHash3 finalizer hash."""
    t = 0x811C9DC5
    for ch in s:
        t ^= ord(ch)
        t = (t * 0x1000193) & 0xFFFFFFFF
    t ^= (t >> 16)
    t = (t * 0x85EBCA6B) & 0xFFFFFFFF
    t ^= (t >> 13)
    t = (t * 0xC2B2AE35) & 0xFFFFFFFF
    t ^= (t >> 16)
    t &= 0xFFFFFFFF
    return f"{t:08x}"


def generate_telemetry_array(user_agent: str | None = None) -> list[Any]:
    """Generate baseline 25-element telemetry vector matching frontend runtime."""
    ua = user_agent or "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    return [
        3000,  # 0: screen.width + screen.height (e.g. 1920 + 1080)
        time.strftime("%a %b %d %Y %H:%M:%S GMT+0000 (Coordinated Universal Time)", time.gmtime()),
        2147483648,  # 2: heap size limit
        random.random(),  # 3: iteration counter r
        ua,  # 4: user agent
        "https://chatgpt.com/cdn/assets/manifest-67419903.js",  # 5: script src
        "c/prod/_",  # 6: data build
        "en-US",  # 7: language
        "en-US,en",  # 8: languages
        random.random(),  # 9: elapsed ms
        "webdriver−false",  # 10: navigator prop
        "location,cookie",  # 11: doc keys
        "window,document",  # 12: win keys
        time.time() * 1000.0,  # 13: perf now
        0,  # 14
        "",  # 15: search params
        8,  # 16: hardware concurrency
        time.time() * 1000.0,  # 17: time origin
        0,  # 18: ai in window
        0,  # 19: createPRNG
        0,  # 20: cache
        0,  # 21: data
        0,  # 22: solana
        0,  # 23: dump
        0,  # 24: InstallTrigger
    ]


def solve_proof_of_work(
    seed: str,
    difficulty: str,
    *,
    user_agent: str | None = None,
    max_iterations: int = 500000,
) -> str:
    """Solve the proof-of-work challenge and return the gAAAAAB formatted token."""
    t0 = time.perf_counter()
    telemetry = generate_telemetry_array(user_agent=user_agent)
    diff_len = len(difficulty)

    for r in range(max_iterations):
        telemetry[3] = r
        telemetry[9] = round((time.perf_counter() - t0) * 1000)
        raw_json = json.dumps(telemetry, separators=(",", ":"))
        encoded = base64.b64encode(raw_json.encode("utf-8")).decode("ascii")
        candidate = f"{seed}{encoded}"
        hash_hex = fnv_murmur3(candidate)
        if hash_hex[:diff_len] <= difficulty:
            return f"gAAAAAB{encoded}~S"

    err_b64 = base64.b64encode(b'"e"').decode("ascii")
    return f"wQ8Lk5FbGpA2NcR9dShT6gYjU7VxZ4D{err_b64}"
