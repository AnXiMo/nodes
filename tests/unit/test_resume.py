import json

import pytest

from nodes import api, cli, providers
from nodes import workflow as app

EMAIL = "saved@example.org"


@pytest.fixture
def saved_account(tmp_path, monkeypatch):
    account_dir = tmp_path / "account"
    node_dir = tmp_path / "node"
    account_dir.mkdir()
    path = account_dir / "accounts_20261019_000000.jsonl"
    previous = {"email": "other@example.org", "access_token": "other-token"}
    target = {
        "email": EMAIL,
        "password": "saved-password",
        "access_token": "saved-token",
        "verified": True,
        "proxy_count": 0,
        "error": "missing AccountID",
    }
    path.write_text(json.dumps(previous) + "\n" + json.dumps(target) + "\n")
    monkeypatch.chdir(tmp_path)

    def unexpected(*args, **kwargs):
        pytest.fail("Resume must not create a mailbox, register, or launch a browser")

    monkeypatch.setattr(providers, "CloudMail", unexpected)
    monkeypatch.setattr(api, "register", unexpected)
    monkeypatch.setattr(app, "solve_turnstile", unexpected)
    monkeypatch.setattr(cli, "load_config", lambda path: {})
    monkeypatch.setattr(cli, "relaunch_with_display", unexpected)
    monkeypatch.setattr(api, "whoami", lambda session, token: {"EmailVerified": True})
    calls = []

    def proxies(session, token):
        assert token == "saved-token"
        calls.append(token)
        return "trial-42", "proxy-user", "p@ss:word", ["127.0.0.1:8080"]

    monkeypatch.setattr(api, "fetch_trial_proxies", proxies, raising=False)
    return path, node_dir, previous, target, calls


def test_resume_exports_for_the_same_saved_account_without_registration(saved_account):
    path, node_dir, previous, target, calls = saved_account
    assert cli.main(["--resume", EMAIL]) == 0
    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert records[:2] == [previous, target]
    resumed = records[-1]
    assert resumed["email"] == EMAIL
    assert resumed["access_token"] == target["access_token"]
    assert resumed["proxy_account_id"] == "trial-42"
    assert resumed["proxy_count"] == 1
    assert "error" not in resumed
    exports = list(node_dir.glob("*.txt"))
    assert len(exports) == 1
    assert exports[0].read_text() == "http://proxy-user:p%40ss%3Aword@127.0.0.1:8080\n"
    assert calls == ["saved-token"]
    assert len(list(path.parent.glob("*.jsonl"))) == 1


def test_resume_reports_server_failure_and_keeps_credentials(saved_account, monkeypatch):
    path, node_dir, _, target, _ = saved_account

    def rejected(*args):
        raise RuntimeError("POST claim-trial HTTP 403: trial unavailable")

    monkeypatch.setattr(api, "fetch_trial_proxies", rejected, raising=False)
    assert cli.main(["--resume", EMAIL]) == 1
    resumed = json.loads(path.read_text().splitlines()[-1])
    assert resumed["access_token"] == target["access_token"]
    assert "trial unavailable" in resumed["error"]
    assert not list(node_dir.glob("*.txt"))


def test_resume_missing_account_does_not_touch_remote_services(saved_account):
    path, _, _, _, calls = saved_account
    before = path.read_text()
    assert cli.main(["--resume", "unknown@example.org"]) == 1
    assert path.read_text() == before
    assert calls == []


def test_resume_unverified_account_does_not_claim_a_trial(saved_account, monkeypatch):
    path, _, _, _, calls = saved_account
    monkeypatch.setattr(api, "whoami", lambda session, token: {"EmailVerified": False})
    assert cli.main(["--resume", EMAIL]) == 1
    assert calls == []
    resumed = json.loads(path.read_text().splitlines()[-1])
    assert resumed["verified"] is False
    assert resumed["userData"]["EmailVerified"] is False


def test_resume_sync_failure_keeps_successful_export_and_account(saved_account, monkeypatch):
    path, node_dir, _, _, calls = saved_account
    monkeypatch.setattr(
        cli, "load_config",
        lambda path: {"resin_url": "https://resin.example.org", "resin_admin_token": "admin"},
    )

    def failed(config, **kwargs):
        assert calls == ["saved-token"]
        assert len(list(node_dir.glob("*.txt"))) == 1
        raise RuntimeError("Resin HTTP 401; retry --sync-resin")

    monkeypatch.setattr(cli.resin, "sync", failed)
    assert cli.main(["--resume", EMAIL]) == 1
    record = json.loads(path.read_text().splitlines()[-1])
    assert record["verified"] is True
    assert record["proxy_count"] == 1
    assert "error" not in record
    assert len(list(node_dir.glob("*.txt"))) == 1


def test_resume_and_count_are_mutually_exclusive():
    with pytest.raises(SystemExit) as error:
        cli.main(["--resume", EMAIL, "--count", "1"])
    assert error.value.code == 2


@pytest.mark.parametrize("expiry", ["initial", "export"])
def test_expired_token_relogs_once_and_keeps_password(saved_account, monkeypatch, expiry):
    path, node_dir, _, target, _ = saved_account
    logins = []

    def whoami(session, token):
        if expiry == "initial" and token == "saved-token":
            raise api.SessionExpired("expired")
        return {"EmailVerified": True}

    def login(session, email, password):
        logins.append((email, password))
        return "renewed-token", {"EmailVerified": True}

    def export(session, token):
        if token == "saved-token":
            raise api.SessionExpired("expired during export")
        assert token == "renewed-token"
        assert json.loads(path.read_text().splitlines()[-1])["access_token"] == token
        return "trial-42", "user", "pass", ["127.0.0.1:8080"]

    monkeypatch.setattr(api, "whoami", whoami)
    monkeypatch.setattr(api, "login", login)
    monkeypatch.setattr(api, "fetch_trial_proxies", export)
    assert cli.main(["--resume", EMAIL.upper()]) == 0
    assert logins == [(EMAIL, target["password"])]
    last = json.loads(path.read_text().splitlines()[-1])
    assert last["password"] == target["password"]
    assert last["access_token"] == "renewed-token"
    assert len(list(node_dir.glob("*.txt"))) == 1


def test_expired_plan_keeps_successful_login(saved_account, monkeypatch):
    path, _, _, target, _ = saved_account
    monkeypatch.setenv("NODES_ACCOUNT_PASSWORD", target["password"])
    monkeypatch.setattr(api, "login", lambda *args: ("renewed-token", {}))

    def expired(*args):
        raise RuntimeError("plan expired")

    monkeypatch.setattr(api, "fetch_trial_proxies", expired)
    assert cli.main(["--login", EMAIL]) == 1
    last = json.loads(path.read_text().splitlines()[-1])
    assert last["access_token"] == "renewed-token"
    assert last["password"] == target["password"]
    assert last["error"] == "plan expired"
    assert last["proxy_count"] == 0


def test_incorrect_explicit_password_cannot_replace_saved_credentials(
    saved_account, monkeypatch, capsys,
):
    path, _, _, _, _ = saved_account
    before = path.read_bytes()
    monkeypatch.setenv("NODES_ACCOUNT_PASSWORD", "wrong-password")

    def rejected(*args):
        raise RuntimeError("invalid password: wrong-password")

    monkeypatch.setattr(api, "login", rejected)
    assert cli.main(["--login", EMAIL]) == 1
    assert path.read_bytes() == before
    assert "wrong-password" not in capsys.readouterr().out


def test_no_password_cannot_register_a_replacement(saved_account, monkeypatch):
    path, _, _, target, _ = saved_account
    target.pop("password")
    target.pop("access_token")
    path.write_text(json.dumps(target) + "\n")
    monkeypatch.setattr(api, "login", lambda *a: pytest.fail("missing credentials"))
    assert cli.main(["--resume", EMAIL]) == 1
    assert "没有保存登录密码" in json.loads(path.read_text().splitlines()[-1])["error"]


def test_new_token_rejected_does_not_loop(saved_account, monkeypatch):
    path, _, _, _, _ = saved_account
    calls = []

    def expired(*args):
        raise api.SessionExpired("expired")

    def login(*args):
        calls.append(1)
        return "new-token", {}

    monkeypatch.setattr(api, "whoami", expired)
    monkeypatch.setattr(api, "login", login)
    assert cli.main(["--resume", EMAIL]) == 1
    assert calls == [1]
    assert json.loads(path.read_text().splitlines()[-1])["access_token"] == "new-token"


def test_import_existing_account_by_password_does_not_register(saved_account, monkeypatch):
    path, _, _, _, _ = saved_account
    monkeypatch.setenv("NODES_ACCOUNT_PASSWORD", "existing-password")
    calls = []

    def login(session, email, password):
        calls.append((email, password))
        return "saved-token", {}

    monkeypatch.setattr(api, "login", login)
    assert cli.main(["--login", "existing@example.org"]) == 0
    assert calls == [("existing@example.org", "existing-password")]
    from nodes.storage import find_account

    _, imported = find_account(path.parent.parent, "existing@example.org")
    assert imported["password"] == "existing-password"
    assert imported["proxy_count"] == 1


def test_resume_all_continues_after_an_account_failure(saved_account, monkeypatch):
    calls = []

    def resume(email, **kwargs):
        calls.append(email)
        return email == EMAIL

    monkeypatch.setattr(app, "resume_account", resume)
    assert cli.main(["--resume-all"]) == 1
    assert calls == ["other@example.org", EMAIL]
