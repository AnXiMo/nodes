"""ProxyScrape account and trial HTTP contracts."""

import json
from urllib.parse import urlsplit

import requests

from .console import log

# ProxyScrape dashboard
BASE_URL = "https://dashboard.proxyscrape.com"
PS_REGISTER = f"{BASE_URL}/v2/v4/account/auth/register"
PS_ME = f"{BASE_URL}/v2/v4/account/auth/me"
PS_VERIFY_EMAIL = f"{BASE_URL}/v2/v4/account/verify-email"
PS_RESEND = f"{BASE_URL}/v2/v4/account/reset-verification-code"
PS_SIGNUP_PAGE = f"{BASE_URL}/v2/sign-up"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
)


HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "User-Agent": UA,
    "Origin": BASE_URL,
    "Referer": PS_SIGNUP_PAGE,
}


def register(email, password, turnstile_token):
    s = requests.Session()
    s.headers.update(HEADERS)
    try:
        r = s.post(
            PS_REGISTER,
            data={
                "email": email,
                "password": password,
                "cf_turnstile_token": turnstile_token,
            },
            timeout=30,
        )
        log(f"注册响应 HTTP {r.status_code}")
        if not r.ok:
            try:
                body = r.json()
            except ValueError:
                body = None
            detail = "; ".join(
                f"{key}={json.dumps(body[key], ensure_ascii=False)}"
                for key in ("error", "errors", "message", "info")
                if isinstance(body, dict) and body.get(key)
            ) or "服务端未返回可用的 JSON 错误详情"
            for secret in (email, password, turnstile_token):
                if secret:
                    detail = detail.replace(secret, "***")
            raise RuntimeError(f"ProxyScrape 注册 HTTP {r.status_code}: {detail[:800]}")
        data = r.json()
        token = data.get("access_token")
        if not token:
            raise RuntimeError("注册未返回 access_token")
        log("注册成功，已取得访问凭据")
        return s, token, data.get("userData", {})
    except Exception:
        s.close()
        raise


def resend_code(session, access_token):
    """触发发送/重发验证码（注册后不会自动发，必须调一次）。"""
    r = session.post(PS_RESEND, headers={"Authorization": f"Bearer {access_token}"}, timeout=30)
    log(f"发送验证邮件 HTTP {r.status_code}")
    r.raise_for_status()


def verify_email(session, access_token, code):
    # 关键：字段名是 verificationCode（不是 code），且服务端要 multipart/form-data
    r = session.post(
        PS_VERIFY_EMAIL,
        headers={"Authorization": f"Bearer {access_token}"},
        files={"verificationCode": (None, code)},
        timeout=30,
    )
    log(f"验邮箱响应 HTTP {r.status_code}")
    r.raise_for_status()


def whoami(session, access_token):
    r = session.post(PS_ME, headers={"Authorization": f"Bearer {access_token}"}, timeout=30)
    r.raise_for_status()
    return r.json()


def fetch_trial_proxies(session, access_token):
    """Reuse the caller's session and return trial ID, credentials and endpoints."""

    def request(method, path, *, params=None):
        context = f"ProxyScrape {method} {path}"
        try:
            response = session.request(
                method,
                BASE_URL + path,
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=30,
                **({"params": params} if params is not None else {}),
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"{context} HTTP 未收到响应：{exc}") from exc
        context += f" HTTP {response.status_code}"
        try:
            body = response.json()
        except ValueError as exc:
            raise RuntimeError(f"{context}：响应不是 JSON") from exc
        if not isinstance(body, dict):
            raise RuntimeError(f"{context}：响应不是 JSON 对象")
        if not response.ok or body.get("success") is False:
            detail = "; ".join(
                f"{key}={body[key]}" for key in ("error", "message", "info") if body.get(key)
            )
            raise RuntimeError(f"{context}：{detail or '请求失败'}")
        return body, context

    body, context = request("GET", "/v2/v4/account/premium/trial-eligibility")
    claimed = body.get("claimed")
    if not isinstance(claimed, bool):
        raise RuntimeError(f"{context}：缺少布尔值 claimed")
    overview = None
    if not claimed:
        body, context = request("POST", "/v2/v4/account/premium/claim-trial")
        account_id = body.get("account_id")
        if not isinstance(account_id, str) or not account_id:
            raise RuntimeError(f"{context}：缺少 account_id；请重新查询试用状态确认领取结果")
    else:
        body, context = request("GET", "/v2/v4/account/subaccounts")
        data = body.get("data")
        accounts = data.get("subaccounts") if isinstance(data, dict) else None
        if not isinstance(accounts, list) or any(not isinstance(row, dict) for row in accounts):
            raise RuntimeError(f"{context}：缺少 data.subaccounts 数组")
        trials = []
        for account in accounts:
            if account.get("AccountType") not in {"premium", "datacenter_shared"}:
                continue
            account_id = account.get("AccountID")
            if not account_id:
                raise RuntimeError(f"{context}：子账号缺少 AccountID")
            candidate, candidate_context = request(
                "GET", f"/v2/v4/account/{account_id}/services/overview"
            )
            if not isinstance(candidate.get("data"), dict):
                raise RuntimeError(f"{candidate_context}：缺少 data 对象")
            if candidate["data"].get("is_trial") in (True, 1):
                trials.append((account_id, candidate, candidate_context))
        if len(trials) != 1:
            raise RuntimeError(f"{context}：账号已领取试用，但找到 {len(trials)} 个匹配试用子账号")
        account_id, overview, context = trials[0]

    if overview is None:
        overview, context = request("GET", f"/v2/v4/account/{account_id}/services/overview")
    data = overview.get("data")
    if not isinstance(data, dict):
        raise RuntimeError(f"{context}：缺少 data 对象")
    if data.get("is_trial") not in (True, 1):
        raise RuntimeError(f"{context}：选中的子账号不是 Premium 试用")
    if overview.get("status") != "valid":
        raise RuntimeError(f"{context}：试用当前不可用，status={overview.get('status')}")
    services = data.get("services")
    credentials = services.get("datacenter_shared") if isinstance(services, dict) else None
    if not isinstance(credentials, dict):
        credentials = {}
    username, password = credentials.get("proxy_username"), credentials.get("proxy_password")
    if not data.get("proxy_credentials_enabled") or not all(
        isinstance(value, str) and value for value in (username, password)
    ):
        raise RuntimeError(f"{context}：试用尚未提供完整的用户名密码认证配置")

    endpoints = []
    while True:
        body, context = request(
            "GET",
            f"/v2/v4/account/{account_id}/datacenter_shared/proxy-list",
            params={
                "format": "json",
                "type": "displayproxies",
                "protocol": "http",
                "start": len(endpoints),
                "length": 100,
            },
        )
        rows, total = body.get("data"), body.get("recordsFiltered")
        if not isinstance(rows, list) or type(total) is not int or total < 0:
            raise RuntimeError(f"{context}：缺少 data 数组或 recordsFiltered 总数")
        if not rows and len(endpoints) < total:
            raise RuntimeError(f"{context}：代理列表分页未返回剩余节点")
        for row in rows:
            if not isinstance(row, list) or len(row) < 4 or not isinstance(row[0], str):
                raise RuntimeError(f"{context}：节点格式必须是 [地址, 协议, 状态, 国家]")
            try:
                endpoint = urlsplit("//" + row[0])
                if not endpoint.hostname or not endpoint.port:
                    raise ValueError("missing host or port")
            except ValueError as exc:
                raise RuntimeError(f"{context}：代理节点缺少有效主机或端口：{row[0]}") from exc
            endpoints.append(row[0])
        if len(endpoints) >= total:
            if not endpoints:
                raise RuntimeError(f"{context}：试用代理列表为空")
            return account_id, username, password, endpoints
