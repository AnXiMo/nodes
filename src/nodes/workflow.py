"""Registration, recovery and concurrent export workflows."""

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
from .storage import find_account, new_round_files, resume_proxy_file, save_account, save_proxies


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


def resume_account(email, *, root=None, mail_timeout=180):
    root = Path.cwd() if root is None else Path(root)
    path, rec = find_account(root, email)
    if not rec.get("access_token"):
        raise ValueError(f"账号文件 {path} 中没有 {email} 的访问凭据")
    node_file = resume_proxy_file(root)
    log(f"继续已保存的账号 {email}，账号文件 {path}")
    try:
        with requests.Session() as session:
            session.headers.update(api.HEADERS)
            if rec.get("mail_provider") == "instant_mail":
                current = api.whoami(session, rec["access_token"])
                rec.update(verified=current.get("EmailVerified") is True, userData=current)
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
            complete_proxy_export(rec, session, node_file)
    except Exception as exc:
        rec.update(error=str(exc), proxy_count=0)
        save_account(rec, path)
        log(f"[!] 账号 {email} 导出失败: {exc}")
        return False
    save_account(rec, path)
    log(f"代理文件 → {node_file}")
    return True


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
            # Preserve access/mailbox credentials even if verification is interrupted.
            if account_file is not None:
                save_account(rec, account_file)
        api.resend_code(session, access_token)
        log("等待新验证邮件…")
        code = mail.wait_for_code(email, after_id=after_id, timeout=mail_timeout)
        api.verify_email(session, access_token, code)
        complete_proxy_export(rec, session, node_file)
    except Exception as exc:
        rec["error"] = str(exc)
        log(f"[!] 账号 {email} 后续处理失败: {exc}")
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
