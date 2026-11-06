"""Use real HTTP on localhost to verify Cloud Mail and ProxyScrape request contracts."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from nodes import api, cli, workflow
from nodes.instant_mail import InstantMail, decode_payload, encode_payload


@pytest.fixture
def api_server():
    state = {
        "accounts": [],
        "verification_sent": False,
        "verified": False,
        "requests": [],
        "login_count": 0,
        "expired": False,
        "expire_mail_auth": False,
        "mail_token": None,
        "trial_claimed": False,
        "trial_claims": 0,
        "instant_mail": False,
        "verification_count": 0,
        "verification_failures": 0,
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.dispatch()

        def do_POST(self):
            self.dispatch()

        def dispatch(self):
            path = self.path.split("?")[0]
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            state["requests"].append((self.command, path, body))
            result = None
            status = 200
            if (
                path == "/mail/api/email/list"
                and state["verification_sent"]
                and state["expire_mail_auth"]
                and not state["expired"]
            ):
                state["expired"] = True
                state["mail_token"] = "revoked"
                self.respond({"code": 401, "message": "auth expired", "data": None})
                return
            if path.startswith("/gmail/api/"):
                assert self.headers["x-app-key"] == "b9db03078622"
                query = parse_qs(urlsplit(self.path).query)
                email = "fixture@gmail.com"
                if path == "/gmail/api/g-mail":
                    assert self.command == "POST"
                    assert query == {"params": ["x03e"]}
                    assert json.loads(decode_payload(json.loads(body)["data"])) == {
                        "email": "random@gmail.com"
                    }
                    state["accounts"].append({"email": email})
                    result = {"success": True, "id": "gmail-mailbox", "email": email}
                else:
                    assert json.loads(decode_payload(query["params"][0])) == {"email": email}
                    assert path.startswith("/gmail/api/email/gmail-mailbox/messages")
                    old = {
                        "id": "zzz-old", "from": "no-reply@proxyscrape.com", "to": email,
                        "subject": "Old verification code: 999999",
                    }
                    if path.endswith("/messages"):
                        result = [old]
                        for number in range(state["verification_count"]):
                            result.insert(0, {
                                "id": f"aaa-new-{number}", "from": "no-reply@proxyscrape.com",
                                "to": email, "subject": "Verify your email",
                            })
                    else:
                        assert "/aaa-new-" in path, "Must never read a baseline message"
                        result = {
                            "id": path.rsplit("/", 1)[-1], "to": email,
                            "from": "no-reply@proxyscrape.com",
                            "html": "<b>Your verification code: aB3dE6gH9j</b>",
                        }
                self.respond({"data": encode_payload(json.dumps(result))})
                return
            if path == "/mail/api/login":
                assert json.loads(body) == {"email": "owner@example.org", "password": "secret"}
                state["login_count"] += 1
                state["mail_token"] = f"mail-session-{state['login_count']}"
                result = {"token": state["mail_token"]}
            elif path.startswith("/mail/api/"):
                assert self.headers["Authorization"] == state["mail_token"]
                if path.endswith("/setting/websiteConfig"):
                    result = {"domainList": ["@example.org"], "minEmailPrefix": 24}
                elif path.endswith("/account/list"):
                    result = state["accounts"]
                elif path.endswith("/account/add"):
                    email = json.loads(body)["email"]
                    result = {"accountId": 5, "email": email, "sort": 0}
                    state["accounts"].append(result)
                elif path.endswith("/email/list"):
                    rows = []
                    if state["verification_sent"]:
                        rows.append(
                            {
                                "emailId": 8,
                                "sendEmail": "no-reply@proxyscrape.com",
                                "toEmail": state["accounts"][0]["email"],
                                "subject": "Verify your email",
                                "content": "<b>Your verification code: aB3dE6gH9j</b>",
                            }
                        )
                    result = {"list": rows}
                result = {"code": 200, "data": result}
            elif path == "/register":
                assert b"cf_turnstile_token=local-token" in body
                result = {
                    "access_token": "local-access",
                    "userData": {"associatedSubaccounts": []},
                }
            else:
                assert self.headers["Authorization"] == "Bearer local-access"
                if path == "/resend":
                    state["verification_sent"] = True
                    state["verification_count"] += 1
                    result = {"success": True}
                elif path == "/verify":
                    assert "multipart/form-data" in self.headers["Content-Type"]
                    assert b'name="verificationCode"' in body
                    assert b"aB3dE6gH9j" in body
                    if state["verification_failures"]:
                        state["verification_failures"] -= 1
                        self.respond({"error": "temporary verification failure"}, 503)
                        return
                    state["verified"] = True
                    result = {"success": True}
                elif path == "/me":
                    result = {"EmailVerified": state["verified"]}
                elif path.endswith("/premium/trial-eligibility"):
                    result = {"success": True, "claimed": state["trial_claimed"]}
                elif path.endswith("/premium/claim-trial"):
                    assert self.command == "POST"
                    assert state["trial_claimed"] is False
                    state["trial_claimed"] = True
                    state["trial_claims"] += 1
                    result = {"success": True, "account_id": "trial-42"}
                elif path.endswith("/account/subaccounts"):
                    result = {
                        "success": True,
                        "data": {
                            "subaccounts": [
                                {"AccountID": "trial-42", "AccountType": "premium"},
                            ]
                            if state["trial_claimed"]
                            else []
                        },
                    }
                elif path.endswith("/services/overview"):
                    result = {
                        "status": "valid",
                        "data": {
                            "is_trial": True,
                            "proxy_credentials_enabled": True,
                            "services": {
                                "datacenter_shared": {
                                    "proxy_username": "user",
                                    "proxy_password": "pass",
                                }
                            },
                        },
                    }
                elif path.endswith("/proxy-list"):
                    query = parse_qs(urlsplit(self.path).query)
                    assert query["format"] == ["json"]
                    assert query["type"] == ["displayproxies"]
                    assert query["protocol"] == ["http"]
                    result = {
                        "data": [
                            ["127.0.0.1:8000", "HTTP", "Online", "us"],
                            ["127.0.0.2:8001", "HTTP", "Online", "us"],
                        ],
                        "recordsFiltered": 2,
                    }
                else:
                    status = 404
                    result = {"error": "unknown endpoint"}
            if path == "/mail/api/login":
                result = {"code": 200, "data": result}
            self.respond(result, status)

        def respond(self, result, status=200):
            payload = result.encode() if isinstance(result, str) else json.dumps(result).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("expire_mail_auth", [False, True])
def test_one_round_creates_mailbox_verifies_and_persists_exports(
    api_server,
    monkeypatch,
    tmp_path,
    expire_mail_auth,
):
    base, state = api_server
    state["expire_mail_auth"] = expire_mail_auth
    config = {
        "cloud_mail_url": base + "/mail",
        "cloud_mail_email": "owner@example.org",
        "cloud_mail_password": "secret",
        "browser_path": "",
        "turnstile_extension_path": "",
    }
    monkeypatch.setattr(cli, "load_config", lambda _: config)
    monkeypatch.setattr(workflow, "solve_turnstile", lambda **kwargs: "local-token")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "relaunch_with_display", lambda argv: None)
    monkeypatch.setattr(api, "BASE_URL", base)
    for name, path in [
        ("PS_REGISTER", "/register"),
        ("PS_RESEND", "/resend"),
        ("PS_VERIFY_EMAIL", "/verify"),
        ("PS_ME", "/me"),
    ]:
        monkeypatch.setattr(api, name, base + path)
    assert cli.main(["--count", "1", "--threads", "1", "--attempts", "1"]) == 0
    records = list((tmp_path / "account").glob("*.jsonl"))
    assert len(records) == 1
    record = json.loads(records[0].read_text())
    assert record["verified"] is True
    assert record["proxy_count"] == 2
    assert record["email"] == state["accounts"][0]["email"]
    exports = list((tmp_path / "node").glob("*.txt"))
    assert len(exports) == 1
    assert exports[0].read_text().splitlines() == [
        "http://user:pass@127.0.0.1:8000",
        "http://user:pass@127.0.0.2:8001",
    ]
    assert sum(path == "/mail/api/account/add" for _, path, _ in state["requests"]) == 1

    assert state["login_count"] == (2 if expire_mail_auth else 1)
    assert sum(path == "/register" for _, path, _ in state["requests"]) == 1
    assert sum(path == "/verify" for _, path, _ in state["requests"]) == 1
    assert len(state["accounts"]) == 1

    assert record["proxy_account_id"] == "trial-42"
    assert state["trial_claims"] == 1
    assert cli.main(["--resume", record["email"]]) == 0
    assert state["trial_claims"] == 1
    assert sum(path == "/register" for _, path, _ in state["requests"]) == 1
    assert sum(path == "/verify" for _, path, _ in state["requests"]) == 1
    assert sum(path == "/mail/api/account/add" for _, path, _ in state["requests"]) == 1
    assert len(list((tmp_path / "node").glob("*.txt"))) == 2


@pytest.mark.parametrize("fail_verification", [False, True])
def test_gmail_round_and_resume_use_persisted_mailbox_without_reregistering(
    api_server, monkeypatch, tmp_path, fail_verification,
):
    from nodes import providers

    base, state = api_server
    state["verification_failures"] = int(fail_verification)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "load_config", lambda _: {"mail_provider": "instant_mail"})
    monkeypatch.setattr(cli, "relaunch_with_display", lambda argv: None)
    monkeypatch.setattr(workflow, "solve_turnstile", lambda **kw: "local-token")
    def factory(**kw):
        return InstantMail(base_url=base + "/gmail", **kw)

    monkeypatch.setattr(providers, "InstantMail", factory)
    monkeypatch.setattr(workflow, "InstantMail", factory)
    monkeypatch.setattr(api, "BASE_URL", base)
    for name, path in [
        ("PS_REGISTER", "/register"), ("PS_RESEND", "/resend"),
        ("PS_VERIFY_EMAIL", "/verify"), ("PS_ME", "/me"),
    ]:
        monkeypatch.setattr(api, name, base + path)
    assert cli.main(["--count", "1", "--threads", "1", "--attempts", "1"]) == int(fail_verification)
    record_file = next((tmp_path / "account").glob("accounts_*.jsonl"))
    records = [json.loads(line) for line in record_file.read_text().splitlines()]
    assert records[0]["verified"] is False  # saved before waiting for mail
    assert records[-1]["mailbox_id"] == "gmail-mailbox"
    assert records[-1]["mail_provider"] == "instant_mail"
    assert records[-1]["verified"] is not fail_verification
    assert cli.main(["--resume", "fixture@gmail.com", "--mail-timeout", "1"]) == 0
    resumed = json.loads(record_file.read_text().splitlines()[-1])
    assert resumed["verified"] is True
    assert resumed["proxy_count"] == 2
    assert state["trial_claims"] == 1
    assert len(state["accounts"]) == 1
    paths = [path for _, path, _ in state["requests"]]
    assert paths.count("/gmail/api/g-mail") == 1
    assert paths.count("/register") == 1
    assert paths.count("/resend") == 1 + int(fail_verification)
    assert not any(path.startswith("/mail/") for path in paths)
