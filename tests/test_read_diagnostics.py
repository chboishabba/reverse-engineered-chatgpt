from __future__ import annotations

import json

from re_gpt.read_diagnostics import redacted_request_shape


def test_redacted_request_shape_keeps_names_not_values() -> None:
    receipt = redacted_request_shape(
        adapter="sync",
        method="GET",
        route="/backend-api/conversation/<id>",
        headers={
            "Authorization": "Bearer TOP-SECRET",
            "Accept": "application/json",
            "OAI-Device-Id": "device-secret",
        },
        cookie_names=["__Secure-next-auth.session-token", "cf_clearance"],
        cookie_domains=["chatgpt.com", ".chatgpt.com"],
        status=429,
        elapsed_ms=12.5,
        response_class="http_error",
    )
    rendered = json.dumps(receipt)
    assert receipt["header_names"] == ["Accept", "Authorization", "OAI-Device-Id"]
    assert receipt["auth_header_present"] is True
    assert receipt["device_id_present"] is True
    assert "TOP-SECRET" not in rendered
    assert "device-secret" not in rendered
    assert receipt["cookie_names"] == ["__Secure-next-auth.session-token", "cf_clearance"]
