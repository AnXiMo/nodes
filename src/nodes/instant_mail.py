"""Gmail protocol adapted from Instant_mail/instant_mail_api.py.

Keep the original codec, app headers and Gmail request contract. This adapter
adds bounded HTTP requests, durable mailbox IDs and Nodes' message interface.
"""

import base64
import hashlib
import json
import tempfile
from email.utils import parseaddr
from pathlib import Path
from threading import RLock
from urllib.parse import quote

import requests

from .mail import Mailbox

GMAIL_BASE_URL = "https://mail-server-2.1timetech.com"
APP_KEY = "b9db03078622"
TRANSLATION = str.maketrans("x=zB4*oaqs5b", "=xBz*4aosqb5")


def encode_payload(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")[::-1].translate(TRANSLATION)


def decode_payload(value: str) -> str:
    value = value.translate(TRANSLATION)[::-1]
    return base64.b64decode(value + "=" * (-len(value) % 4), validate=True).decode("utf-8")


def encode_email_payload(email: str) -> str:
    return encode_payload(json.dumps({"email": email}, separators=(",", ":"), ensure_ascii=False))


def _gmail(email):
    if not isinstance(email, str):
        raise ValueError("Instant Mail 必须返回 gmail.com 邮箱")
    address = email.strip()
    local, separator, domain = address.rpartition("@")
    if not separator or not local or "@" in local or domain.lower() != "gmail.com":
        raise ValueError("Instant Mail 仅支持 gmail.com 后缀")
    if parseaddr(address)[1] != address or any(c.isspace() for c in address):
        raise ValueError("Instant Mail Gmail 邮箱地址格式错误")
    return address


def _address(value):
    if isinstance(value, dict):
        value = value.get("address") or value.get("email") or ""
    return parseaddr(value)[1] if isinstance(value, str) else ""


class InstantMail(Mailbox):
    provider = "instant_mail"
    display_name = "Instant Mail Gmail"

    def __init__(self, *, root=None, session=None, base_url=GMAIL_BASE_URL):
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.directory = (Path.cwd() if root is None else Path(root)) / "account" / "instant-mail"
        self.accounts = {}
        self._message_ids = {}
        self._request_lock = RLock()

    def _request(self, method, path, *, email=None, create=False):
        headers = {
            "accept": "application/json",
            "x-app-key": APP_KEY,
            "user-agent": "okhttp/4.12.0",
        }
        kwargs = {"params": {"params": "x03e" if create else encode_email_payload(email)}}
        if create:
            kwargs["json"] = {"data": encode_email_payload("random@gmail.com")}
        # One shared Session per round, with serialized HTTP access.
        with self._request_lock:
            response = self.session.request(
                method, self.base_url + path, headers=headers, timeout=30, **kwargs
            )
            if not 200 <= response.status_code < 300:
                raise RuntimeError(f"Instant Mail {method} 请求失败: HTTP {response.status_code}")
            try:
                envelope = response.json()
                if not isinstance(envelope, dict) or not isinstance(envelope.get("data"), str):
                    raise ValueError("missing encoded data")
                return json.loads(decode_payload(envelope["data"]))
            except (ValueError, KeyError, TypeError) as exc:
                raise RuntimeError("Instant Mail 返回的不是有效编码 JSON") from exc

    def _state_path(self, email):
        digest = hashlib.sha256(email.lower().encode("utf-8")).hexdigest()
        return self.directory / f"{digest}.json"

    def restore_mailbox(self, email, mailbox_id):
        email = _gmail(email)
        if not isinstance(mailbox_id, str) or not mailbox_id.strip():
            raise ValueError("Instant Mail 缺少 mailbox_id，不能按完整 Gmail 地址重新创建邮箱")
        with self._request_lock:
            self.directory.mkdir(parents=True, exist_ok=True)
            record = {"mail_provider": self.provider, "email": email, "mailbox_id": mailbox_id}
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", dir=self.directory, delete=False
                ) as stream:
                    temporary = Path(stream.name)
                    json.dump(record, stream, ensure_ascii=False)
                    stream.write("\n")
                temporary.replace(self._state_path(email))
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            self.accounts[email.lower()] = mailbox_id

    def new_address(self):
        data = self._request("POST", "/api/g-mail", create=True)
        if not isinstance(data, dict) or data.get("success") is not True:
            raise RuntimeError("Instant Mail 创建 Gmail 邮箱失败")
        email = _gmail(data.get("email"))
        self.restore_mailbox(email, data.get("id"))
        return email

    def ensure_mailbox(self, email):
        email = _gmail(email)
        with self._request_lock:
            if email.lower() in self.accounts:
                return
            path = self._state_path(email)
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if (
                    data["email"].lower() != email.lower()
                    or data["mail_provider"] != self.provider
                    or not isinstance(data["mailbox_id"], str)
                    or not data["mailbox_id"].strip()
                ):
                    raise ValueError("邮箱凭据不匹配")
                self.accounts[email.lower()] = data["mailbox_id"]
            except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
                raise ValueError(
                    f"无法恢复 {email} 的 Instant Mail mailbox_id，请保留 account/instant-mail/"
                ) from exc

    def mailbox_record(self, email):
        self.ensure_mailbox(email)
        return {"mail_provider": self.provider, "mailbox_id": self.accounts[email.lower()]}

    def _path(self, email):
        self.ensure_mailbox(email)
        return f"/api/email/{quote(self.accounts[email.lower()], safe='')}/messages"

    @staticmethod
    def _normalize(data, sequence, message_id):
        return {
            "emailId": sequence,
            "id": message_id,
            "sendEmail": _address(data.get("from") or data.get("fromText")),
            "toEmail": _address(data.get("to")),
            "subject": data.get("subject") or "",
            "text": data.get("text") or "",
            "content": data.get("html") or "",
        }

    def list_messages(self, email):
        with self._request_lock:
            data = self._request("GET", self._path(email), email=email)
            if not isinstance(data, list) or any(
                not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"]
                for row in data
            ):
                raise RuntimeError("Instant Mail 邮件列表格式错误")
            ids = self._message_ids.setdefault(email.lower(), {})
            # Provider IDs are opaque strings, not timestamps or sortable integers.
            for row in reversed(data):
                if row["id"] not in ids:
                    ids[row["id"]] = len(ids) + 1
            return [self._normalize(row, ids[row["id"]], row["id"]) for row in data]

    def _message_with_body(self, email, message):
        path = self._path(email) + "/" + quote(message["id"], safe="")
        detail = self._request("GET", path, email=email)
        if not isinstance(detail, dict) or detail.get("id", message["id"]) != message["id"]:
            raise RuntimeError("Instant Mail 邮件详情格式错误")
        normalized = self._normalize(detail, message["emailId"], message["id"])
        return {key: value or message.get(key, "") for key, value in normalized.items()}

    def close(self):
        self.session.close()
