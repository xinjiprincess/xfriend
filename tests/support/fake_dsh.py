"""Deterministic ACP subprocess: exercises transport without a provider or credentials."""

import json
import os
import sys
from pathlib import Path


def send(payload: dict) -> None:
    print(json.dumps({"jsonrpc": "2.0", **payload}), flush=True)


def reply(request_id: object, result: dict) -> None:
    send({"id": request_id, "result": result})


pending = None
log = Path(os.environ["DSH_HOME"]) / "calls.log"
for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    with log.open("a", encoding="utf-8") as stream:
        stream.write(str(method) + "\n")
    if method == "initialize":
        reply(request["id"], {"protocolVersion": 1, "agentCapabilities": {}})
    elif method == "session/new":
        reply(request["id"], {"sessionId": "persistent-session"})
    elif method in {"session/resume", "session/close"}:
        reply(request["id"], {})
    elif method == "session/prompt":
        text = request["params"]["prompt"][0]["text"]
        if text in {"wait", "permission"}:
            pending = request["id"]
            if text == "permission":
                send(
                    {
                        "id": "permission-1",
                        "method": "session/request_permission",
                        "params": {
                            "sessionId": "persistent-session",
                            "toolCall": {
                                "toolCallId": "tool-1",
                                "title": "Read workspace",
                                "kind": "read",
                            },
                            "options": [
                                {"optionId": "allow", "name": "Allow once", "kind": "allow_once"},
                                {
                                    "optionId": "reject",
                                    "name": "Reject once",
                                    "kind": "reject_once",
                                },
                            ],
                        },
                    }
                )
        else:
            send(
                {
                    "method": "session/update",
                    "params": {
                        "sessionId": "persistent-session",
                        "update": {
                            "sessionUpdate": "agent_message_chunk",
                            "content": {
                                "type": "text",
                                "text": "reply:" + text,
                            },
                        },
                    },
                }
            )
            reply(request["id"], {"stopReason": "end_turn"})
    elif method == "session/cancel":
        if pending is not None:
            reply(pending, {"stopReason": "cancelled"})
            pending = None
    elif request.get("id") == "permission-1":
        reply(pending, {"stopReason": "end_turn"})
        pending = None
