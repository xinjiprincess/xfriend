"""Portable archives reject unsafe input and never expose credentials in plain packages."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile

import pytest
from pydantic import ValidationError

from octop.infra.transfer.archive import MAGIC, InvalidPackage, decode, encode, safe_path
from octop.infra.transfer.models import Expert, ModelRef, Package, Provider


def package(*, secret: bool = False) -> Package:
    return Package(
        package_id="sample",
        client_version="1.4.7",
        providers=[Provider(ref="text:one", name="模型", kind="openai", has_secret=secret)],
        experts=[
            Expert(
                ref="expert:one",
                name="专家",
                model=ModelRef(provider_ref="text:one", model_id="a/b"),
                files={"SOUL.md": "assets/one/SOUL.md"},
            )
        ],
    )


def raw_archive(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, value in files.items():
            archive.writestr(name, value)
    return buffer.getvalue()


def test_plain_roundtrip_preserves_unicode_and_model_slashes() -> None:
    result, files, secrets = decode(
        encode(package(), {"assets/one/SOUL.md": "你好".encode()}, {}, "")
    )
    assert result.experts[0].name == "专家"
    assert result.experts[0].model is not None
    assert result.experts[0].model.model_id == "a/b"
    assert files["assets/one/SOUL.md"].decode() == "你好"
    assert secrets == {}


def test_encryption_authenticates_file_password_and_header() -> None:
    data = encode(
        package(secret=True),
        {"assets/one/SOUL.md": b"prompt"},
        {"text:one": "private-api-key"},
        "migration",
    )
    assert data.startswith(MAGIC)
    assert b"private-api-key" not in data
    assert decode(data, "migration")[2] == {"text:one": "private-api-key"}
    for invalid, password in [(data, "wrong"), (data[:-1] + bytes([data[-1] ^ 1]), "migration")]:
        with pytest.raises(InvalidPackage):
            decode(invalid, password)


def test_plain_secrets_rejected_even_with_matching_hash() -> None:
    blob = b'{"text:one":"credential"}'
    metadata = package(secret=True).model_dump()
    metadata["checksums"] = {"secrets.json": hashlib.sha256(blob).hexdigest()}
    with pytest.raises(InvalidPackage):
        decode(raw_archive({"manifest.json": json.dumps(metadata).encode(), "secrets.json": blob}))


@pytest.mark.parametrize(
    "path",
    [
        "../x",
        "/x",
        "C:/x",
        "C:x",
        "\\\\server\\x",
        "x/../y",
        "x//y",
        "x/CON.txt",
        "x/NUL",
        "x/COM1",
        "x/file:secret",
        "x/a.",
        "x/a ",
        "x/\x00",
        "x\\y",
    ],
)
def test_cross_platform_unsafe_paths_rejected(path: str) -> None:
    with pytest.raises(InvalidPackage):
        safe_path(path)


def test_case_collisions_and_untracked_files_rejected() -> None:
    for files in [
        {"manifest.json": package().model_dump_json().encode(), "assets/A": b"a", "assets/a": b"b"},
        {"manifest.json": package().model_dump_json().encode(), "untracked": b"a"},
    ]:
        with pytest.raises(InvalidPackage):
            decode(raw_archive(files))


def test_checksum_tampering_rejected() -> None:
    metadata = package().model_dump()
    metadata["checksums"] = {"assets/one/SOUL.md": hashlib.sha256(b"expected").hexdigest()}
    with pytest.raises(InvalidPackage):
        decode(
            raw_archive(
                {"manifest.json": json.dumps(metadata).encode(), "assets/one/SOUL.md": b"modified"}
            )
        )


def test_unknown_format_and_duplicate_json_keys_rejected() -> None:
    for metadata in [b'{"format_version":99}', b'{"format": "xfriend-portable", "format":"other"}']:
        with pytest.raises(InvalidPackage):
            decode(raw_archive({"manifest.json": metadata}))


def test_team_cannot_reference_missing_or_nested_members() -> None:
    with pytest.raises(ValidationError):
        Package(
            package_id="x",
            client_version="x",
            experts=[
                Expert(
                    ref="expert:team",
                    name="team",
                    kind="team",
                    member_refs=["expert:a", "expert:b"],
                )
            ],
        )


def test_provider_url_cannot_embed_credentials_or_query() -> None:
    for url in [
        "https://user:secret@example.com/v1",
        "https://example.com?api_key=secret",
        "file:///etc/passwd",
    ]:
        with pytest.raises(ValidationError):
            Provider(ref="text:p", name="p", kind="openai", base_url=url)


def test_previous_format_without_team_overrides_is_readable() -> None:
    old = package().model_copy(update={"format_version": 1})
    metadata = old.model_dump()
    for expert in metadata["experts"]:
        expert.pop("member_models", None)
    blob = b"prompt"
    metadata["checksums"] = {"assets/one/SOUL.md": hashlib.sha256(blob).hexdigest()}
    restored, _, _ = decode(
        raw_archive({"manifest.json": json.dumps(metadata).encode(), "assets/one/SOUL.md": blob})
    )
    assert restored.format_version == 1
    assert restored.experts[0].member_models == {}


def test_team_model_assignment_must_reference_real_member_and_provider() -> None:
    first = Expert(ref="expert:a", name="a")
    second = Expert(ref="expert:b", name="b")
    provider = Provider(ref="text:p", name="p", kind="openai")
    for member, provider_ref in [("expert:missing", "text:p"), ("expert:a", "text:missing")]:
        team = Expert(
            ref="expert:t",
            name="t",
            kind="team",
            member_refs=[first.ref, second.ref],
            member_models={member: ModelRef(provider_ref=provider_ref, model_id="x")},
        )
        with pytest.raises(ValidationError):
            Package(
                package_id="x",
                client_version="x",
                experts=[first, second, team],
                providers=[provider],
            )
