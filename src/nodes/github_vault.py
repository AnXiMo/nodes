"""Keep the encrypted account vault on a dedicated, durable GitHub data branch."""

import argparse
import base64
import json
import os
import re
import threading
from pathlib import Path

import requests

from . import account_backup

BRANCH = "account-vault"
VAULT_FILE = "accounts.vault"
STATE_FILE = ".nodes-vault-state.json"
_lock = threading.RLock()


class GitHubVault:
    def __init__(self, repository=None, token=None, *, session=None):
        self.repository = repository or os.environ.get("GITHUB_REPOSITORY", "")
        token = token or os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN", "")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", self.repository) or not token:
            raise ValueError("GitHub备份需要 GITHUB_REPOSITORY 和 GITHUB_TOKEN")
        self.session = session or requests.Session()
        self.session.headers.update({"Authorization": "Bearer " + token,
                                     "Accept": "application/vnd.github+json",
                                     "X-GitHub-Api-Version": "2022-11-28"})

    def close(self):
        self.session.close()

    def request(self, method, path, body=None, *, missing_ok=False):
        try:
            response = self.session.request(
                method, "https://api.github.com/repos/" + self.repository + path,
                json=body, timeout=30,
            )
        except requests.RequestException as exc:
            raise RuntimeError("GitHub加密备份连接失败，账号仍保留在本地") from exc
        if missing_ok and response.status_code == 404:
            return None
        if not response.ok:
            raise RuntimeError(f"GitHub加密备份 {method} 请求失败 HTTP {response.status_code}")
        return response.json()

    def head(self):
        result = self.request("GET", "/git/ref/heads/" + BRANCH, missing_ok=True)
        if result is None:
            # 404 can also mean the caller cannot see the repository.
            self.request("GET", "")
            return None
        return result["object"]["sha"]

    def download(self, head):
        commit = self.request("GET", "/git/commits/" + head)
        tree = self.request("GET", "/git/trees/" + commit["tree"]["sha"])["tree"]
        if len(tree) != 1 or tree[0].get("path") != VAULT_FILE or tree[0].get("type") != "blob":
            raise ValueError("账号备份分支包含意外文件，已停止恢复")
        blob = self.request("GET", "/git/blobs/" + tree[0]["sha"])
        if blob.get("encoding") != "base64" or blob.get("size", 0) > account_backup.MAX_BYTES * 2:
            raise ValueError("GitHub账号备份格式或大小无效")
        return base64.b64decode(blob["content"])

    def upload(self, data, expected_head):
        if self.head() != expected_head:
            raise RuntimeError("账号备份已被其他运行更新；未覆盖远端，请重新恢复后再运行")
        blob = self.request("POST", "/git/blobs", {
            "content": base64.b64encode(data).decode("ascii"), "encoding": "base64",
        })
        tree = self.request("POST", "/git/trees", {"tree": [{
            "path": VAULT_FILE, "mode": "100644", "type": "blob", "sha": blob["sha"],
        }]})
        commit = self.request("POST", "/git/commits", {
            "message": "Save encrypted account records", "tree": tree["sha"],
            "parents": [expected_head] if expected_head else [],
        })
        if expected_head:
            self.request("PATCH", "/git/refs/heads/" + BRANCH,
                         {"sha": commit["sha"], "force": False})
        else:
            self.request("POST", "/git/refs",
                         {"ref": "refs/heads/" + BRANCH, "sha": commit["sha"]})
        return commit["sha"]


def restore(root, *, client=None):
    root = Path(root)
    # A failed restore must invalidate an older successful marker.
    (root / STATE_FILE).unlink(missing_ok=True)
    account_backup.cipher()  # Missing/wrong-shaped key fails before any account activity.
    own_client = client is None
    client = client or GitHubVault()
    try:
        head = client.head()
        count = account_backup.restore(client.download(head), root) if head else 0
        state = {"version": 1, "repository": client.repository, "head": head, "restored": True}
        account_backup.private_write(root / STATE_FILE, json.dumps(state).encode())
        return count
    finally:
        if own_client:
            client.close()


def save(root, *, client=None):
    root = Path(root)
    with _lock:
        try:
            state = json.loads((root / STATE_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError("必须先成功恢复GitHub账号备份，不能用空运行覆盖已有资料") from exc
        own_client = client is None
        client = client or GitHubVault()
        try:
            if not state.get("restored") or state.get("repository") != client.repository:
                raise ValueError("GitHub账号备份恢复状态不匹配")
            data = account_backup.pack(root)
            files = account_backup.unpack(data)
            if state["head"]:
                old = account_backup.unpack(client.download(state["head"]))
                # Never silently truncate history or replace a saved mailbox capability.
                for name, content in old.items():
                    current = files.get(name)
                    if current is None or (
                        not current.startswith(content) if name.endswith(".jsonl")
                        else current != content
                    ):
                        raise ValueError("账号备份缺失或覆盖了原有记录，已停止上传")
                if old == files:
                    return len(files)
            # Initialize an empty encrypted vault too, so configuration can be
            # verified before any account activity. An existing vault is never cleared.
            account_backup.private_write(root / VAULT_FILE, data)
            state["head"] = client.upload(data, state["head"])
            account_backup.private_write(root / STATE_FILE, json.dumps(state).encode())
            return len(files)
        finally:
            if own_client:
                client.close()


def checkpoint(root):
    """Persist after each account record, including partial verification/export failures."""
    if os.environ.get("NODES_ACCOUNT_BACKUP_GITHUB") == "1":
        save(root)


def main(argv=None):
    parser = argparse.ArgumentParser(description="加密保存或恢复既有账号资料")
    parser.add_argument("action", choices=("restore", "save"))
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    try:
        count = globals()[args.action](args.root)
    except Exception as exc:
        print(f"账号备份失败：{exc}")
        return 1
    print(f"账号加密备份 {args.action} 完成，{count} 个文件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
