"""Real action/transport methods, synthetic WS only; no QQ or network."""
from importlib import import_module
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock
import asyncio
import json
import sys

import pytest

PACKAGE = "sl_video_deadline_test"
package = ModuleType(PACKAGE)
package.__path__ = [str(Path(__file__).resolve().parents[1])]
sys.modules[PACKAGE] = package
actions = import_module(f"{PACKAGE}.services.action_service")
transport_module = import_module(f"{PACKAGE}.transport")


class SyntheticWS:
    closed = False

    def __init__(self):
        self.requests = asyncio.Queue()
        self.sent = []

    async def send_str(self, data):
        request = json.loads(data)
        self.sent.append(request)
        await self.requests.put(request)


def setup_service(monkeypatch):
    # Scale seconds by 100, preserving 15 < 25 < outer 30 < gateway 60.
    original_wait_for = asyncio.wait_for
    original_timeout = asyncio.timeout
    budgets = []

    def scaled_wait_for(future, timeout):
        budgets.append(timeout)
        return original_wait_for(future, timeout=timeout / 100)

    transport_asyncio = SimpleNamespace(
        get_running_loop=asyncio.get_running_loop, Future=asyncio.Future,
        wait_for=scaled_wait_for,
    )
    service_asyncio = SimpleNamespace(
        CancelledError=asyncio.CancelledError,
        timeout=lambda seconds: original_timeout(None if seconds is None else seconds / 100),
    )
    transport = transport_module.QQTransportClient(Mock(), AsyncMock(), AsyncMock(), AsyncMock())
    transport._ws = SyntheticWS()
    transport._server_config = SimpleNamespace(action_timeout_sec=15.0)
    transport.observed_budgets = budgets
    monkeypatch.setattr(transport_module, "asyncio", transport_asyncio)
    monkeypatch.setattr(actions, "asyncio", service_asyncio)
    service = actions.QQActionService(
        Mock(), transport, SimpleNamespace(profile=SimpleNamespace(client_type="snowluma"))
    )
    return service, transport


VIDEO = {"message_type": "group", "group_id": "200", "message": [{"type": "video", "data": {"file": "synthetic"}}]}


def resolve(transport, request, data):
    transport._resolve_pending_action(request["echo"], {"status": "ok", "retcode": 0, "data": data})


@pytest.mark.asyncio
async def test_video_exceeds_old_deadline_but_confirms_inside_outer_rpc(monkeypatch):
    service, transport = setup_service(monkeypatch)
    async with asyncio.timeout(.60):  # actual Host gateway default, scaled
        async with asyncio.timeout(.30):  # actual Runner cap.call default, scaled
            task = asyncio.create_task(service.call_action("send_msg", VIDEO))
            request = await transport._ws.requests.get()
            # Advance past the old 15s deadline without waiting real seconds.
            await asyncio.sleep(.17)
            assert not task.done()
            resolve(transport, request, {"message_id": -123})
            query = await asyncio.wait_for(transport._ws.requests.get(), 1)
            assert query["action"] == "get_msg"
            resolve(transport, query, {"message_id": -123, "message_type": "group", "group_id": 200, "message_seq": 42})
            assert (await task)["data"]["message_id"] == -123
    assert [r["action"] for r in transport._ws.sent] == ["send_msg", "get_msg"]
    assert not transport._pending_actions
    assert transport.observed_budgets == [25.0, 15.0]


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["send_wait", "group_confirm"])
async def test_video_total_timeout_is_unknown_and_late_response_does_not_resend(monkeypatch, phase):
    service, transport = setup_service(monkeypatch)
    task = asyncio.create_task(service.call_action("send_msg", VIDEO))
    request = await transport._ws.requests.get()
    if phase == "group_confirm":
        await asyncio.sleep(.17)
        resolve(transport, request, {"message_id": -123})
        request = await asyncio.wait_for(transport._ws.requests.get(), 1)
    with pytest.raises(RuntimeError, match=f"stage={phase}.*error_type=TimeoutError"):
        await task
    count = len(transport._ws.sent)
    resolve(transport, request, {"message_id": -123})
    assert len(transport._ws.sent) == count
    assert not transport._pending_actions
    assert sum(r["action"] == "send_msg" for r in transport._ws.sent) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["send_wait", "group_confirm"])
async def test_cancellation_removes_pending_and_never_replays(monkeypatch, phase):
    service, transport = setup_service(monkeypatch)
    task = asyncio.create_task(service.call_action("send_msg", VIDEO))
    request = await transport._ws.requests.get()
    if phase == "group_confirm":
        resolve(transport, request, {"message_id": -123})
        request = await asyncio.wait_for(transport._ws.requests.get(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    resolve(transport, request, {"message_id": -123})
    assert len(transport._ws.sent) == (1 if phase == "send_wait" else 2)
    assert not transport._pending_actions
    assert "error_type=CancelledError" in service._logger.warning.call_args.args[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("action,params", [
    ("get_msg", {"message_id": -123}),
    ("send_msg", {"message": [{"type": "text", "data": {"text": "private synthetic text"}}]}),
    ("send_msg", {"message": [{"type": "image", "data": {"file": "secret-token"}}]}),
])
async def test_non_video_keeps_configured_deadline_and_diagnostics_are_private(monkeypatch, action, params):
    service, transport = setup_service(monkeypatch)
    task = asyncio.create_task(service.call_action(action, params))
    await transport._ws.requests.get()
    with pytest.raises(RuntimeError, match="stage=send_wait.*error_type=TimeoutError") as raised:
        await task
    assert isinstance(raised.value.__cause__, TimeoutError)
    assert "secret-token" not in str(raised.value)
    assert "private synthetic text" not in str(raised.value)
    assert len(transport._ws.sent) == 1
    assert not transport._pending_actions
    assert transport.observed_budgets == [15.0]


def test_video_budget_fits_current_host_rpc_contract():
    assert actions.VIDEO_ACTION_BUDGET_SEC == 25
    assert actions.VIDEO_ACTION_BUDGET_SEC + 5 <= 30 < 60
    for action in ["send_msg", "send_group_msg", "send_private_msg"]:
        assert actions._is_video_send(action, VIDEO)
    assert not actions._is_video_send("get_msg", VIDEO)
