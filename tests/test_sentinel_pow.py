import base64
import json

from re_gpt.proof_of_work import (
    build_requirements_token,
    fnv_murmur3,
    solve_proof_of_work,
    solve_turnstile_dx,
    xor_strings,
)
from re_gpt.cdp_sentinel import find_chatgpt_tab_ws_url


def test_fnv_murmur3_known_vector():
    # Known hash for "abc" verified against browser JavaScript engine
    assert fnv_murmur3("abc") == "1cc93dbc"


def test_build_requirements_token_and_headless_dx_vm():
    req_key = build_requirements_token()
    assert req_key.startswith("gAAAAAC")
    # Build a synthetic 2-instruction dx program:
    # [2, 50, "ok-vm"], [7, 3, 50] -> sets register 50 to "ok-vm" and calls opcode 3 with r.get(50)
    prog_json = json.dumps([[2, 50, "ok-vm"], [7, 3, 50]])
    xored = xor_strings(prog_json, req_key)
    dx = base64.b64encode(xored.encode("latin1")).decode("ascii")
    tok = solve_turnstile_dx(dx, req_key)
    assert base64.b64decode(tok).decode("ascii") == "ok-vm"



def test_solve_proof_of_work():
    seed = "test-seed-12345"
    # Easy difficulty so test finishes in <10ms
    difficulty = "ffffff"
    token = solve_proof_of_work(seed, difficulty, max_iterations=100)
    assert token.startswith("gAAAAAB")
    assert token.endswith("~S")

    # Verify the payload structure
    inner_b64 = token[len("gAAAAAB"):-len("~S")]
    raw_json = base64.b64decode(inner_b64).decode("utf-8")
    telemetry = json.loads(raw_json)
    assert isinstance(telemetry, list)
    assert len(telemetry) == 25
    assert isinstance(telemetry[3], int)


def test_find_chatgpt_tab_ws_url():
    # Tests non-crashing behavior when CDP endpoint is reachable or not
    url = find_chatgpt_tab_ws_url("http://127.0.0.1:9222")
    # If Chrome is running with ChatGPT, it should return a valid ws URL
    if url is not None:
        assert url.startswith("ws://")
        assert "devtools/page" in url
