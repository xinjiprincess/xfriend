"""Online releases verify deployment against a trusted, already pinned public key."""

from __future__ import annotations

import base64
import hashlib
import importlib.util
from pathlib import Path

import httpx
import pytest

from octop.infra.licensing.service import PRODUCT, LicenseProfile

_spec = importlib.util.spec_from_file_location(
    "prepare_license_profile", Path(__file__).parents[2] / "scripts" / "prepare_license_profile.py"
)
assert _spec is not None and _spec.loader is not None
preflight = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(preflight)

KEY = base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip("=")
OTHER_KEY = base64.urlsafe_b64encode(bytes(reversed(range(32)))).decode().rstrip("=")
BASE_URL = "https://activation.example.test/xfriendmanager"


def profile() -> LicenseProfile:
    return LicenseProfile(server_url=BASE_URL, public_key=KEY)


def ready_payload() -> dict[str, object]:
    return {
        "status": "ready",
        "schema_version": 1,
        "public_key_sha256": hashlib.sha256(bytes(range(32))).hexdigest(),
    }


def public_payload() -> dict[str, object]:
    return {"product": PRODUCT, "algorithm": "Ed25519", "public_key": KEY}


def test_server_preflight_preserves_trusted_key_and_url_prefix() -> None:
    requests: list[str] = []
    config = profile()
    before = config.model_dump()

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        requests.append(str(request.url))
        payload = ready_payload() if request.url.path.endswith("/ready") else public_payload()
        return httpx.Response(
            200, json=payload, headers={"content-type": "application/json; charset=utf-8"}
        )

    preflight.verify_server(config, transport=httpx.MockTransport(respond))
    assert requests == [f"{BASE_URL}/ready", f"{BASE_URL}/api/v1/public-key"]
    assert config.model_dump() == before


@pytest.mark.parametrize("endpoint", ["/ready", "/api/v1/public-key"])
@pytest.mark.parametrize("failure", ["html", "redirect", "invalid_json", "json_array", "http_503"])
def test_server_preflight_rejects_wrong_http_responses(endpoint: str, failure: str) -> None:
    requests: list[str] = []
    private_response = "never-include-this-upstream-body"

    def respond(request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/xfriendmanager")
        requests.append(path)
        if path != endpoint:
            assert path == "/ready"
            return httpx.Response(200, json=ready_payload())
        if failure == "html":
            return httpx.Response(
                200, text=f"<html>{private_response}</html>", headers={"content-type": "text/html"}
            )
        if failure == "redirect":
            return httpx.Response(302, headers={"location": "https://other.example.test/"})
        if failure == "invalid_json":
            return httpx.Response(
                200, text=private_response, headers={"content-type": "application/json"}
            )
        if failure == "json_array":
            return httpx.Response(200, json=[private_response])
        return httpx.Response(503, text=private_response)

    with pytest.raises(ValueError) as raised:
        preflight.verify_server(profile(), transport=httpx.MockTransport(respond))
    assert private_response not in str(raised.value)
    assert requests == (["/ready"] if endpoint == "/ready" else ["/ready", endpoint])


@pytest.mark.parametrize(
    ("endpoint", "field", "value"),
    [
        ("/ready", "status", "not_ready"),
        ("/ready", "status", True),
        ("/ready", "public_key_sha256", "a" * 64),
        ("/api/v1/public-key", "product", "another-product"),
        ("/api/v1/public-key", "algorithm", "RSA"),
        ("/api/v1/public-key", "public_key", OTHER_KEY),
        ("/api/v1/public-key", "public_key", "not-a-valid-key!"),
        ("/api/v1/public-key", "public_key", None),
    ],
)
def test_server_preflight_rejects_unready_or_mismatched_identity(
    endpoint: str, field: str, value: object
) -> None:
    config = profile()

    def respond(request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/xfriendmanager")
        payload = ready_payload() if path == "/ready" else public_payload()
        if path == endpoint:
            payload[field] = value
        return httpx.Response(200, json=payload)

    with pytest.raises(ValueError):
        preflight.verify_server(config, transport=httpx.MockTransport(respond))
    assert config.public_key == KEY


def test_server_preflight_sanitizes_transport_error() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("private-proxy-details", request=request)

    with pytest.raises(ValueError, match="无法连接") as raised:
        preflight.verify_server(profile(), transport=httpx.MockTransport(respond))
    assert "private-proxy-details" not in str(raised.value)


@pytest.mark.parametrize(
    "config",
    [
        LicenseProfile(server_url="http://localhost:9097", public_key=KEY),
        LicenseProfile(server_url=BASE_URL, public_key="invalid-key"),
        LicenseProfile(mode="local_trial", trial_days=5),
    ],
)
def test_server_preflight_validates_profile_before_network(config: LicenseProfile) -> None:
    def unexpected_request(request: httpx.Request) -> httpx.Response:
        pytest.fail("Invalid release profiles must not make requests")

    with pytest.raises(ValueError):
        preflight.verify_server(config, transport=httpx.MockTransport(unexpected_request))


def test_local_trial_staging_never_opens_http_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unexpected_client(*args: object, **kwargs: object) -> httpx.Client:
        pytest.fail("Local trial staging must not access the network")

    monkeypatch.setattr(preflight.httpx, "Client", unexpected_client)
    output = tmp_path / "local-trial.json"
    preflight.main(["--local-trial-days", "5", "--output", str(output)])
    result = LicenseProfile.model_validate_json(output.read_text(encoding="utf-8"))
    assert result.mode == "local_trial" and result.trial_days == 5


def test_cli_failed_verification_does_not_write_release_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "trusted.json"
    source.write_text(profile().model_dump_json(), encoding="utf-8")
    output = tmp_path / "release.json"
    output.write_text("keep-existing-profile", encoding="utf-8")
    verify = preflight.verify_server

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, text="<html>private-body</html>", headers={"content-type": "text/html"}
        )

    def verify_mock(config: LicenseProfile) -> None:
        verify(config, transport=httpx.MockTransport(respond))

    monkeypatch.setattr(preflight, "verify_server", verify_mock)
    with pytest.raises(SystemExit) as raised:
        preflight.main(["--input", str(source), "--verify-server", "--output", str(output)])
    assert raised.value.code == 2
    assert output.read_text(encoding="utf-8") == "keep-existing-profile"
    assert "private-body" not in capsys.readouterr().err


def test_cli_rejects_local_trial_and_server_verification(tmp_path: Path) -> None:
    output = tmp_path / "release.json"
    with pytest.raises(SystemExit) as raised:
        preflight.main(["--local-trial-days", "5", "--verify-server", "--output", str(output)])
    assert raised.value.code == 2
    assert not output.exists()
