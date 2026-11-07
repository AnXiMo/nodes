"""Registration, recovery and concurrent export workflows."""

import os
import random
import string
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

from . import api
from .browser import solve_turnstile
from .console import log, set_worker
from .instant_mail import InstantMail
from .providers import create_mail, mail_provider
from .storage import (
    account_emails,
    find_account,
    new_round_files,
    resume_proxy_file,
    save_account,
    save_proxies,
)


def complete_proxy_export(rec, session, node_file):
    current = api.whoami(session, rec["access_token"])
    rec["verified"] = current.get("EmailVerified") is True
    rec["userData"] = current
    if not rec["verified"]:
        raise RuntimeError("ProxyScrape 服务端尚未确认邮箱验证成功，未开通试用")
    log("正在查询当前账号的 Premium 试用状态并导出代理…")
    account_id, username, password, proxies = api.fetch_trial_proxies(session, rec["access_token"])
    save_proxies(username, password, proxies, node_file)
    rec.update(
        proxy_account_id=account_id,
        proxy_username=username,
        proxy_password=password,
        proxy_count=len(proxies),
    )
    rec.pop("error", None)
    log(f"已导出 {len(proxies)} 个代理，子账号 {account_id}")


def _redacted_error(exc, rec):
    detail = str(exc)
    for key in ("password", "access_token", "proxy_password", "mailbox_id"):
        value = rec.get(key)
        if isinstance(value, str) and value:
            detail = detail.replace(value, "***")
    return detail


def _relogin(session, rec, *, config=None, hidden=True, challenge_timeout=120):
    if not rec.get("password"):
        raise ValueError("账号没有保存登录密码；请用 --login EMAIL 提供原密码")
    try:
        token, user = api.login(session, rec["email"], rec["password"])
    except api.LoginChallengeRequired:
        challenge = os.environ.get("NODES_LOGIN_CHALLENGE_TOKEN", "")
        if not challenge:
            from .login_challenge import obtain

            challenge = obtain(config=config, hidden=hidden, timeout=challenge_timeout)
        token, user = api.login(session, rec["email"], rec["password"], challenge)
    rec.update(access_token=token, userData=user)


def resume_account(email, *, root=None, mail_timeout=180, config=None, hidden=True,
                   challenge_timeout=120, force_login=False, password=None):
    root = Path.cwd() if root is None else Path(root)
    try:
        path, rec = find_account(root, email)
    except ValueError:
        if not force_login or not password:
            raise
        path, _ = new_round_files(root)
        rec = {"email": email, "verified": False, "proxy_count": 0}
    if password:
        # The new password is saved only after the server accepts it.
        rec = {**rec, "password": password}
    node_file = resume_proxy_file(root)
    log(f"继续已保存的账号 {email}，账号文件 {path}")
    login_attempted = False
    login_accepted = False

    def relogin(session):
        nonlocal login_attempted, login_accepted
        if login_attempted:
            raise api.SessionExpired("重新登录后的会话仍不可用，已停止重试")
        login_attempted = True
        _relogin(session, rec, config=config, hidden=hidden,
                 challenge_timeout=challenge_timeout)
        login_accepted = True
        # Preserve the accepted password and new session even if export later fails.
        save_account(rec, path)

    try:
        with requests.Session() as session:
            session.headers.update(api.HEADERS)
            if force_login or not rec.get("access_token"):
                relogin(session)
                current = api.whoami(session, rec["access_token"])
            else:
                try:
                    current = api.whoami(session, rec["access_token"])
                except api.SessionExpired:
                    relogin(session)
                    current = api.whoami(session, rec["access_token"])
            rec.update(verified=current.get("EmailVerified") is True, userData=current)
            if rec.get("mail_provider") == "instant_mail":
                if current.get("EmailVerified") is not True:
                    mail = InstantMail(root=root)
                    try:
                        mail.restore_mailbox(email, rec.get("mailbox_id"))
                        after_id = max(
                            (row["emailId"] for row in mail.list_messages(email)), default=0
                        )
                        api.resend_code(session, rec["access_token"])
                        code = mail.wait_for_code(email, after_id=after_id, timeout=mail_timeout)
                        api.verify_email(session, rec["access_token"], code)
                    finally:
                        mail.close()
            try:
                complete_proxy_export(rec, session, node_file)
            except api.SessionExpired:
                relogin(session)
                complete_proxy_export(rec, session, node_file)
    except Exception as exc:
        detail = _redacted_error(exc, rec)
        if force_login and password and not login_accepted:
            log(f"[!] 登录未完成: {detail}")
            return False
        rec.update(error=detail, proxy_count=0)
        save_account(rec, path)
        log(f"[!] 账号 {email} 导出失败: {detail}")
        return False
    save_account(rec, path)
    log(f"代理文件 → {node_file}")
    return True


def resume_all(*, root=None, **kwargs):
    root = Path.cwd() if root is None else Path(root)
    emails = account_emails(root)
    if not emails:
        raise ValueError("没有可恢复的已保存账号，请先恢复账号备份")
    outcomes = [resume_account(email, root=root, **kwargs) for email in emails]
    log(f"账号恢复完成：{sum(outcomes)}/{len(outcomes)} 成功；失败记录及密码仍保留")
    return all(outcomes)


def _register_once(
    headless,
    node_file,
    config,
    mail,
    *,
    mail_timeout=180,
    challenge_timeout=120,
    account_file=None,
):
    """创建所选服务商邮箱，再执行注册、验证和代理导出流程。"""
    email = mail.new_address()
    mail.ensure_mailbox(email)
    log(f"{getattr(mail, 'display_name', 'Cloud Mail')} 邮箱: {email}")
    after_id = max((row["emailId"] for row in mail.list_messages(email)), default=0)
    password = "Ps" + "".join(random.choices(string.ascii_letters + string.digits, k=10)) + "!9"
    token = solve_turnstile(headless=headless, timeout=challenge_timeout, config=config)
    session, access_token, userdata = api.register(email, password, token)
    rec = {
        "email": email,
        "password": password,
        "access_token": access_token,
        "userData": userdata,
        "verified": False,
        "proxy_username": "",
        "proxy_password": "",
        "proxy_count": 0,
        "ts": int(time.time()),
    }
    try:
        if getattr(mail, "provider", None) == "instant_mail":
            rec.update(mail.mailbox_record(email))
        # Persist credentials for every provider before verification or export starts.
        if account_file is not None:
            save_account(rec, account_file)
        api.resend_code(session, access_token)
        log("等待新验证邮件…")
        code = mail.wait_for_code(email, after_id=after_id, timeout=mail_timeout)
        api.verify_email(session, access_token, code)
        complete_proxy_export(rec, session, node_file)
    except Exception as exc:
        rec["error"] = _redacted_error(exc, rec)
        log(f"[!] 账号 {email} 后续处理失败: {rec['error']}")
    finally:
        session.close()
    return rec


def register_one(idx, headless, acc_file, node_file, config, mail, max_attempts=3, **kwargs):
    """保留 Nodes 的账号级重试，每个已注册账号都保存，失败会写明原因。"""
    set_worker(idx)
    last = None
    for attempt in range(1, max_attempts + 1):
        if attempt > 1:
            log(f"—— 第 {attempt}/{max_attempts} 次尝试 ——")
        try:
            rec = _register_once(
                headless, node_file, config, mail, account_file=acc_file, **kwargs
            )
        except Exception as exc:
            log(f"[x] 本次尝试失败: {exc}")
            continue
        save_account(rec, acc_file)
        last = rec
        if rec["proxy_count"] > 0:
            log(f"[✓] 完成 {rec['email']} · 代理 {rec['proxy_count']} 个")
            return rec
        if attempt < max_attempts:
            log("已保存未完成账号，按 Nodes 的重试方式使用新邮箱再次尝试")
    log(f"[x] #{idx} 未完成代理导出，已尝试 {max_attempts} 次")
    return last


def run_round(count, threads, headless, config, *, root=None, **kwargs):
    mail = create_mail(config, root=root)
    try:
        if mail_provider(config) == "cloud_mail":
            mail._login()
        acc_file, node_file = new_round_files(Path.cwd() if root is None else root)
        t0 = time.time()
        ok = []
        with ThreadPoolExecutor(max_workers=threads) as ex:
            futs = [
                ex.submit(
                    register_one,
                    i + 1,
                    headless,
                    acc_file,
                    node_file,
                    config,
                    mail,
                    **kwargs,
                )
                for i in range(count)
            ]
            for fu in as_completed(futs):
                try:
                    r = fu.result()
                except Exception as e:
                    r = None
                    log(f"[worker error] {e}")
                if r and r.get("proxy_count", 0) > 0:
                    ok.append(r)

        dt = time.time() - t0
        total_proxies = sum(r.get("proxy_count", 0) for r in ok)
        print("\n" + "=" * 52)
        print(f"  完成 {len(ok)}/{count}  ·  用时 {dt:.0f}s  ·  代理共 {total_proxies} 个")
        print(f"  账号 → {acc_file}")
        print(f"  代理 → {node_file}")

        print("=" * 52 + "\n")
        return len(ok) == count
    finally:
        mail.session.close()
