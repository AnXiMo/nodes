"""The installed nodes command and local virtual-display startup."""

import argparse
import getpass
import os
import platform
import shutil
import sys
from pathlib import Path

from . import account_backup, resin, workflow
from .config import load_config
from .console import log
from .providers import create_mail, mail_provider


def relaunch_with_display(argv):
    if platform.system() != "Linux" or os.environ.get("DISPLAY"):
        return None
    xvfb = shutil.which("xvfb-run")
    if not xvfb:
        raise RuntimeError("Linux 未设置 DISPLAY，请先安装 chromium xvfb xauth。")
    os.execv(
        xvfb,
        [xvfb, "-a", "-s", "-screen 0 1280x900x24", sys.executable, "-m", "nodes", *argv],
    )


def _ask(prompt, default):
    try:
        v = input(prompt).strip()
    except EOFError:
        v = ""
    return v or default


def guide():
    print("=" * 52)
    print("   ProxyScrape 批量注册机  ·  本地打码走协议")
    print("   浏览器只出 Turnstile token，注册/收信/验证全走 HTTP")
    print("   邮箱: Instant Mail Gmail / Cloud Mail   账号→account/  代理→node/")
    print("=" * 52)
    try:
        count = int(_ask("① 注册数量        [默认 5，输 0 退出]: ", "5"))
    except ValueError:
        count = 5
    try:
        threads = int(_ask("② 并发线程        [默认 3]: ", "3"))
    except ValueError:
        threads = 3
    hl = _ask("③ 隐藏浏览器窗口   [Y/n]: ", "Y").lower()
    headless = not hl.startswith("n")
    threads = max(1, min(threads, count, 8))  # 并发上限 8，别把机器压垮
    print("-" * 52)
    print(f"  → 注册 {count} 个 · 并发 {threads} · {'隐藏窗口' if headless else '显示窗口'}")
    print("-" * 52)
    return count, threads, headless


def _positive_int(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("必须是正整数")
    return number


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(
        prog="nodes", description="Nodes · Instant Mail Gmail / Cloud Mail"
    )
    command = parser.add_mutually_exclusive_group()
    command.add_argument("--count", type=_positive_int, help="本轮注册数量；省略则进入交互模式")
    command.add_argument(
        "--resume", metavar="EMAIL", help="恢复账号导出；会话失效时使用已保存密码重登"
    )
    command.add_argument("--resume-all", action="store_true", help="逐个恢复所有已保存账号")
    command.add_argument(
        "--login", metavar="EMAIL", help="用原密码重新登录；从提示或环境变量读取密码"
    )
    command.add_argument("--backup-accounts", metavar="FILE", help="将账号及邮箱凭据加密备份到文件")
    command.add_argument(
        "--restore-accounts", metavar="FILE", help="恢复加密账号备份；不覆盖不同本地文件"
    )
    parser.add_argument("--threads", type=_positive_int, default=3, help="并发数，默认 3，最多 8")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--hide-browser", dest="hidden", action="store_true", help="隐藏浏览器窗口")
    mode.add_argument("--show-browser", dest="hidden", action="store_false", help="显示浏览器窗口")
    parser.set_defaults(hidden=True)
    parser.add_argument(
        "--attempts", type=_positive_int, default=3, help="每个任务最多尝试次数，默认 3"
    )
    parser.add_argument("--mail-timeout", type=_positive_int, default=180, help="收信超时秒数")
    parser.add_argument(
        "--challenge-timeout", type=_positive_int, default=120, help="浏览器验证超时秒数"
    )
    command.add_argument(
        "--check-mail", action="store_true", help="检查邮箱；Gmail 模式会创建并保存邮箱"
    )
    command.add_argument(
        "--sync-resin", action="store_true", help="将已有导出合并同步到 Resin，不注册新账号"
    )
    args = parser.parse_args(argv)
    try:
        if args.backup_accounts:
            count = account_backup.backup_file(Path.cwd(), args.backup_accounts)
            log(f"已加密备份 {count} 个账号文件")
            return 0
        if args.restore_accounts:
            count = account_backup.restore(Path(args.restore_accounts).read_bytes(), Path.cwd())
            log(f"已恢复 {count} 个账号文件")
            return 0
        config = load_config(Path.cwd())
        resin_enabled = False
        if not args.check_mail:
            resin_enabled = resin.configured(config, required=args.sync_resin)
        if args.sync_resin:
            resin.sync(config)
            return 0
        if args.resume or args.resume_all or args.login:
            options = {"mail_timeout": args.mail_timeout, "config": config,
                       "hidden": args.hidden, "challenge_timeout": args.challenge_timeout}
            if args.login:
                password = os.environ.get("NODES_ACCOUNT_PASSWORD", "")
                if not password and sys.stdin.isatty():
                    password = getpass.getpass("ProxyScrape 原账号密码：")
                if not password:
                    raise ValueError("请交互输入原密码，或设置 NODES_ACCOUNT_PASSWORD")
                success = workflow.resume_account(args.login, force_login=True,
                                                  password=password, **options)
            elif args.resume_all:
                success = workflow.resume_all(**options)
            else:
                success = workflow.resume_account(args.resume, **options)
            if resin_enabled and (success or args.resume_all):
                resin.sync(config, skip_empty=True)
            return 0 if success else 1
        selected = mail_provider(config)
        if args.check_mail:
            mail = create_mail(config)
            try:
                email = (
                    mail.new_address() if selected == "instant_mail" else config["cloud_mail_email"]
                )
                rows = mail.list_messages(email)
                label = "Instant Mail Gmail" if selected == "instant_mail" else "Cloud Mail"
                log(f"{label} 接口正常，当前返回 {len(rows)} 封邮件")
            finally:
                mail.session.close()
            return 0
        if not args.count and not sys.stdin.isatty():
            parser.error("非交互环境必须指定操作；使用 --help 查看注册、登录、恢复和备份命令")
        relaunch_with_display(argv)
        kwargs = {
            "max_attempts": args.attempts,
            "mail_timeout": args.mail_timeout,
            "challenge_timeout": args.challenge_timeout,
        }
        if args.count is not None:
            success = workflow.run_round(
                args.count,
                min(args.threads, args.count, 8),
                args.hidden,
                config,
                **kwargs,
            )
            if resin_enabled:
                resin.sync(config, skip_empty=True)
            return 0 if success else 1
        while True:
            count, threads, headless = guide()
            if count <= 0:
                return 0
            workflow.run_round(count, threads, headless, config, **kwargs)
            if resin_enabled:
                resin.sync(config, skip_empty=True)
    except KeyboardInterrupt:
        log("操作已中断")
        return 130
    except Exception as exc:
        log(f"[x] {exc}")
        return 1
