"""Cross-platform Chromium configuration, without launching a browser."""

import re
from pathlib import Path

import pytest

from nodes.browser import browser_options


@pytest.fixture
def linux_display(monkeypatch):
    monkeypatch.setattr("nodes.browser.platform.system", lambda: "Linux")
    monkeypatch.setenv("DISPLAY", ":99")
    monkeypatch.setattr(
        "nodes.browser.shutil.which",
        lambda name: "/usr/bin/chromium" if name == "chromium" else None,
    )


def test_linux_discovers_chromium_and_keeps_hidden_browser_headed(linux_display):
    options = browser_options(hidden=True)
    assert options.browser_path == "/usr/bin/chromium"
    assert not any(arg.startswith("--headless") for arg in options.arguments)
    assert "--window-position=-32000,-32000" in options.arguments
    assert Path(options.extensions[0]).name == "turnstile"
    assert options.is_auto_port


def test_linux_without_display_explains_virtual_display(monkeypatch):
    monkeypatch.setattr("nodes.browser.platform.system", lambda: "Linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    with pytest.raises(RuntimeError, match="DISPLAY.*Xvfb.*nodes"):
        browser_options()


def test_linux_without_browser_reports_installation_requirement(linux_display, monkeypatch):
    monkeypatch.setattr("nodes.browser.shutil.which", lambda name: None)
    with pytest.raises(RuntimeError, match="Chromium.*BROWSER_PATH"):
        browser_options()


def test_explicit_browser_path_overrides_discovery(linux_display):
    options = browser_options(browser_path="/opt/google/chrome/chrome")
    assert options.browser_path == "/opt/google/chrome/chrome"


def test_visible_browser_does_not_move_offscreen(linux_display):
    options = browser_options(hidden=False)
    assert not any(arg.startswith("--window-position") for arg in options.arguments)


def test_missing_extension_is_reported(linux_display, tmp_path):
    missing = tmp_path / "missing-extension"
    with pytest.raises(FileNotFoundError, match=re.escape(str(missing))):
        browser_options(extension_path=missing)


def test_extension_directory_requires_manifest(linux_display, tmp_path):
    with pytest.raises(FileNotFoundError, match="manifest.json"):
        browser_options(extension_path=tmp_path)


def test_non_linux_does_not_require_x_display(monkeypatch):
    monkeypatch.setattr("nodes.browser.platform.system", lambda: "Windows")
    monkeypatch.delenv("DISPLAY", raising=False)
    options = browser_options(hidden=True)
    assert not any(arg.startswith("--headless") for arg in options.arguments)
