import json

import pytest
import requests

from nodes import api, providers
from nodes import workflow as app

CONFIG = {
    "cloud_mail_url": "https://mail.example.org",
    "cloud_mail_email": "owner@example.org",
    "cloud_mail_password": "secret",
    "browser_path": "",
    "turnstile_extension_path": "",
}


class Session:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class Mail:
    instances = []

    def __init__(self, base_url, email, password):
        assert (base_url, email, password) == (
            "https://mail.example.org",
            "owner@example.org",
            "secret",
        )
        self.events = []
        self.session = Session()
        self.instances.append(self)

    def new_address(self):
        return "random@example.org"

    def ensure_mailbox(self, email):
        self.events.append(("create", email))

    def list_messages(self, email):
        self.events.append(("baseline", email))
        return [{"emailId": 7}]

    def wait_for_code(self, email, **kwargs):
        assert kwargs == {"after_id": 7, "timeout": 9}
        self.events.append(("wait", email))
        return "aB3dE6gH9j"


def setup_flow(monkeypatch):
    monkeypatch.setattr(providers, "CloudMail", Mail)
    monkeypatch.setattr(app, "solve_turnstile", lambda **kw: "challenge-token")
    monkeypatch.setattr(
        api,
        "register",
        lambda *a: (
            Session(),
            "access-token",
            {"associatedSubaccounts": []},
        ),
    )
    monkeypatch.setattr(api, "resend_code", lambda *a: None)
    monkeypatch.setattr(api, "whoami", lambda *a: {"EmailVerified": True})
    monkeypatch.setattr(
        api,
        "fetch_trial_proxies",
        lambda session, token: (
            "trial-42",
            "proxy-user",
            "proxy-pass",
            ["127.0.0.1:8080"],
        ),
        raising=False,
    )
    return Mail(CONFIG["cloud_mail_url"], CONFIG["cloud_mail_email"], CONFIG["cloud_mail_password"])


def test_cloud_mail_flow_exports_verified_account_and_proxy(monkeypatch, tmp_path):
    mail = setup_flow(monkeypatch)

    def verify(session, token, code):
        assert code == "aB3dE6gH9j"

    monkeypatch.setattr(api, "verify_email", verify)
    path = tmp_path / "proxies.txt"
    rec = app._register_once(True, path, CONFIG, mail, mail_timeout=9)
    assert rec["email"] == "random@example.org"
    assert rec["verified"] is True
    assert rec["proxy_count"] == 1
    assert rec["proxy_account_id"] == "trial-42"
    assert mail.session.closed is False
    assert Mail.instances[-1].events == [
        ("create", "random@example.org"),
        ("baseline", "random@example.org"),
        ("wait", "random@example.org"),
    ]
    assert path.read_text() == "http://proxy-user:proxy-pass@127.0.0.1:8080\n"


def test_mail_failure_is_saved_and_returns_failure(monkeypatch, tmp_path):
    mail = setup_flow(monkeypatch)

    def timeout(*a, **kw):
        raise TimeoutError("mail timed out")

    monkeypatch.setattr(Mail, "wait_for_code", timeout)
    account = tmp_path / "accounts.jsonl"
    result = app.register_one(
        1,
        True,
        account,
        tmp_path / "proxies.txt",
        CONFIG,
        mail,
        max_attempts=1,
    )
    assert result["verified"] is False
    assert result["error"] == "mail timed out"
    records = [json.loads(line) for line in account.read_text().splitlines()]
    assert records[0]["password"] == records[-1]["password"]
    assert records[0]["verified"] is False
    assert records[-1]["access_token"] == "access-token"
    assert not (tmp_path / "proxies.txt").exists()


def test_does_not_export_when_server_still_reports_unverified(monkeypatch, tmp_path):
    mail = setup_flow(monkeypatch)
    monkeypatch.setattr(api, "verify_email", lambda *a: None)
    monkeypatch.setattr(api, "whoami", lambda *a: {"EmailVerified": False})
    rec = app._register_once(True, tmp_path / "proxies.txt", CONFIG, mail, mail_timeout=9)
    assert rec["verified"] is False
    assert "验证" in rec["error"]
    assert not (tmp_path / "proxies.txt").exists()


@pytest.mark.parametrize("endpoint", ["resend_code", "verify_email"])
def test_verification_http_failure_saves_account_without_export(monkeypatch, tmp_path, endpoint):
    mail = setup_flow(monkeypatch)
    session = Session()
    monkeypatch.setattr(api, "register", lambda *args: (session, "access-token", {}))

    def rejected(*args):
        raise requests.HTTPError(f"{endpoint}: HTTP 403")

    def unexpected(*args):
        pytest.fail("Failed verification must not continue to trial or export")

    monkeypatch.setattr(api, endpoint, rejected)
    monkeypatch.setattr(api, "whoami", unexpected)
    monkeypatch.setattr(api, "fetch_trial_proxies", unexpected)
    account = tmp_path / "accounts.jsonl"
    result = app.register_one(
        1, True, account, tmp_path / "proxies.txt", CONFIG, mail, max_attempts=1, mail_timeout=9,
    )
    assert result["error"] == f"{endpoint}: HTTP 403"
    assert result["verified"] is False
    records = [json.loads(line) for line in account.read_text().splitlines()]
    assert records[0]["password"] == records[-1]["password"]
    assert records[0]["verified"] is False
    assert records[-1]["access_token"] == "access-token"
    assert not (tmp_path / "proxies.txt").exists()
    assert session.closed is True


def test_round_shares_mail_client_and_closes_it_after_all_workers(monkeypatch, tmp_path):
    calls = []
    clients = []

    class Login(Mail):
        def _login(self):
            calls.append("login")
            self.token = "one-round-token"

    def worker(idx, hidden, acc_file, node_file, config, mail, **kwargs):
        assert mail.session.closed is False
        clients.append(mail)
        return {"proxy_count": 1}

    monkeypatch.setattr(providers, "CloudMail", Login)
    monkeypatch.setattr(app, "register_one", worker)
    assert app.run_round(3, 2, True, CONFIG, root=tmp_path) is True
    assert calls == ["login"]
    assert len(clients) == 3
    assert all(client is clients[0] for client in clients)
    assert clients[0].session.closed is True
