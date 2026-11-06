"""Offline regression: detect a challenge iframe inside a closed shadow root."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from DrissionPage import Chromium

from nodes.browser import _read_state, browser_options
from nodes.config import load_config

PAGE = b"""<!doctype html><title>Closed shadow challenge fixture</title>
<input type="hidden" name="cf-turnstile-response" value="">
<div id="challenge-host"></div>
<script>
window.turnstile = {
  render() { throw new Error('The existing fixture must not be rendered again'); },
  getResponse() { return ''; }
};
const shadow = document.querySelector('#challenge-host').attachShadow({mode: 'closed'});
const frame = document.createElement('iframe');
frame.src = '/turnstile-frame';
shadow.appendChild(frame);
</script>"""
FRAME = b"<!doctype html><title>Local challenge frame</title><p>No external challenge</p>"
LOCATOR = 'css:iframe[src*="challenges.cloudflare.com"],iframe[src*="turnstile"]'


class FixturePage(BaseHTTPRequestHandler):
    def do_GET(self):
        body = FRAME if self.path == "/turnstile-frame" else PAGE
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), FixturePage)
    Thread(target=server.serve_forever, daemon=True).start()
    browser = None
    try:
        config = load_config(Path.cwd())
        browser = Chromium(browser_options(hidden=True, browser_path=config["browser_path"]))
        tab = browser.latest_tab
        tab.get(f"http://127.0.0.1:{server.server_port}/")
        assert tab.title == "Closed shadow challenge fixture"
        assert tab.run_js("return document.querySelector('#challenge-host').shadowRoot") is None
        assert (
            tab.run_js("return document.querySelector('iframe[src*=turnstile]') !== null") is False
        )
        frame = tab.ele(LOCATOR, timeout=0)
        assert frame, "DrissionPage element lookup did not pierce the closed shadow root"
        assert frame.attr("src").endswith("/turnstile-frame")
        print(
            "PASS: tab.ele(css:iframe..., timeout=0) detects the closed-shadow iframe.", flush=True
        )
        state = _read_state(tab)
        assert state["ready"] is True, state
        assert state["hasApi"] is True, state
        assert state["hasInput"] is True, state
        assert state["hasIframe"] is True, state
        assert state["token"] == "", state
        print("PASS: _read_state detects the existing iframe without page querySelector access.")
    finally:
        if browser is not None:
            browser.quit()
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
