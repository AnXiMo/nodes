import os
import subprocess
import sys

import pytest

from nodes import cli, providers, workflow

CONFIG = {
    "cloud_mail_url": "https://mail.example.org",
    "cloud_mail_email": "owner@example.org",
    "cloud_mail_password": "secret",
}


def test_batch_cli_does_not_prompt_and_propagates_failure(monkeypatch):
    monkeypatch.setattr(cli, "load_config", lambda path: CONFIG)
    monkeypatch.setattr(cli, "relaunch_with_display", lambda argv: None)
    seen = []

    def run(count, threads, hidden, config, **kwargs):
        seen.append((count, threads, hidden, config, kwargs))
        return False

    monkeypatch.setattr(workflow, "run_round", run)
    assert cli.main(["--count", "2", "--threads", "1", "--hide-browser", "--attempts", "1"]) == 1
    assert seen[0][:3] == (2, 1, True)
    assert seen[0][4]["max_attempts"] == 1


@pytest.mark.parametrize(
    "args",
    [
        ["--count", "0"],
        ["--count", "-1"],
        ["--count", "1", "--threads", "0"],
        ["--count", "1", "--attempts", "0"],
        ["--count", "1", "--mail-timeout", "0"],
    ],
)
def test_invalid_options_fail_before_side_effects(args):
    with pytest.raises(SystemExit) as error:
        cli.main(args)
    assert error.value.code == 2


def test_help_works_without_credentials(capsys):
    with pytest.raises(SystemExit) as error:
        cli.main(["--help"])
    assert error.value.code == 0
    assert "--count" in capsys.readouterr().out


def test_linux_without_display_replaces_process_and_keeps_child_exit_status(tmp_path):
    xvfb = tmp_path / "xvfb-run"
    xvfb.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\nexit 23\n')
    xvfb.chmod(0o755)
    args = ["--count", "2", "--threads", "1", "--attempts", "1"]
    environment = {
        **os.environ,
        "DISPLAY": "",
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "CLOUD_MAIL_URL": CONFIG["cloud_mail_url"],
        "CLOUD_MAIL_EMAIL": CONFIG["cloud_mail_email"],
        "CLOUD_MAIL_PASSWORD": CONFIG["cloud_mail_password"],
    }
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            'from nodes import cli; cli.platform.system = lambda: "Linux"; '
            "raise SystemExit(cli.main())",
            *args,
        ],
        env=environment,
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 23, result.stderr
    assert result.stdout.splitlines() == [
        "-a",
        "-s",
        "-screen 0 1280x900x24",
        sys.executable,
        "-m",
        "nodes",
        *args,
    ]


@pytest.mark.parametrize("system,display", [("Linux", ":99"), ("Darwin", ""), ("Windows", "")])
def test_existing_display_and_desktop_platforms_do_not_spawn_xvfb(monkeypatch, system, display):
    monkeypatch.setattr(cli.platform, "system", lambda: system)
    monkeypatch.setenv("DISPLAY", display)
    monkeypatch.setattr(cli.os, "execv", lambda *a: pytest.fail("must not relaunch"))
    assert cli.relaunch_with_display(["--count", "1"]) is None


def test_linux_missing_xvfb_fails_before_registering(monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_config", lambda path: CONFIG)
    monkeypatch.setattr(cli.platform, "system", lambda: "Linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    monkeypatch.setattr(workflow, "run_round", lambda *a, **kw: pytest.fail("must not register"))
    assert cli.main(["--count", "1"]) == 1
    assert "chromium xvfb xauth" in capsys.readouterr().out


def test_check_mail_uses_working_directory_config_without_a_display(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for key in ("CLOUD_MAIL_URL", "CLOUD_MAIL_EMAIL", "CLOUD_MAIL_PASSWORD"):
        monkeypatch.delenv(key, raising=False)
    (tmp_path / ".env").write_text(
        "CLOUD_MAIL_URL=https://mail.example.org\n"
        "CLOUD_MAIL_EMAIL=owner@example.org\nCLOUD_MAIL_PASSWORD=secret\n"
    )
    monkeypatch.setattr(cli, "relaunch_with_display", lambda *a: pytest.fail("must not relaunch"))
    events = []

    class Mail:
        def __init__(self, url, email, password):
            assert (url, email, password) == tuple(CONFIG.values())
            self.session = self

        def list_messages(self, email):
            events.append(email)
            return []

        def close(self):
            events.append("closed")

    monkeypatch.setattr(providers, "CloudMail", Mail)
    assert cli.main(["--check-mail"]) == 0
    assert events == ["owner@example.org", "closed"]
    assert not (tmp_path / "account").exists()
    assert not (tmp_path / "node").exists()


def test_sync_resin_needs_no_mail_browser_or_registration(monkeypatch):
    config = {"resin_url": "https://resin.example.org", "resin_admin_token": "admin"}
    monkeypatch.setattr(cli, "load_config", lambda path: config)

    def unexpected(*args, **kwargs):
        pytest.fail("Sync must not access mailbox, browser or registration")

    monkeypatch.setattr(providers, "CloudMail", unexpected)
    monkeypatch.setattr(cli, "relaunch_with_display", unexpected)
    monkeypatch.setattr(workflow, "run_round", unexpected)
    monkeypatch.setattr(workflow, "resume_account", unexpected)
    calls = []
    monkeypatch.setattr(cli.resin, "sync", lambda config: calls.append(config))
    assert cli.main(["--sync-resin"]) == 0
    assert calls == [config]


@pytest.mark.parametrize("result", [True, False])
def test_batch_syncs_once_after_all_exports_including_partial_success(monkeypatch, result):
    config = {**CONFIG, "resin_url": "https://resin.example.org", "resin_admin_token": "admin"}
    monkeypatch.setattr(cli, "load_config", lambda path: config)
    monkeypatch.setattr(cli, "relaunch_with_display", lambda argv: None)
    events = []

    def run(*args, **kwargs):
        events.append("exports complete")
        return result

    def sync(config, **kwargs):
        assert kwargs == {"skip_empty": True}
        events.append("sync")

    monkeypatch.setattr(workflow, "run_round", run)
    monkeypatch.setattr(cli.resin, "sync", sync)
    assert cli.main(["--count", "3"]) == (0 if result else 1)
    assert events == ["exports complete", "sync"]


@pytest.mark.parametrize("config,args", [
    ({}, ["--sync-resin"]),
    ({**CONFIG, "resin_url": "https://resin.example.org"}, ["--count", "1"]),
    ({**CONFIG, "resin_admin_token": "admin"}, ["--count", "1"]),
])
def test_missing_resin_config_fails_before_external_operations(monkeypatch, config, args, capsys):
    monkeypatch.setattr(cli, "load_config", lambda path: config)
    monkeypatch.setattr(
        workflow, "run_round", lambda *args, **kwargs: pytest.fail("Must not register")
    )
    monkeypatch.setattr(
        cli.resin, "sync", lambda *args, **kwargs: pytest.fail("Must not sync")
    )
    assert cli.main(args) == 1
    assert "缺少 Resin 配置" in capsys.readouterr().out


@pytest.mark.parametrize("other", [["--count", "1"], ["--resume", "a@b.org"], ["--check-mail"]])
def test_sync_resin_is_mutually_exclusive_with_other_commands(other):
    with pytest.raises(SystemExit) as error:
        cli.main(["--sync-resin", *other])
    assert error.value.code == 2
