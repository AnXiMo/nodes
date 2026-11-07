"""Run the provider's native login challenge, adding a display only when needed."""

import contextlib
import json
import os
import platform
import shutil
import subprocess
import sys

from .api import PS_LOGIN_PAGE
from .browser import solve_turnstile


def obtain(*, config=None, hidden=True, timeout=120):
    if platform.system() != "Linux" or os.environ.get("DISPLAY"):
        return solve_turnstile(headless=hidden, timeout=timeout, config=config,
                               page_url=PS_LOGIN_PAGE)
    xvfb = shutil.which("xvfb-run")
    if not xvfb:
        raise RuntimeError("登录验证需要浏览器；请先安装 chromium xvfb xauth")
    try:
        result = subprocess.run(
            [xvfb, "-a", "-s", "-screen 0 1280x900x24", sys.executable,
             "-m", "nodes.login_challenge"],
            input=json.dumps({"config": config, "hidden": hidden, "timeout": timeout}),
            text=True, encoding="utf-8", capture_output=True, timeout=timeout + 60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("登录浏览器验证启动失败或超时") from exc
    if result.returncode:
        # Child output may include a challenge token: never echo it into CI logs.
        raise RuntimeError("登录浏览器验证失败，请在本机使用 --show-browser 重试")
    try:
        token = json.loads(result.stdout)["token"]
        if not isinstance(token, str) or not token:
            raise ValueError("empty token")
        return token
    except (ValueError, KeyError, TypeError) as exc:
        raise RuntimeError("登录浏览器未返回有效的验证结果") from exc


def main():
    options = json.load(sys.stdin)
    try:
        with contextlib.redirect_stdout(sys.stderr):
            token = solve_turnstile(
                config=options.get("config"), headless=options["hidden"],
                timeout=options["timeout"], page_url=PS_LOGIN_PAGE,
            )
    except Exception:
        return 1
    print(json.dumps({"token": token}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
