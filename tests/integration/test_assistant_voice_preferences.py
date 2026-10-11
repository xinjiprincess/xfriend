"""Personal voice choices do not disclose administrator-owned service secrets."""

from tests.support.auth import create_user


async def test_personal_voice_settings_and_redacted_service_list(env) -> None:
    client, _server, admin = env
    alice = await create_user(client, admin, username="alice")
    created = await client.post(
        "/api/admin/voice/providers",
        headers=admin,
        json={
            "name": "mimo-test",
            "kind": "mimo",
            "capability": "both",
            "api_key": "private-test-key",
            "extra_json": '{"voice_sample":"private-reference"}',
        },
    )
    assert created.status_code == 201
    public = await client.get("/api/voice/providers", headers=alice)
    assert public.status_code == 200
    assert "private-test-key" not in public.text
    assert "private-reference" not in public.text
    preferences = {
        "stt": "mimo-test",
        "tts": "mimo-test",
        "voice_id": "冰糖",
        "auto_read": True,
        "auto_send": False,
    }
    saved = await client.put("/api/voice/preferences", headers=alice, json=preferences)
    assert saved.status_code == 200
    assert saved.json() == preferences
    assert (await client.get("/api/voice/preferences", headers=admin)).json()["tts"] == ""
    rejected = await client.put("/api/voice/preferences", headers=alice, json={"stt": "edge"})
    assert rejected.status_code >= 400
    assert (await client.get("/api/voice/preferences", headers=alice)).json() == preferences
    assert (await client.get("/api/voice/preferences")).status_code == 401
