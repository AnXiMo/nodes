import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

from nodes import api, github_vault, login_challenge, workflow


def response(status=200, **body):
    value = requests.Response()
    value.status_code = status
    value._content = json.dumps(body).encode()
    return value


def test_login_uses_official_form_contract():
    session = Mock()
    session.post.return_value = response(access_token="session-token", userData={"id": 1})
    assert api.login(session, "saved@example.org", "saved-password", "challenge") == (
        "session-token", {"id": 1},
    )
    session.post.assert_called_once_with(
        api.PS_LOGIN, headers={"Referer": api.PS_LOGIN_PAGE},
        data={"email": "saved@example.org", "password": "saved-password",
              "cf_turnstile_token": "challenge"}, timeout=30,
    )


def test_login_redacts_provider_errors():
    session = Mock()
    session.post.return_value = response(401, error='wrong password: secret"with\\slashes')
    with pytest.raises(RuntimeError) as error:
        api.login(session, "saved@example.org", 'secret"with\\slashes')
    assert "secret" not in str(error.value)


def test_login_requires_a_real_access_token():
    session = Mock()
    session.post.return_value = response(success=True)
    with pytest.raises(RuntimeError, match="access_token"):
        api.login(session, "saved@example.org", "password")


def test_login_challenge_retries_only_on_typed_challenge(monkeypatch):
    calls = []

    def login(session, email, password, token=""):
        calls.append(token)
        if not token:
            raise api.LoginChallengeRequired("turnstile")
        return "new-session", {}

    monkeypatch.delenv("NODES_LOGIN_CHALLENGE_TOKEN", raising=False)
    monkeypatch.setattr(api, "login", login)
    monkeypatch.setattr(login_challenge, "obtain", lambda **kwargs: "native-challenge")
    record = {"email": "saved@example.org", "password": "password"}
    workflow._relogin(Mock(), record)
    assert calls == ["", "native-challenge"]
    assert record["access_token"] == "new-session"


def test_native_challenge_targets_login_page(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":99")
    solve = Mock(return_value="native-token")
    monkeypatch.setattr(login_challenge, "solve_turnstile", solve)
    assert login_challenge.obtain(config={}, hidden=False, timeout=17) == "native-token"
    solve.assert_called_once_with(
        headless=False, timeout=17, config={}, page_url=api.PS_LOGIN_PAGE,
    )


@pytest.mark.parametrize("status", [0, 1])
def test_headless_host_challenge_uses_private_ipc(monkeypatch, capsys, status):
    monkeypatch.setattr(login_challenge.platform, "system", lambda: "Linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setattr(login_challenge.shutil, "which", lambda name: "/bin/xvfb-run")
    run = Mock(return_value=SimpleNamespace(
        returncode=status, stdout='{"token":"sensitive-token"}', stderr="private-log",
    ))
    monkeypatch.setattr(login_challenge.subprocess, "run", run)
    if status:
        with pytest.raises(RuntimeError, match="登录浏览器验证失败"):
            login_challenge.obtain(config={})
    else:
        assert login_challenge.obtain(config={}) == "sensitive-token"
    assert run.call_args.args[0][-1] == "nodes.login_challenge"
    assert run.call_args.kwargs["capture_output"] is True
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("endpoint", ["whoami", "fetch_trial_proxies"])
def test_account_401_is_session_expiry(endpoint):
    session = Mock()
    session.post.return_value = session.request.return_value = response(401)
    with pytest.raises(api.SessionExpired):
        getattr(api, endpoint)(session, "expired-token")


@pytest.mark.parametrize("head", [None, "old-head"])
def test_github_upload_is_ciphertext_only_and_never_force_pushes(head):
    client = github_vault.GitHubVault("owner/nodes", "fake-token", session=Mock())
    client.head = lambda: head
    client.request = Mock(side_effect=[{"sha": "blob"}, {"sha": "tree"},
                                       {"sha": "commit"}, {}])
    assert client.upload(b"encrypted-fixture", head) == "commit"
    calls = client.request.call_args_list
    commit_body = calls[2].args[2]
    assert commit_body["parents"] == ([head] if head else [])
    if head:
        assert calls[-1].args[2] == {"sha": "commit", "force": False}
    else:
        assert calls[-1].args[2]["ref"] == "refs/heads/account-vault"


def test_github_race_detected_before_creating_objects():
    client = github_vault.GitHubVault("owner/nodes", "fake-token", session=Mock())
    client.head = lambda: "changed-head"
    client.request = Mock()
    with pytest.raises(RuntimeError, match="已被其他运行更新"):
        client.upload(b"encrypted", "old-head")
    client.request.assert_not_called()


def test_login_opens_email_form_before_challenge_api_is_loaded(monkeypatch):
    import DrissionPage

    from nodes import browser

    tab = Mock()
    tab.run_js.return_value = True
    native = Mock(latest_tab=tab)
    monkeypatch.setattr(DrissionPage, "Chromium", lambda opts: native)
    monkeypatch.setattr(browser, "browser_options", lambda *args: None)
    monkeypatch.setattr(browser.time, "sleep", lambda _: None)
    states = iter([{"ready": True, "hasApi": False}, {"token": "native", "tokenLen": 6}])
    monkeypatch.setattr(browser, "_read_state", lambda tab: next(states))
    assert browser.solve_turnstile(
        config={"browser_path": "", "turnstile_extension_path": ""},
        page_url=api.PS_LOGIN_PAGE,
    ) == "native"
    assert "Continue with email" in tab.run_js.call_args.args[0]
    native.quit.assert_called_once()
