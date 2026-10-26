import base64
import json

from re_gpt.proof_of_work import fnv_murmur3, solve_proof_of_work
from re_gpt.cdp_sentinel import find_chatgpt_tab_ws_url


def test_fnv_murmur3_known_vector():
    # Known hash for "abc" verified against browser JavaScript engine
    assert fnv_murmur3("abc") == "1cc93dbc"


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
