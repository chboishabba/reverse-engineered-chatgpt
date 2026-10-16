from pathlib import Path


def test_setup_pins_runtime_versions_used_by_supported_venv() -> None:
    setup_text = (Path(__file__).resolve().parents[1] / "setup.py").read_text(encoding="utf-8")
    assert 'curl_cffi==0.14.0' in setup_text
    assert 'websockets==16.0' in setup_text
    assert 'curl_cffi==0.5.9' not in setup_text
