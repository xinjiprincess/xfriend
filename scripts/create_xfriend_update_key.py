"""Create an Ed25519 update-signing key outside the source checkout."""

from __future__ import annotations

import argparse
import base64
import hashlib
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-key", type=Path, required=True)
    args = parser.parse_args()
    path: Path = args.private_key.resolve()
    if path.exists():
        parser.error("Refusing to overwrite an existing release key")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    private = Ed25519PrivateKey.generate()
    encoded = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    with path.open("xb") as stream:
        stream.write(encoded)
    path.chmod(0o600)
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    print("key_id:", hashlib.sha256(public).hexdigest()[:16])
    print("public_key_base64:", base64.b64encode(public).decode("ascii"))
    print("private_key_path:", path)


if __name__ == "__main__":
    main()
