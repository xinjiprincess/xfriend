"""Sign an immutable Xfriend update release using a private key outside the repo.

Input JSON follows desktop/internal/updates.Release, except each artifact also
has a local ``file`` path relative to --artifacts-root. This command writes the
public release envelope only; key generation and trust-root distribution are
separate release operations.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key


def sign_release(spec: dict[str, Any], root: Path, key: Ed25519PrivateKey, key_id: str) -> bytes:
    release = dict(spec)
    if release.get("format") != 1 or release.get("application") != "xfriend":
        raise ValueError("Unsupported release specification")
    artifacts = [release["installer"], *release.get("components", [])]
    completed: list[dict[str, Any]] = []
    for item in artifacts:
        name = item["file"]
        if not isinstance(name, str):
            raise ValueError("Unsafe artifact path")
        posix = PurePosixPath(name)
        if (
            not name
            or posix.is_absolute()
            or PureWindowsPath(name).drive
            or ".." in posix.parts
            or "\\" in name
        ):
            raise ValueError("Unsafe artifact path")
        source = root / name
        if (
            source.is_symlink()
            or not source.is_file()
            or not source.resolve().is_relative_to(root.resolve())
        ):
            raise ValueError("Artifact file missing or unsafe")
        digest = hashlib.sha256()
        size = 0
        with source.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
        if size == 0:
            raise ValueError("Empty artifact")
        completed.append(
            {"id": item["id"], "url": item["url"], "sha256": digest.hexdigest(), "size": size}
        )
    release["installer"] = completed[0]
    release["components"] = completed[1:]
    payload = json.dumps(release, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )
    envelope = {
        "key_id": key_id,
        "payload": base64.b64encode(payload).decode("ascii"),
        "signature": base64.b64encode(key.sign(payload)).decode("ascii"),
    }
    return (json.dumps(envelope, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--artifacts-root", type=Path, required=True)
    parser.add_argument("--private-key", type=Path, required=True)
    parser.add_argument("--key-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists; publish immutable metadata")
    key = load_pem_private_key(args.private_key.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        parser.error("An Ed25519 private key is required")
    spec = json.loads(args.release.read_text(encoding="utf-8"))
    signed = sign_release(spec, args.artifacts_root, key, args.key_id)
    args.output.write_bytes(signed)


if __name__ == "__main__":
    main()
