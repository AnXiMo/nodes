"""Check configured mail and Resin endpoints without registering ProxyScrape accounts."""

from pathlib import Path

import requests

from nodes.config import load_config
from nodes.providers import create_mail, mail_provider
from nodes.resin import configured


def main():
    config = load_config(Path.cwd())
    configured(config, required=True)
    selected = mail_provider(config)
    with requests.Session() as session:
        response = session.get(
            config["resin_url"].rstrip("/") + "/api/v1/subscriptions",
            headers={"Authorization": f"Bearer {config['resin_admin_token']}"},
            timeout=30,
        )
        if response.status_code != 200:
            raise RuntimeError(f"Resin API HTTP {response.status_code}")
        body = response.json()
        if (
            not isinstance(body, dict)
            or not isinstance(body.get("items"), list)
            or type(body.get("total")) is not int
        ):
            raise RuntimeError("Resin subscription API response has an unexpected format")
        print(f"PASS: Resin authenticated API; {body['total']} subscriptions.")
    mail = create_mail(config)
    try:
        email = mail.new_address() if selected == "instant_mail" else config["cloud_mail_email"]
        rows = mail.list_messages(email)
        print(f"PASS: {selected} mailbox/list API; domain={email.rsplit('@', 1)[-1]}.")
        print(f"PASS: received {len(rows)} existing messages; no ProxyScrape account registered.")
    finally:
        mail.session.close()


if __name__ == "__main__":
    main()
