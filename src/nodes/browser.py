"""Headed Chromium configuration shared by desktop Linux and Docker."""

import json
import os
import platform
import shutil
import time
from pathlib import Path

from DrissionPage import ChromiumOptions

from .api import PS_LOGIN_PAGE, PS_SIGNUP_PAGE
from .config import DEFAULT_EXTENSION, load_config
from .console import log


def browser_options(hidden=False, browser_path="", extension_path=""):
    """Use a real browser window; Linux servers supply DISPLAY through Xvfb."""
    if platform.system() == "Linux" and not os.environ.get("DISPLAY"):
        raise RuntimeError(
            "Linux 未设置 DISPLAY，请安装 Xvfb 后通过 nodes 命令启动。"
        )

    extension = Path(extension_path or DEFAULT_EXTENSION).resolve()
    if not (extension / "manifest.json").is_file():
        raise FileNotFoundError(f"找不到 Turnstile 扩展 manifest.json: {extension}")

    if not browser_path and platform.system() == "Linux":
        browser_path = next(
            (
                path
                for name in (
                    "chromium",
                    "chromium-browser",
                    "google-chrome",
                    "google-chrome-stable",
                )
                if (path := shutil.which(name))
            ),
            "",
        )
        if not browser_path:
            raise RuntimeError("找不到 Chromium / Chrome，请安装 chromium 或配置 BROWSER_PATH。")

    options = ChromiumOptions()
    options.auto_port()
    options.headless(False)
    if browser_path:
        options.set_browser_path(browser_path)
    for flag in (
        "--no-first-run",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-background-networking",
        "--mute-audio",
        "--disable-gpu",
        "--window-size=1280,900",
    ):
        options.set_argument(flag)
    if hidden:
        options.set_argument("--window-position=-32000,-32000")
    options.add_extension(str(extension))
    return options


_FILL_JS = r"""
function setVal(el,val){
  var setter=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;
  setter.call(el,val);
  el.dispatchEvent(new Event('input',{bubbles:true}));
  el.dispatchEvent(new Event('change',{bubbles:true}));
}
var email=document.querySelector('input[type=email]');
var pwds=document.querySelectorAll('input[type=password]');
var chk=document.querySelector('input[type=checkbox]');
if(email) setVal(email,'warmup'+Date.now()+'@example.com');
if(pwds[0]) setVal(pwds[0],'Warmup123!9');
if(pwds[1]) setVal(pwds[1],'Warmup123!9');
if(chk&&!chk.checked) chk.click();
return 'filled';
"""

# 一次读全 widget 状态（供轮询检测用，不盲等）：页面/API/input/iframe/token
_STATE_JS = r"""
try{
  var inp=document.querySelector('input[name="cf-turnstile-response"]');
  var token=inp?String(inp.value||'').trim():'';
  if(!token && window.turnstile && typeof turnstile.getResponse==='function'){
    try{ token=String(turnstile.getResponse()||'').trim(); }catch(e){}
  }
  return JSON.stringify({
    ready: document.readyState==='complete',
    hasApi: !!(window.turnstile && typeof turnstile.render==='function'),
    hasInput: !!inp,
    tokenLen: token.length,
    token: token.length>=80 ? token : ''
  });
}catch(e){ return JSON.stringify({err:String(e)}); }
"""

_TURNSTILE_FRAME = 'css:iframe[src*="challenges.cloudflare.com"],iframe[src*="turnstile"]'



def _read_state(tab):
    """页面脚本读状态；浏览器库查找封闭 Shadow DOM 内的 iframe。"""
    state = json.loads(tab.run_js(_STATE_JS))
    if state.get("err"):
        raise RuntimeError(f"读取 Turnstile 页面状态失败: {state['err']}")
    state["hasIframe"] = bool(tab.ele(_TURNSTILE_FRAME, timeout=0))
    return state


def solve_turnstile(headless=False, timeout=120, config=None, *, page_url=PS_SIGNUP_PAGE):
    """等待站点原生验证组件，不创建重复 widget，超时保留实际阶段。"""
    from DrissionPage import Chromium
    from DrissionPage.errors import ContextLostError, ElementLostError

    config = config or load_config(Path.cwd())
    opts = browser_options(
        headless,
        config["browser_path"],
        config["turnstile_extension_path"],
    )
    browser = Chromium(opts)
    tab = browser.latest_tab
    deadline = time.monotonic() + timeout
    stage = "等待页面及 Turnstile API"
    last_stage = None
    last_error = None
    state = {}
    filled = False
    try:
        log("浏览器打开登录验证页…" if page_url == PS_LOGIN_PAGE else "浏览器打开 sign-up 页…")
        tab.get(page_url)
        while time.monotonic() < deadline:
            try:
                state = _read_state(tab)
                if state.get("token"):
                    log(f"Turnstile 通过，token 长度={state['tokenLen']}")
                    return state["token"]
                if page_url == PS_LOGIN_PAGE and state.get("ready") and not filled:
                    # The email form may load the challenge API only after it opens.
                    filled = bool(tab.run_js("""
const button = [...document.querySelectorAll('button')]
    .find(el => el.textContent.trim() === 'Continue with email');
if (button) { button.click(); return true; }
return false;
"""))
                    stage = "等待登录邮箱表单"
                elif not (state.get("ready") and state.get("hasApi")):
                    stage = "等待页面及 Turnstile API"
                elif not filled:
                    tab.run_js(_FILL_JS)
                    filled = True
                    stage = "等待验证组件挂载"
                elif not state.get("hasInput"):
                    stage = "等待 cf-turnstile-response 挂载"
                elif not state.get("hasIframe"):
                    stage = "等待站点原生验证 iframe"
                else:
                    stage = "等待验证结果"
                    iframe = tab.ele(_TURNSTILE_FRAME, timeout=0)
                    if iframe:
                        body = iframe.ele("tag:body", timeout=0)
                        root = body.shadow_root if body else None
                        button = root.ele("tag:input", timeout=0) if root else None
                        if button:
                            button.click()
            except (ContextLostError, ElementLostError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                log(f"验证页面更新，重新定位: {last_error}")
            if stage != last_stage:
                log(f"Turnstile 阶段: {stage}")
                last_stage = stage
            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(0.5, remaining))
        detail = (
            f"Turnstile 超时：阶段={stage}；"
            f"hasInput={bool(state.get('hasInput'))}；"
            f"hasIframe={bool(state.get('hasIframe'))}；"
            f"tokenLength={state.get('tokenLen', 0)}"
        )
        if last_error:
            detail += f"；最后页面异常={last_error}"
        raise TimeoutError(detail)
    finally:
        try:
            browser.quit()
        except Exception as exc:
            log(f"[!] 关闭浏览器失败: {exc}")
