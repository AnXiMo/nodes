import json
import os

import pytest
from cryptography.fernet import Fernet

from nodes import account_backup as backup
from nodes import cli, github_vault


@pytest.fixture
def account(tmp_path, monkeypatch):
    monkeypatch.setenv(backup.KEY_ENV, Fernet.generate_key().decode())
    path = tmp_path / "account/accounts_saved.jsonl"
    path.parent.mkdir()
    path.write_bytes(b'{"email":"saved@example.org","password":"fixture-password"}\r\n')
    mailbox = path.parent / "instant-mail" / ("a" * 64 + ".json")
    mailbox.parent.mkdir()
    mailbox.write_bytes(b'{"mailbox_id":"fixture-mailbox"}\n')
    return tmp_path, path, mailbox


def test_encrypted_roundtrip_is_byte_exact_and_private(account, tmp_path):
    root, path, mailbox = account
    data = backup.pack(root)
    assert all(value not in data for value in
               (b"fixture-password", b"saved@example.org", b"fixture-mailbox"))
    destination = tmp_path / "restored"
    assert backup.restore(data, destination) == 2
    for original in (path, mailbox):
        restored = destination / original.relative_to(root)
        assert restored.read_bytes() == original.read_bytes()
        if os.name != "nt":
            assert restored.stat().st_mode & 0o777 == 0o600
    assert backup.restore(data, root) == 2


@pytest.mark.parametrize("failure", ["wrong-key", "tamper"])
def test_invalid_backup_writes_nothing(account, monkeypatch, failure):
    root, _, _ = account
    data = backup.pack(root)
    if failure == "wrong-key":
        monkeypatch.setenv(backup.KEY_ENV, Fernet.generate_key().decode())
    else:
        data = data[:-4] + b"xxxx"
    with pytest.raises(ValueError, match="密钥不匹配或文件已损坏"):
        backup.restore(data, root / "invalid")
    assert not (root / "invalid").exists()


@pytest.mark.parametrize("name", ["../escape", "account/../../escape", "account/private.key",
                                 "C:/escape", "account\\accounts_saved.jsonl"])
def test_authenticated_but_unsafe_paths_are_rejected(account, name):
    root, _, _ = account
    payload = json.dumps({"version": 1, "files": {name: "{}"}}).encode()
    data = backup.MAGIC + backup.cipher().encrypt(payload)
    with pytest.raises(ValueError, match="非法路径"):
        backup.restore(data, root / "invalid")
    assert not (root / "invalid").exists()


def test_conflicting_restore_preflights_all_files(account):
    root, path, mailbox = account
    data = backup.pack(root)
    destination = root / "conflict"
    existing = destination / mailbox.relative_to(root)
    existing.parent.mkdir(parents=True)
    existing.write_text('{"mailbox_id":"different"}')
    with pytest.raises(ValueError, match="已有不同"):
        backup.restore(data, destination)
    assert not (destination / path.relative_to(root)).exists()
    assert existing.read_text() == '{"mailbox_id":"different"}'


def test_symlink_backup_is_rejected(account):
    root, path, _ = account
    link = path.parent / "accounts_link.jsonl"
    try:
        link.symlink_to(path)
    except OSError:
        pytest.skip("host does not allow creating symlinks")
    with pytest.raises(ValueError, match="符号链接"):
        backup.pack(root)


def test_backup_cli_requires_no_mail_or_resin(account, monkeypatch):
    root, _, _ = account
    monkeypatch.chdir(root)
    monkeypatch.setattr(cli, "load_config", lambda *a: pytest.fail("no services"))
    assert cli.main(["--backup-accounts", str(root / "test.vault")]) == 0
    monkeypatch.chdir(root.parent)
    assert cli.main(["--restore-accounts", str(root / "test.vault")]) == 0


class Remote:
    repository = "owner/nodes"

    def __init__(self):
        self.current = None
        self.commits = {}

    def head(self):
        return self.current

    def download(self, head):
        return self.commits[head]

    def upload(self, data, expected_head):
        if expected_head != self.current:
            raise RuntimeError("concurrent update")
        self.current = str(len(self.commits) + 1)
        self.commits[self.current] = data
        return self.current


def test_vault_durable_roundtrip_and_incremental_checkpoint(account, monkeypatch):
    root, path, _ = account
    remote = Remote()
    assert github_vault.restore(root, client=remote) == 0
    assert github_vault.save(root, client=remote) == 2
    pristine = path.read_bytes()
    with path.open("ab") as output:
        output.write(b'{"password":"second-fixture-password"}\n')
    assert github_vault.save(root, client=remote) == 2
    assert len(remote.commits) == 2
    assert github_vault.save(root, client=remote) == 2
    assert len(remote.commits) == 2  # no empty/identical commit
    restored = root / "next-run"
    github_vault.restore(restored, client=remote)
    assert (restored / path.relative_to(root)).read_bytes() == path.read_bytes()
    assert pristine in path.read_bytes()
    for data in remote.commits.values():
        assert b"fixture-password" not in data
    calls = []
    monkeypatch.setenv("NODES_ACCOUNT_BACKUP_GITHUB", "1")
    monkeypatch.setattr(github_vault, "save", lambda root: calls.append(root))
    from nodes.storage import save_account

    save_account({"email": "saved@example.org"}, path)
    assert calls == [root]


def test_save_fails_closed_without_successful_restore(account, monkeypatch):
    root, _, _ = account
    remote = Remote()
    with pytest.raises(ValueError, match="先成功恢复"):
        github_vault.save(root, client=remote)
    github_vault.restore(root, client=remote)
    github_vault.save(root, client=remote)
    monkeypatch.setenv(backup.KEY_ENV, Fernet.generate_key().decode())
    with pytest.raises(ValueError):
        github_vault.restore(root, client=remote)
    with pytest.raises(ValueError, match="先成功恢复"):
        github_vault.save(root, client=remote)
    assert len(remote.commits) == 1


@pytest.mark.parametrize("mutation", ["delete", "truncate", "mailbox"])
def test_vault_refuses_loss_of_old_credentials(account, mutation):
    root, path, mailbox = account
    remote = Remote()
    github_vault.restore(root, client=remote)
    github_vault.save(root, client=remote)
    if mutation == "delete":
        path.unlink()
    elif mutation == "truncate":
        path.write_bytes(b'{}\n')
    else:
        mailbox.write_bytes(b'{}\n')
    with pytest.raises(ValueError, match="缺失或覆盖"):
        github_vault.save(root, client=remote)
    assert len(remote.commits) == 1


def test_vault_rejects_concurrent_writer(account):
    root, path, _ = account
    remote = Remote()
    github_vault.restore(root, client=remote)
    github_vault.save(root, client=remote)
    remote.upload(backup.pack(root), remote.current)
    path.write_bytes(path.read_bytes() + b'{}\n')
    with pytest.raises(RuntimeError, match="concurrent"):
        github_vault.save(root, client=remote)
    assert len(remote.commits) == 2


def test_empty_vault_can_be_verified_before_account_creation(account):
    root, _, _ = account
    empty = root / "empty"
    remote = Remote()
    assert github_vault.restore(empty, client=remote) == 0
    assert github_vault.save(empty, client=remote) == 0
    assert len(remote.commits) == 1
    assert backup.unpack(remote.download(remote.head())) == {}
    assert github_vault.restore(root / "new-run", client=remote) == 0
    assert not (empty / "account").exists()
