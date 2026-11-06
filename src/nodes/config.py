"""Nodes 本地配置。环境变量覆盖 .env，.env 覆盖 JSON 配置。"""

import json
import os
from pathlib import Path

from dotenv import dotenv_values

DEFAULT_EXTENSION = Path(__file__).parent / "resources" / "turnstile"

FIELDS = {
    "mail_provider": "MAIL_PROVIDER",
    "cloud_mail_url": "CLOUD_MAIL_URL",
    "cloud_mail_email": "CLOUD_MAIL_EMAIL",
    "cloud_mail_password": "CLOUD_MAIL_PASSWORD",
    "browser_path": "BROWSER_PATH",
    "turnstile_extension_path": "TURNSTILE_EXTENSION_PATH",
    "resin_url": "RESIN_URL",
    "resin_admin_token": "RESIN_ADMIN_TOKEN",
    "resin_subscription_name": "RESIN_SUBSCRIPTION_NAME",
}


def load_config(base_dir, *, environ=None):
    base_dir = Path(base_dir)
    path = base_dir / "config.local.json"
    config = {}
    if path.exists():
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"读取 {path} 失败: {exc}") from exc
        if not isinstance(config, dict):
            raise ValueError(f"{path} 必须是 JSON 对象")
    env = {**dotenv_values(base_dir / ".env"), **(os.environ if environ is None else environ)}
    result = {
        field: str(env.get(variable, config.get(field, "")) or "")
        for field, variable in FIELDS.items()
    }
    for field in FIELDS.keys() - {"cloud_mail_password", "resin_admin_token"}:
        result[field] = result[field].strip()
    result["cloud_mail_url"] = result["cloud_mail_url"].rstrip("/")
    result["resin_url"] = result["resin_url"].rstrip("/")
    result["resin_subscription_name"] = result["resin_subscription_name"] or "nodes"
    for field in ("browser_path", "turnstile_extension_path"):
        value = result[field]
        if value and (field == "turnstile_extension_path" or "/" in value or "\\" in value):
            result[field] = str((base_dir / Path(value).expanduser()).resolve())
    if not result["turnstile_extension_path"]:
        result["turnstile_extension_path"] = str(DEFAULT_EXTENSION)
    return result
