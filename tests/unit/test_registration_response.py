import json

import pytest
import requests

from nodes import api


@pytest.fixture(autouse=True)
def no_external_ip_probe(monkeypatch):
    monkeypatch.setattr(api, "report_registration_egress", lambda session: None)


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"error": "email already exists"}, "email already exists"),
        ({"errors": {"email": ["address is not accepted"]}}, "address is not accepted"),
        (None, "未返回可用的 JSON 错误详情"),
    ],
)
def test_register_surfaces_rejection_reason_and_closes_failed_session(
    monkeypatch, payload, expected
):
    response = requests.Response()
    response.status_code = 400
    response._content = json.dumps(payload).encode() if payload else b"<html>bad request</html>"
    session = requests.Session()
    closed = []
    monkeypatch.setattr(session, "post", lambda *a, **kw: response)
    monkeypatch.setattr(session, "close", lambda: closed.append(True))
    monkeypatch.setattr(api.requests, "Session", lambda: session)
    with pytest.raises(RuntimeError, match=expected):
        api.register("fixture@gmail.com", "secret-password", "fixture-turnstile")
    assert closed == [True]


def test_register_error_does_not_echo_credentials(monkeypatch):
    response = requests.Response()
    response.status_code = 400
    response._content = json.dumps(
        {
            "message": "fixture@gmail.com secret-password fixture-turnstile",
            "access_token": "private-response-token",
        }
    ).encode()
    session = requests.Session()
    monkeypatch.setattr(session, "post", lambda *a, **kw: response)
    monkeypatch.setattr(api.requests, "Session", lambda: session)
    with pytest.raises(RuntimeError) as error:
        api.register("fixture@gmail.com", "secret-password", "fixture-turnstile")
    for secret in (
        "fixture@gmail.com",
        "secret-password",
        "fixture-turnstile",
        "private-response-token",
    ):
        assert secret not in str(error.value)
    assert "HTTP 400" in str(error.value)
