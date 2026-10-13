"""Unmigrated messages stay visibly pending while their expert is restoring."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from octop.infra.errors import ErrorCode, OctopError
from octop.infra.history.reader import _legacy_agent


def test_checkpoint_history_waits_for_sdk_and_expert_restore() -> None:
    get_agent = Mock()
    restoring = SimpleNamespace(agent_startup_pending=lambda _: True, get_agent=get_agent)
    for registry in (None, restoring):
        with pytest.raises(OctopError) as raised:
            _legacy_agent(registry, "old-expert")
        assert raised.value.code is ErrorCode.SERVICE_STARTING
        assert raised.value.status == 503
    get_agent.assert_not_called()
    restoring.agent_startup_pending = lambda _: False
    assert _legacy_agent(restoring, "old-expert") is get_agent.return_value


def test_checkpoint_history_keeps_stopped_expert_error() -> None:
    registry = SimpleNamespace(
        agent_startup_pending=lambda _: False,
        get_agent=Mock(side_effect=OctopError(ErrorCode.AGENT_NOT_RUNNING, "Stopped expert")),
    )
    with pytest.raises(OctopError) as raised:
        _legacy_agent(registry, "stopped")
    assert raised.value.code is ErrorCode.AGENT_NOT_RUNNING
