"""Bounded archive handling and authenticated whole-package encryption."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import stat
import unicodedata
import zipfile
from pathlib import PurePosixPath
from typing import Any

from octop.infra.transfer.models import Package

MAX_FILE = 8 * 1024 * 1024
MAX_TOTAL = 64 * 1024 * 1024
MAX_ARCHIVE = 72 * 1024 * 1024
MAX_FILES = 4000
MAGIC = b"XFPACK-ENC1\n"
PARAMS = {"kdf": "argon2id", "memory_kib": 65536, "iterations": 3, "parallelism": 1}


class InvalidPackage(ValueError):
    """An invalid, unsupported or unauthenticated package (no sensitive details)."""


def safe_path(value: str) -> str:
    if not value or "\\" in value or "\x00" in value:
        raise InvalidPackage
    parts = value.split("/")
    if PurePosixPath(value).is_absolute() or any(p in {"", ".", ".."} for p in parts):
        raise InvalidPackage
    for part in parts:
        if part.endswith((" ", ".")) or any(ord(c) < 32 or c in ':<>"|?*' for c in part):
            raise InvalidPackage
        if re.fullmatch(r"(?i)(con|prn|aux|nul|com[0-9]|lpt[0-9])(?:\..*)?", part):
            raise InvalidPackage
    if len(value) > 240:
        raise InvalidPackage
    return value


def _json(data: bytes) -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in pairs:
            if key in out:
                raise InvalidPackage
            out[key] = value
        return out

    return json.loads(data.decode("utf-8"), object_pairs_hook=unique)


def encrypt(data: bytes, password: str) -> bytes:
    from argon2.low_level import Type, hash_secret_raw
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if not password:
        raise InvalidPackage
    salt, nonce = os.urandom(16), os.urandom(12)
    header = json.dumps(
        {**PARAMS, "salt": salt.hex(), "nonce": nonce.hex()}, sort_keys=True
    ).encode()
    aad = MAGIC + len(header).to_bytes(4, "big") + header
    key = hash_secret_raw(password.encode(), salt, 3, 65536, 1, 32, Type.ID)
    return aad + AESGCM(key).encrypt(nonce, data, aad)


def decrypt(data: bytes, password: str) -> bytes:
    from argon2.low_level import Type, hash_secret_raw
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    n = len(MAGIC)
    length = int.from_bytes(data[n : n + 4], "big")
    if not 1 <= length <= 512 or not password:
        raise InvalidPackage
    header = _json(data[n + 4 : n + 4 + length])
    if not isinstance(header, dict) or set(header) != set(PARAMS) | {"salt", "nonce"}:
        raise InvalidPackage
    if any(header.get(k) != v for k, v in PARAMS.items()):
        raise InvalidPackage
    salt, nonce = bytes.fromhex(header["salt"]), bytes.fromhex(header["nonce"])
    if len(salt) != 16 or len(nonce) != 12:
        raise InvalidPackage
    aad = data[: n + 4 + length]
    key = hash_secret_raw(password.encode(), salt, 3, 65536, 1, 32, Type.ID)
    return AESGCM(key).decrypt(nonce, data[len(aad) :], aad)


def encode(
    package: Package, files: dict[str, bytes], secrets: dict[str, str], password: str
) -> bytes:
    contents = dict(files)
    if secrets:
        if not password:
            raise InvalidPackage
        contents["secrets.json"] = json.dumps(secrets).encode()
    if len(contents) >= MAX_FILES or any(len(b) > MAX_FILE for b in contents.values()):
        raise InvalidPackage
    if sum(map(len, contents.values())) > MAX_TOTAL - MAX_FILE:
        raise InvalidPackage
    checksums = {safe_path(p): hashlib.sha256(b).hexdigest() for p, b in contents.items()}
    metadata = package.model_copy(update={"checksums": checksums}).model_dump_json().encode()
    if len(metadata) > MAX_FILE:
        raise InvalidPackage
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", metadata)
        for path, content in contents.items():
            archive.writestr(path, content)
    data = buffer.getvalue()
    return encrypt(data, password) if password else data


def decode(data: bytes, password: str = "") -> tuple[Package, dict[str, bytes], dict[str, str]]:
    """Validate completely in memory. Never extract paths or execute/fetch anything."""
    try:
        if len(data) > MAX_ARCHIVE:
            raise InvalidPackage
        encrypted = data.startswith(MAGIC)
        if encrypted:
            data = decrypt(data, password)
        contents: dict[str, bytes] = {}
        names: set[str] = set()
        total = 0
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if len(archive.infolist()) > MAX_FILES:
                raise InvalidPackage
            for item in archive.infolist():
                path = safe_path(item.filename)
                normalized = unicodedata.normalize("NFC", path).casefold()
                if normalized in names or item.is_dir() or stat.S_ISLNK(item.external_attr >> 16):
                    raise InvalidPackage
                names.add(normalized)
                if item.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                    raise InvalidPackage
                if item.file_size > MAX_FILE or item.flag_bits & 1:
                    raise InvalidPackage
                if item.file_size > max(1024 * 1024, item.compress_size * 500):
                    raise InvalidPackage
                with archive.open(item) as stream:
                    blob = stream.read(MAX_FILE + 1)
                total += len(blob)
                if len(blob) > MAX_FILE or total > MAX_TOTAL:
                    raise InvalidPackage
                contents[path] = blob
        package = Package.model_validate(_json(contents.pop("manifest.json")))
        if set(contents) != set(package.checksums):
            raise InvalidPackage
        for path, blob in contents.items():
            if hashlib.sha256(blob).hexdigest() != package.checksums[path]:
                raise InvalidPackage
        secret_blob = contents.pop("secrets.json", None)
        if secret_blob is not None and not encrypted:
            raise InvalidPackage
        secrets = _json(secret_blob) if secret_blob else {}
        providers = {p.ref for p in package.providers if p.has_secret}
        if not isinstance(secrets, dict) or set(secrets) != providers:
            raise InvalidPackage
        if any(not isinstance(v, str) or len(v) > 100000 for v in secrets.values()):
            raise InvalidPackage
        used: set[str] = set()
        for expert in package.experts:
            for relative, path in expert.files.items():
                safe_path(relative)
                if relative not in {
                    "SOUL.md",
                    "IDENTITY.md",
                    "AGENTS.md",
                    "TOOLS.md",
                    "HEARTBEAT.md",
                } and (not relative.startswith("skills/") or len(relative.split("/")) < 3):
                    raise InvalidPackage
                if path not in contents or not path.startswith("assets/"):
                    raise InvalidPackage
                used.add(path)
            if expert.avatar_file:
                if expert.avatar_file not in contents:
                    raise InvalidPackage
                used.add(expert.avatar_file)
        if used != set(contents):
            raise InvalidPackage
        return package, contents, secrets
    except Exception as exc:
        # Never put validation input (possibly credentials) in errors or API logs.
        raise InvalidPackage from exc
