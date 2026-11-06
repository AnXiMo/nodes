import json
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import unquote, urlsplit

import pytest
import requests

from nodes import cli, providers
from nodes.instant_mail import (
    GMAIL_BASE_URL,
    InstantMail,
    decode_payload,
    encode_email_payload,
    encode_payload,
)

EMAIL = "fixture@gmail.com"
CODE = "aB3dE6gH9j"


def message(message_id="message-new", **overrides):
    return {
        "id": message_id,
        "from": "ProxyScrape <noreply@proxyscrape.com>",
        "to": EMAIL,
        "subject": "Verify your email",
        **overrides,
    }


class Session:
    def __init__(self):
        self.calls = []
        self.rows = []
        self.details = {}
        self.created = {"success": True, "email": EMAIL, "id": "mail/box"}
        self.closed = False

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        assert url.startswith(GMAIL_BASE_URL + "/")
        assert kwargs["timeout"] == 30
        assert kwargs["headers"]["x-app-key"] == "b9db03078622"
        assert kwargs["headers"]["user-agent"] == "okhttp/4.12.0"
        if method == "POST":
            assert url.endswith("/api/g-mail")
            assert kwargs["params"] == {"params": "x03e"}
            assert json.loads(decode_payload(kwargs["json"]["data"])) == {
                "email": "random@gmail.com"
            }
            data = self.created
        else:
            assert json.loads(decode_payload(kwargs["params"]["params"])) == {"email": EMAIL}
            assert "/mail%2Fbox/messages" in url
            path = urlsplit(url).path
            data = (
                self.rows
                if path.endswith("/messages")
                else self.details[unquote(path.rsplit("/", 1)[-1])]
            )
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps({"data": encode_payload(json.dumps(data))}).encode()
        return response

    def close(self):
        self.closed = True


@pytest.fixture
def mailbox(tmp_path):
    session = Session()
    mail = InstantMail(root=tmp_path, session=session)
    assert mail.new_address() == EMAIL
    return mail, session


def test_original_codec_known_vectors():
    assert decode_payload("x03e") == "{}"
    assert decode_payload("x01W") == "[]"
    assert encode_email_payload("ocrjaxgjjm@tempmail.edu.pl") == (
        "x0nIqznL1RWZuwWoh1GctVGdA1mosdGehpmcj9mI6IC5pFW5lJye"
    )
    assert decode_payload(encode_payload("验证码")) == "验证码"


def test_mailbox_id_persists_and_restores_without_creating_another(mailbox, tmp_path):
    mail, session = mailbox
    state = next((tmp_path / "account" / "instant-mail").glob("*.json"))
    assert json.loads(state.read_text(encoding="utf-8"))["mailbox_id"] == "mail/box"
    restored = InstantMail(root=tmp_path, session=session)
    assert restored.list_messages(EMAIL) == []
    assert sum(method == "POST" for method, _, _ in session.calls) == 1
    assert restored.mailbox_record(EMAIL) == {
        "mail_provider": "instant_mail",
        "mailbox_id": "mail/box",
    }


@pytest.mark.parametrize(
    "email", ["fixture@googlemail.com", "a@gmail.com.evil", "a@outlook.com", "@gmail.com", None]
)
def test_only_exact_gmail_domain_is_accepted(tmp_path, email):
    session = Session()
    session.created["email"] = email
    with pytest.raises(ValueError, match="gmail.com"):
        InstantMail(root=tmp_path, session=session).new_address()
    assert not (tmp_path / "account").exists()


@pytest.mark.parametrize("created", [{"success": False}, {"success": True, "email": EMAIL}, []])
def test_invalid_create_response_cannot_start_registration(tmp_path, created):
    session = Session()
    session.created = created
    with pytest.raises((ValueError, RuntimeError)):
        InstantMail(root=tmp_path, session=session).new_address()


def test_missing_mailbox_id_does_not_reallocate_a_gmail(tmp_path):
    session = Session()
    mail = InstantMail(root=tmp_path, session=session)
    with pytest.raises(ValueError, match="mailbox_id"):
        mail.list_messages(EMAIL)
    assert not session.calls


def test_stale_ids_and_list_reordering_do_not_supply_old_codes(mailbox):
    mail, session = mailbox
    session.rows = [message("zzz-old"), message("yyy-old")]
    after_id = max(row["emailId"] for row in mail.list_messages(EMAIL))
    session.rows = [message("aaa-new"), message("yyy-old"), message("zzz-old")]
    session.details["aaa-new"] = {"id": "aaa-new", "html": f"<b>Verification code: {CODE}</b>"}
    assert mail.wait_for_code(EMAIL, after_id=after_id, timeout=0) == CODE
    assert sum("/messages/" in url for _, url, _ in session.calls) == 1
    session.rows.reverse()
    with pytest.raises(TimeoutError, match="未找到新邮件"):
        mail.wait_for_code(EMAIL, after_id=3, timeout=0)


@pytest.mark.parametrize(
    "overrides",
    [
        {"from": "noreply@proxyscrape.com.evil"},
        {"from": "noreply@evilproxyscrape.com"},
        {"to": "another@gmail.com"},
        {"from": ""},
    ],
)
def test_wrong_sender_or_recipient_cannot_verify_an_account(mailbox, overrides):
    mail, session = mailbox
    session.rows = [message(**overrides)]
    session.details["message-new"] = {"id": "message-new", "text": f"Verification code: {CODE}"}
    with pytest.raises(TimeoutError, match="未匹配"):
        mail.wait_for_code(EMAIL, after_id=0, timeout=0)


def test_detail_sender_overrides_list_sender_before_filtering(mailbox):
    mail, session = mailbox
    session.rows = [message()]
    session.details["message-new"] = message(
        **{"from": "bad@evil.test", "text": f"Verification code: {CODE}"}
    )
    with pytest.raises(TimeoutError, match="未匹配"):
        mail.wait_for_code(EMAIL, after_id=0, timeout=0)


def test_detail_path_escapes_opaque_id_and_reads_html(mailbox):
    mail, session = mailbox
    session.rows = [message("part/one")]
    session.details["part/one"] = {
        "id": "part/one",
        "from": {"address": "noreply@mail.proxyscrape.com"},
        "to": {"address": EMAIL},
        "html": f"<p>Verification code: <b>{CODE}</b></p>",
    }
    assert mail.wait_for_code(EMAIL, after_id=0, timeout=0) == CODE
    assert session.calls[-1][1].endswith("/messages/part%2Fone")


def test_concurrent_polling_keeps_ids_stable(mailbox):
    mail, session = mailbox
    session.rows = [message("a"), message("b")]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: mail.list_messages(EMAIL), range(12)))
    assert all(rows == results[0] for rows in results)
    assert sorted(row["emailId"] for row in results[0]) == [1, 2]


@pytest.mark.parametrize("rows", [{"error": "expired"}, [{"id": 7}], [{}]])
def test_invalid_message_lists_report_error(mailbox, rows):
    mail, session = mailbox
    session.rows = rows
    with pytest.raises(RuntimeError, match="列表格式"):
        mail.list_messages(EMAIL)


@pytest.mark.parametrize(
    "status,body",
    [(403, "denied"), (200, "<html>error</html>"), (200, '{"data":"%%%"}'), (200, '{"data":42}')],
)
def test_http_and_decode_errors_are_explicit_and_close_check_mail_session(
    tmp_path, monkeypatch, status, body
):
    session = Session()
    response = requests.Response()
    response.status_code = status
    response._content = body.encode()
    session.request = lambda *a, **kw: response
    mail = InstantMail(root=tmp_path, session=session)
    monkeypatch.setattr(cli, "load_config", lambda _: {"mail_provider": "instant_mail"})
    monkeypatch.setattr(cli, "create_mail", lambda _: mail)
    assert cli.main(["--check-mail"]) == 1
    assert session.closed is True


def test_check_mail_needs_no_cloud_credentials_or_browser(tmp_path, monkeypatch):
    mail = InstantMail(root=tmp_path, session=Session())
    monkeypatch.setattr(cli, "load_config", lambda _: {"mail_provider": "instant_mail"})
    monkeypatch.setattr(cli, "create_mail", lambda _: mail)
    monkeypatch.setattr(cli, "relaunch_with_display", lambda *_: pytest.fail("no browser"))
    assert cli.main(["--check-mail"]) == 0
    assert mail.session.closed
    assert not (tmp_path / "node").exists()


def test_default_provider_and_explicit_override_keep_legacy_compatibility():
    cloud = {
        "cloud_mail_url": "https://example.test",
        "cloud_mail_email": "a@test.org",
        "cloud_mail_password": "p",
    }
    assert providers.mail_provider({}) == "instant_mail"
    assert providers.mail_provider(cloud) == "cloud_mail"
    assert providers.mail_provider({**cloud, "mail_provider": "instant_mail"}) == "instant_mail"
    with pytest.raises(ValueError, match="MAIL_PROVIDER"):
        providers.mail_provider({"mail_provider": "typo"})
    with pytest.raises(ValueError, match="CLOUD_MAIL_PASSWORD"):
        providers.mail_provider({"cloud_mail_url": "https://example.test"})
