"""Voice references belong to their creator, never to a global provider."""

import io
import wave

from tests.support.auth import create_user


def sample_wav() -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(b"\0\0" * 2400)
    return output.getvalue()


async def test_voice_library_ownership_and_default_cleanup(env) -> None:
    client, _server, admin = env
    alice = await create_user(client, admin, username="voice-alice")
    await client.post(
        "/api/admin/voice/providers",
        headers=admin,
        json={
            "name": "mimo-library",
            "kind": "mimo",
            "capability": "tts",
            "api_key": "test-secret",
        },
    )
    data = sample_wav()
    created = await client.post(
        "/api/voice/library",
        headers=alice,
        data={"name": "my voice"},
        files={"audio": ("sample.wav", data, "audio/wav")},
    )
    assert created.status_code == 201
    voice_id = created.json()["id"]
    endpoint = f"/api/voice/library/{voice_id}"
    assert (await client.get("/api/voice/library", headers=admin)).json() == []
    assert (await client.get(endpoint + "/sample", headers=admin)).status_code == 404
    assert (await client.patch(endpoint, headers=admin, json={"name": "stolen"})).status_code == 404
    assert (await client.delete(endpoint, headers=admin)).status_code == 404
    assert (await client.get(endpoint + "/sample", headers=alice)).content == data
    renamed = await client.patch(endpoint, headers=alice, json={"name": "renamed"})
    assert renamed.json()["name"] == "renamed"
    assert (
        await client.put(
            "/api/voice/preferences",
            headers=alice,
            json={
                "tts": "mimo-library",
                "voice_id": f"library:{voice_id}",
            },
        )
    ).status_code == 200
    assert (await client.delete(endpoint, headers=alice)).status_code == 204
    assert (await client.get("/api/voice/preferences", headers=alice)).json()["voice_id"] == ""
    assert (await client.get(endpoint + "/sample", headers=alice)).status_code == 404
    assert (await client.get("/api/voice/library")).status_code == 401


async def test_voice_design_and_clone_do_not_modify_provider(env, monkeypatch) -> None:
    from octop.infra.voice import adapters

    client, server, admin = env
    alice = await create_user(client, admin, username="design-alice")
    await client.post(
        "/api/admin/voice/providers",
        headers=admin,
        json={
            "name": "mimo-design",
            "kind": "mimo",
            "capability": "tts",
            "api_key": "test-secret",
            "extra_json": '{"voice_description":"administrator style"}',
        },
    )
    models = []

    async def synthesize(row, text, **kwargs):
        extra = row.get_extra()
        models.append(extra["tts_model"])
        if extra["tts_model"].endswith("voiceclone"):
            assert extra["voice_sample"].startswith("data:audio/wav;base64,")
        else:
            assert extra["voice_description"] == "gentle"
        wav = bytearray(sample_wav())
        wav[4:8] = b"\xff" * 4
        wav[40:44] = b"\xff" * 4
        yield bytes(wav)

    monkeypatch.setattr(adapters, "synthesize_mimo", synthesize)
    response = await client.post(
        "/api/voice/library/design",
        headers=alice,
        json={
            "name": "designed",
            "provider": "mimo-design",
            "description": "gentle",
            "text": "Hello",
        },
    )
    assert response.status_code == 201
    voice_id = "library:" + response.json()["id"]
    speech = await client.post(
        "/api/voice/tts",
        headers=alice,
        json={
            "text": "new text",
            "provider": "mimo-design",
            "voice_id": voice_id,
        },
    )
    assert speech.status_code == 200
    assert speech.content == sample_wav()
    assert models == ["mimo-v2.5-tts-voicedesign", "mimo-v2.5-tts-voiceclone"]
    assert server.services.voice_provider_repo.get_by_name("mimo-design").get_extra() == {
        "voice_description": "administrator style",
    }
    denied = await client.post(
        "/api/voice/tts",
        headers=admin,
        json={
            "text": "new text",
            "provider": "mimo-design",
            "voice_id": voice_id,
        },
    )
    assert denied.status_code == 404


async def test_library_rejects_invalid_reference_and_foreign_selection(env) -> None:
    client, _server, admin = env
    result = await client.post(
        "/api/voice/library",
        headers=admin,
        data={"name": "bad"},
        files={"audio": ("sample.wav", b"not audio", "audio/wav")},
    )
    assert result.status_code == 400
    assert (await client.get("/api/voice/library", headers=admin)).json() == []
    result = await client.put(
        "/api/voice/preferences",
        headers=admin,
        json={
            "voice_id": "library:../../secret",
        },
    )
    assert result.status_code == 404
