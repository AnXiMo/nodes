import logging
import re
import secrets
import time
from email.utils import parseaddr
from html.parser import HTMLParser
from threading import RLock

import requests


class _MailText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"style", "script"}:
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in {"style", "script"}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def extract_code(message: dict) -> str | None:
    parser = _MailText()
    parser.feed(message.get("content") or "")
    text = " ".join([message.get("subject") or "", message.get("text") or "", *parser.parts])
    # 仅处理邮件文本，不从 URL、CSS 或 HTML 属性中猜验证码。
    contextual = re.search(
        r"(?:verification\s+code|verify\s+code|security\s+code|验证码)"
        r"\s*(?:is\s*)?[:：\-]?\s*([A-Z0-9]{10}|\d{4,8})(?!\w)",
        text,
        re.IGNORECASE,
    )
    if contextual:
        return contextual.group(1)
    candidates = set(re.findall(r"(?<![\w/])\d{4,8}(?![\w/])", text))
    return candidates.pop() if len(candidates) == 1 else None


class Mailbox:
    def _message_with_body(self, email, message):
        return message

    def wait_for_code(
        self,
        email: str,
        *,
        after_id: int,
        timeout: float = 180,
        poll_interval: float = 3,
    ) -> str:
        deadline = time.monotonic() + timeout
        while True:
            rows = sorted(self.list_messages(email), key=lambda row: row["emailId"], reverse=True)
            new_rows = [row for row in rows if row["emailId"] > after_id]
            matching = 0
            for message in new_rows:
                message = self._message_with_body(email, message)
                sender = parseaddr(message.get("sendEmail") or "")[1].lower()
                domain = sender.rpartition("@")[2]
                if (message.get("toEmail") or "").lower() != email.lower() or not (
                    domain == "proxyscrape.com" or domain.endswith(".proxyscrape.com")
                ):
                    continue
                matching += 1
                code = extract_code(message)
                if code:
                    return code
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if not new_rows:
                    reason = "未找到新邮件"
                elif not matching:
                    reason = "新邮件未匹配目标收件地址及 ProxyScrape 发件域名"
                else:
                    reason = "匹配邮件中未解析到验证码"
                raise TimeoutError(
                    f"等待 ProxyScrape 新验证码超时；{reason}。最后一次查询："
                    f"返回 {len(rows)} 封，新邮件 {len(new_rows)} 封，匹配 {matching} 封；"
                    f"基线编号 {after_id}"
                )
            time.sleep(min(poll_interval, remaining))


class CloudMail(Mailbox):
    provider = "cloud_mail"
    display_name = "Cloud Mail"

    def __init__(self, base_url: str, email: str, password: str, *, session=None):
        self.base_url = base_url.rstrip("/")
        self.email = email
        self.password = password
        self.token = None
        self.accounts = {}
        self.session = session or requests.Session()
        self._request_lock = RLock()

    def _send(self, method: str, path: str, **kwargs):
        send = self.session.post if method == "POST" else self.session.get
        headers = {"Authorization": self.token} if self.token else {}
        response = send(self.base_url + "/api" + path, headers=headers, timeout=30, **kwargs)
        if response.status_code == 401:
            return 401, None, f"HTTP 401 {response.reason or 'Unauthorized'}"
        response.raise_for_status()
        try:
            body = response.json()
        except requests.exceptions.JSONDecodeError as exc:
            raise RuntimeError(
                f"Cloud Mail {method} {path} 返回的不是 JSON，请检查 CLOUD_MAIL_URL"
            ) from exc
        if not isinstance(body, dict):
            raise RuntimeError(f"Cloud Mail {method} {path} 返回格式错误: {body}")
        return body.get("code"), body.get("data"), body.get("message")

    def _request(self, method: str, path: str, **kwargs):
        # 只串行化邮箱 HTTP 请求，使同一轮任务使用同一个可更新的登录结果。
        with self._request_lock:
            for attempt in range(2):
                code, data, message = self._send(method, path, **kwargs)
                if code == 200:
                    return data
                if code != 401 or path == "/login" or attempt == 1:
                    raise RuntimeError(f"Cloud Mail {method} {path} API 错误 {code}：{message}")
                logging.getLogger(__name__).warning(
                    "Cloud Mail 会话失效：%s %s；重新登录并重试当前请求一次",
                    method,
                    path,
                )
                self.token = None
                self._login()

    def _login(self) -> None:
        with self._request_lock:
            if not self.token:
                data = self._request(
                    "POST",
                    "/login",
                    json={"email": self.email, "password": self.password},
                )
                if not isinstance(data, dict) or not data.get("token"):
                    raise RuntimeError("Cloud Mail 登录没有返回 token")
                self.token = data["token"]

    def new_address(self) -> str:
        self._login()
        config = self._request("GET", "/setting/websiteConfig")
        domains = config.get("domainList") if isinstance(config, dict) else None
        if not domains or not isinstance(domains, list) or not isinstance(domains[0], str):
            raise RuntimeError("Cloud Mail 没有返回可用邮箱域名 domainList")
        # 和 Cloud Mail 网页一样使用第一个域名；随机前缀遵守站点的最小长度。
        length = max(24, int(config.get("minEmailPrefix") or 0))
        return f"{secrets.token_hex((length + 1) // 2)}{domains[0]}"

    def ensure_mailbox(self, email: str) -> None:
        if self._find_account(email) is not None:
            return
        data = self._request("POST", "/account/add", json={"email": email})
        if not isinstance(data, dict) or not isinstance(data.get("accountId"), int):
            raise RuntimeError("Cloud Mail 创建邮箱没有返回 accountId；重运行时会先查询邮箱")
        self.accounts[email.lower()] = data["accountId"]

    def _find_account(self, email: str) -> int | None:
        self._login()
        key = email.lower()
        if key in self.accounts:
            return self.accounts[key]
        params = {"accountId": 0, "size": 30}
        while True:
            rows = self._request("GET", "/account/list", params=params)
            if not isinstance(rows, list):
                raise RuntimeError("Cloud Mail 邮箱列表格式不符合 3.0.0 接口约定")
            for row in rows:
                self.accounts[row["email"].lower()] = row["accountId"]
            if key in self.accounts:
                return self.accounts[key]
            if len(rows) < 30:
                return None
            params.update(accountId=rows[-1]["accountId"], lastSort=rows[-1]["sort"])

    def _account_id(self, email: str) -> int:
        account_id = self._find_account(email)
        if account_id is None:
            raise ValueError(f"当前 Cloud Mail 账号下不存在邮箱 {email}")
        return account_id

    def list_messages(self, email: str) -> list[dict]:
        data = self._request(
            "GET",
            "/email/list",
            params={
                "accountId": self._account_id(email),
                "allReceive": 0,
                "emailId": 0,
                "timeSort": 0,
                "size": 50,
                "type": 0,
            },
        )
        rows = data.get("list") if isinstance(data, dict) else None
        if not isinstance(rows, list) or any(
            not isinstance(row, dict) or not isinstance(row.get("emailId"), int) for row in rows
        ):
            raise RuntimeError("Cloud Mail 邮件列表格式不符合接口约定")
        return rows
