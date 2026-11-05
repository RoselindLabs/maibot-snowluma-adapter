"""Synthetic OneBot -> real SDK normalization -> notice codec -> gateway contracts."""

from copy import deepcopy
from importlib import import_module
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import asyncio
import sys

import pytest
from maibot_sdk.context import PluginContext

PACKAGE = "sl_private_recall_test"
package = ModuleType(PACKAGE)
package.__path__ = [str(Path(__file__).resolve().parents[1])]
sys.modules[PACKAGE] = package
QQNoticeCodec = import_module(f"{PACKAGE}.codecs.notice.message_codec").QQNoticeCodec
QQEventRouter = import_module(f"{PACKAGE}.runtime.router").QQEventRouter
QQPrivateRecallResolver = import_module(f"{PACKAGE}.runtime.private_recall").QQPrivateRecallResolver


def stream(**changes):
    return dict(
        session_id="private-session", stream_id="private-session", platform="qq",
        account_id="100", scope="scope", chat_type="private", is_group_session=False,
        user_id="200", group_id=None, **changes,
    )


def original(sender="100"):
    return {
        "message_id": "-123", "session_id": "private-session", "platform": "qq",
        "account_id": "100", "scope": "scope", "is_notify": False,
        "message_info": {
            "user_info": {"user_id": sender}, "group_info": None,
            "additional_config": {"platform_io_target_user_id": "200"},
        },
    }


def payload(**changes):
    return {
        "post_type": "notice", "notice_type": "friend_recall", "self_id": 100,
        "user_id": 200, "message_id": -123, "time": 1791504000, **changes,
    }


def setup_route(*, message=None, streams=None, client_type="snowluma", enabled=True, banned=()):
    # Only the bottom RPC is mocked: PluginContext unwraps the real Host result shapes.
    async def rpc(method, plugin_id, data, **kwargs):
        assert method == "cap.call"
        capability, args = data["capability"], data["args"]
        if capability == "chat.get_all_streams":
            assert args == {"platform": "qq"}
            return {"success": True, "streams": [stream()] if streams is None else streams}
        assert capability == "message.get_by_id"
        assert args == {
            "message_id": "-123", "stream_id": "private-session", "chat_id": "",
            "include_binary_data": False,
        }
        return {"success": True, "message": message}

    rpc_mock = AsyncMock(side_effect=rpc)
    context = PluginContext(plugin_id="maibot-team.snowluma-adapter", rpc_call=rpc_mock)
    query = SimpleNamespace(
        get_stranger_info=AsyncMock(side_effect=lambda uid: {"nickname": {"100": "Bot", "200": "Peer"}[uid]}),
        get_group_info=AsyncMock(return_value={"group_name": "Group"}),
        get_group_member_info=AsyncMock(side_effect=lambda gid, uid: {"nickname": {"100": "Bot", "200": "Peer"}[uid]}),
    )
    codec = QQNoticeCodec(Mock(), query)
    settings = SimpleNamespace(
        client=SimpleNamespace(connection_id="scope"), filters=SimpleNamespace(ban_user_id=list(banned)),
        notice=object(), debug=SimpleNamespace(ignore_self_message=True),
    )
    gateway = SimpleNamespace(route_message=AsyncMock(return_value=True))
    router = QQEventRouter(gateway, Mock(), "gateway", lambda: settings, host_context=context)
    router.bind_runtime(SimpleNamespace(
        profile=SimpleNamespace(client_type=client_type), notice_codec=codec,
        ban_tracker=SimpleNamespace(record_notice=AsyncMock()),
        notice_filter=SimpleNamespace(is_notice_event_allowed=Mock(return_value=enabled)),
    ))
    return router, gateway, rpc_mock, query


@pytest.mark.parametrize("sender,name", [("100", "Bot"), ("200", "Peer")])
async def test_recall_actor_and_private_peer_are_independent_through_real_sdk(sender, name):
    router, gateway, rpc, _ = setup_route(message=original(sender))
    event = payload()
    before = deepcopy(event)
    await router.handle_transport_payload(event)
    message = gateway.route_message.call_args.kwargs["message"]
    assert message["message_info"]["user_info"]["user_id"] == sender
    assert message["processed_plain_text"] == f"[事件-好友撤回] {name} 撤回了一条消息（消息ID:-123）"
    assert message["message_info"]["additional_config"]["platform_io_target_user_id"] == "200"
    assert message["message_info"]["additional_config"]["napcat_notice_payload"] == before == event
    assert (message["account_id"], message["scope"]) == ("100", "scope")
    assert rpc.await_count == 2


@pytest.mark.parametrize("operator", [100, 200])
async def test_explicit_actor_never_becomes_private_route(operator):
    router, gateway, rpc, _ = setup_route()
    await router.handle_transport_payload(payload(operator_id=operator))
    info = gateway.route_message.call_args.kwargs["message"]["message_info"]
    assert info["user_info"]["user_id"] == str(operator)
    assert info["additional_config"]["platform_io_target_user_id"] == "200"
    rpc.assert_not_awaited()


@pytest.mark.parametrize("change", [
    {"account_id": "999"}, {"scope": "another"}, {"platform": "other"},
    {"user_id": "999"}, {"group_id": "300", "chat_type": "group", "is_group_session": True},
    {"session_id": ""}, {"chat_type": None},
])
async def test_no_cross_identity_or_unscoped_query(change):
    target = stream()
    target.update(change)
    router, gateway, rpc, _ = setup_route(message=original(), streams=[target])
    await router.handle_transport_payload(payload())
    gateway.route_message.assert_not_awaited()
    assert rpc.await_count == 1


@pytest.mark.parametrize("streams", [[], [stream(), stream()], [stream()] * 513])
async def test_missing_ambiguous_or_oversized_session_discovery_is_unknown(streams):
    router, gateway, rpc, _ = setup_route(message=original(), streams=streams)
    await router.handle_transport_payload(payload())
    gateway.route_message.assert_not_awaited()
    assert rpc.await_count == 1


@pytest.mark.parametrize("change", [
    {"account_id": "999"}, {"scope": "another"}, {"session_id": "other-session"},
    {"message_id": "other-id"}, {"platform": "other"}, {"is_notify": True},
])
async def test_returned_message_identity_is_rechecked(change):
    message = original()
    message.update(change)
    router, gateway, _, _ = setup_route(message=message)
    await router.handle_transport_payload(payload())
    gateway.route_message.assert_not_awaited()


@pytest.mark.parametrize("message", [
    None, {}, original("999"),
    {**original(), "message_info": {"user_info": {"user_id": "100"}}},
    {**original(), "message_info": {**original()["message_info"], "group_info": {"group_id": "300"}}},
    {**original(), "message_info": {**original()["message_info"], "additional_config": {"platform_io_target_user_id": "999"}}},
])
async def test_missing_or_conflicting_evidence_never_labels_peer(message):
    router, gateway, _, query = setup_route(message=message)
    await router.handle_transport_payload(payload())
    gateway.route_message.assert_not_awaited()
    query.get_stranger_info.assert_not_awaited()


@pytest.mark.parametrize("changes", [{"user_id": None}, {"user_id": True}, {"user_id": 100}, {"group_id": 300}, {"operator_id": 999}])
async def test_invalid_route_or_foreign_operator_is_rejected(changes):
    router, gateway, rpc, _ = setup_route(message=original())
    await router.handle_transport_payload(payload(**changes))
    gateway.route_message.assert_not_awaited()
    rpc.assert_not_awaited()


@pytest.mark.parametrize("options", [{"enabled": False}, {"banned": ["200"]}, {"banned": ["100"]}])
async def test_existing_notice_switch_and_blacklists_remain_effective(options):
    router, gateway, _, _ = setup_route(message=original(), **options)
    await router.handle_transport_payload(payload())
    gateway.route_message.assert_not_awaited()


async def test_group_recall_is_unchanged_and_does_not_read_private_history():
    router, gateway, rpc, _ = setup_route()
    await router.handle_transport_payload(payload(notice_type="group_recall", group_id=300, operator_id=100))
    message = gateway.route_message.call_args.kwargs["message"]
    assert message["processed_plain_text"] == "[事件-群消息撤回] Bot 撤回了 Peer 的消息（消息ID:-123）"
    assert message["message_info"]["additional_config"]["platform_io_target_group_id"] == "300"
    rpc.assert_not_awaited()


async def test_napcat_standard_friend_recall_retains_user_semantics():
    router, gateway, rpc, _ = setup_route(client_type="napcat")
    await router.handle_transport_payload(payload())
    assert gateway.route_message.call_args.kwargs["message"]["message_info"]["user_info"]["user_id"] == "200"
    rpc.assert_not_awaited()


async def test_lookup_failure_is_unknown_and_cancellation_propagates():
    ctx = SimpleNamespace(chat=SimpleNamespace(get_all_streams=AsyncMock(side_effect=RuntimeError)))
    resolver = QQPrivateRecallResolver(ctx, Mock())
    assert await resolver.resolve(payload(), "100", "scope", "200") == ""
    ctx.chat.get_all_streams.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await resolver.resolve(payload(), "100", "scope", "200")
