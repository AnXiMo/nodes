import json

import pytest

from nodes.config import DEFAULT_EXTENSION, load_config


def test_mail_provider_environment_overrides_json(tmp_path):
    (tmp_path / "config.local.json").write_text('{"mail_provider":"cloud_mail"}')
    config = load_config(tmp_path, environ={"MAIL_PROVIDER": " instant_mail "})
    assert config["mail_provider"] == "instant_mail"


def test_environment_overrides_json_and_dotenv(tmp_path):
    (tmp_path / "config.local.json").write_text(
        json.dumps(
            {
                "cloud_mail_url": "https://json.example",
                "cloud_mail_email": "json@example.org",
                "cloud_mail_password": "json-secret",
            }
        )
    )
    (tmp_path / ".env").write_text('CLOUD_MAIL_PASSWORD="dotenv#secret"\n')
    config = load_config(tmp_path, environ={"CLOUD_MAIL_EMAIL": "env@example.org"})
    assert config["cloud_mail_url"] == "https://json.example"
    assert config["cloud_mail_email"] == "env@example.org"
    assert config["cloud_mail_password"] == "dotenv#secret"


def test_missing_config_is_allowed_until_run(tmp_path):
    config = load_config(tmp_path, environ={})
    assert config["cloud_mail_password"] == ""
    assert config["turnstile_extension_path"] == str(DEFAULT_EXTENSION)


@pytest.mark.parametrize("content", ["{", "[]"])
def test_invalid_json_config_is_reported_with_path(tmp_path, content):
    path = tmp_path / "config.local.json"
    path.write_text(content)
    with pytest.raises(ValueError, match="config.local.json"):
        load_config(tmp_path, environ={})


def test_relative_browser_and_extension_paths_are_project_relative(tmp_path):
    (tmp_path / "config.local.json").write_text(
        json.dumps(
            {
                "browser_path": "./bin/chromium",
                "turnstile_extension_path": "custom-extension",
            }
        )
    )
    config = load_config(tmp_path, environ={})
    assert config["browser_path"] == str(tmp_path / "bin/chromium")
    assert config["turnstile_extension_path"] == str(tmp_path / "custom-extension")


def test_resin_config_precedence_and_default_subscription(tmp_path):
    (tmp_path / "config.local.json").write_text(json.dumps({
        "resin_url": "https://json.example.org/",
        "resin_admin_token": "json-token",
        "resin_subscription_name": "json-sub",
    }))
    (tmp_path / ".env").write_text(
        'RESIN_ADMIN_TOKEN="dotenv#token"\nRESIN_SUBSCRIPTION_NAME=\n'
    )
    config = load_config(tmp_path, environ={"RESIN_URL": "https://env.example.org/"})
    assert config["resin_url"] == "https://env.example.org"
    assert config["resin_admin_token"] == "dotenv#token"
    assert config["resin_subscription_name"] == "nodes"
