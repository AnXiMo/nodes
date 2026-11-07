import json

import pytest
import requests

from nodes import api


def response(body, status=200):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(body).encode()
    result._content_consumed = True
    return result


@pytest.mark.parametrize("address", ["8.8.4.4", "2606:4700:4700::1111"])
def test_reports_public_ip_and_releases_response(monkeypatch, capsys, address):
    session = requests.Session()
    reply = response({"ip": address})
    closed = []

    def get(url, **kwargs):
        assert url == "https://api.ipify.org?format=json"
        assert kwargs == {"timeout": (3, 5), "allow_redirects": False}
        return reply

    monkeypatch.setattr(session, "get", get)
    monkeypatch.setattr(reply, "close", lambda: closed.append(True))
    assert api.report_registration_egress(session) == address
    output = capsys.readouterr().out
    assert address in output and "不代表注册服务观测值" in output
    assert closed == [True]


@pytest.mark.parametrize("body,status", [
    ({"ip": "8.8.4.4"}, 302),
    ({"ip": "8.8.4.4"}, 429),
    ({"ip": "127.0.0.1"}, 200),
    ({"ip": "10.0.0.1"}, 200),
    ({"ip": "fe80::1%eth0"}, 200),
    ({"ip": "observer-secret"}, 200),
    ({"ip": 42}, 200),
    ({}, 200),
    ([], 200),
])
def test_invalid_observation_is_unknown_and_does_not_echo_body(
    monkeypatch, capsys, body, status,
):
    session = requests.Session()
    monkeypatch.setattr(session, "get", lambda *a, **kw: response(body, status))
    assert api.report_registration_egress(session) is None
    output = capsys.readouterr().out
    assert "未知" in output and "observer-secret" not in output


def test_probe_failure_does_not_leak_proxy_credentials_or_block_registration(monkeypatch, capsys):
    session = requests.Session()
    calls = []

    def get(*args, **kwargs):
        calls.append("probe")
        raise requests.exceptions.ProxyError("http://user:private-password@proxy.example.invalid")

    def post(*args, **kwargs):
        calls.append("register")
        return response({"access_token": "fixture-token", "userData": {}})

    monkeypatch.setattr(session, "get", get)
    monkeypatch.setattr(session, "post", post)
    monkeypatch.setattr(api.requests, "Session", lambda: session)
    returned, token, _ = api.register("fixture@example.invalid", "password", "challenge")
    assert returned is session and token == "fixture-token"
    assert calls == ["probe", "register"]
    output = capsys.readouterr().out
    assert "未知" in output and "注册响应 HTTP 200" in output
    assert "private-password" not in output and "proxy.example.invalid" not in output


def test_probe_uses_registration_session_and_logs_repeated_observations(monkeypatch, capsys):
    session = requests.Session()
    calls = []

    def get(*args, **kwargs):
        calls.append("probe")
        return response({"ip": "8.8.4.4"})

    def post(url, **kwargs):
        assert url == api.PS_REGISTER
        calls.append("register")
        return response({"access_token": "fixture-token", "userData": {}})

    monkeypatch.setattr(session, "get", get)
    monkeypatch.setattr(session, "post", post)
    monkeypatch.setattr(api.requests, "Session", lambda: session)
    for _ in range(2):
        assert api.register("fixture@example.invalid", "password", "challenge")[0] is session
    assert calls == ["probe", "register", "probe", "register"]
    assert capsys.readouterr().out.count("注册前出口探测 IP：8.8.4.4") == 2
