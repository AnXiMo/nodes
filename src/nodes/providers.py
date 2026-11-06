"""Select a mail provider while accepting existing Cloud Mail configurations."""

from .instant_mail import InstantMail
from .mail import CloudMail

CLOUD_FIELDS = ("cloud_mail_url", "cloud_mail_email", "cloud_mail_password")


def mail_provider(config):
    selected = (config.get("mail_provider") or "").strip().lower()
    if not selected:
        selected = "cloud_mail" if any(config.get(key) for key in CLOUD_FIELDS) else "instant_mail"
    if selected not in {"instant_mail", "cloud_mail"}:
        raise ValueError("MAIL_PROVIDER 仅支持 instant_mail 或 cloud_mail")
    if selected == "cloud_mail":
        missing = [key.upper() for key in CLOUD_FIELDS if not config.get(key)]
        if missing:
            raise ValueError("缺少 Cloud Mail 配置: " + ", ".join(missing))
    return selected


def create_mail(config, *, root=None):
    if mail_provider(config) == "instant_mail":
        return InstantMail(root=root)
    return CloudMail(*(config[key] for key in CLOUD_FIELDS))
