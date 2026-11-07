"""Authenticated encryption of account records; the key is never stored with the vault."""

import json
import os
import re
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from .storage import _file_lock

MAGIC = b"NODES-ACCOUNT-VAULT-1\n"
MAX_BYTES = 32 * 1024 * 1024
KEY_ENV = "NODES_ACCOUNT_BACKUP_KEY"


def cipher(key=None):
    key = key if key is not None else os.environ.get(KEY_ENV, "")
    if not key:
        raise ValueError(f"缺少 {KEY_ENV}；请单独保管加密密钥")
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{KEY_ENV} 必须是 Fernet 生成的32字节密钥") from exc


def _allowed(name):
    return bool(re.fullmatch(r"account/(accounts_[A-Za-z0-9_.-]+\.jsonl|"
                             r"instant-mail/[a-f0-9]{64}\.json)", name))


def private_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = None
    try:
        fd, name = tempfile.mkstemp(prefix=".account-", dir=path.parent)
        temporary = Path(name)
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def pack(root, *, key=None):
    root = Path(root).resolve()
    files = {}
    with _file_lock:
        account_dir = root / "account"
        if account_dir.is_symlink():
            raise ValueError("账号目录不能是符号链接")
        for path in sorted(account_dir.rglob("*")):
            if path.is_symlink():
                raise ValueError("账号备份不能包含符号链接")
            if not path.is_file():
                continue
            name = path.relative_to(root).as_posix()
            if not _allowed(name):
                continue
            if path.stat().st_size > MAX_BYTES:
                raise ValueError("账号文件超过备份大小上限")
            text = path.read_bytes().decode("utf-8")
            for row in text.splitlines() if name.endswith(".jsonl") else [text]:
                if row.strip():
                    if not isinstance(json.loads(row), dict):
                        raise ValueError("账号备份记录必须是JSON对象")
            files[name] = text
    raw = json.dumps({"version": 1, "files": files}, ensure_ascii=False).encode("utf-8")
    if len(raw) > MAX_BYTES:
        raise ValueError("账号备份超过大小上限")
    return MAGIC + cipher(key).encrypt(raw)


def unpack(data, *, key=None):
    if len(data) > MAX_BYTES * 2 or not data.startswith(MAGIC):
        raise ValueError("不是受支持的账号加密备份")
    try:
        raw = cipher(key).decrypt(data[len(MAGIC):])
    except InvalidToken as exc:
        raise ValueError("账号备份密钥不匹配或文件已损坏；未恢复任何文件") from exc
    if len(raw) > MAX_BYTES:
        raise ValueError("账号备份超过大小上限")
    value = json.loads(raw)
    if (not isinstance(value, dict) or value.get("version") != 1
            or not isinstance(value.get("files"), dict)):
        raise ValueError("账号备份结构无效")
    files = value["files"]
    for name, text in files.items():
        if not isinstance(name, str) or not _allowed(name) or not isinstance(text, str):
            raise ValueError("账号备份包含非法路径或内容")
        for row in text.splitlines() if name.endswith(".jsonl") else [text]:
            if row.strip() and not isinstance(json.loads(row), dict):
                raise ValueError("账号备份记录必须是JSON对象")
    return files


def restore(data, root, *, key=None):
    root = Path(root).resolve()
    files = unpack(data, key=key)
    with _file_lock:
        # Validate every destination before writing even the first file.
        for name, text in files.items():
            path = root / name
            if not path.resolve().is_relative_to(root) or any(
                parent.is_symlink() for parent in (path, *path.parents) if parent != root.parent
            ):
                raise ValueError("账号恢复路径不安全")
            if path.exists() and path.read_bytes() != text.encode("utf-8"):
                raise ValueError("本地已有不同的账号文件；请先备份或恢复到空目录")
        for name, text in files.items():
            private_write(root / name, text.encode("utf-8"))
    return len(files)


def backup_file(root, destination):
    data = pack(root)
    private_write(destination, data)
    return len(unpack(data))
