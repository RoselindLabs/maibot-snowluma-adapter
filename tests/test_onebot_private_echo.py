"""Synthetic OneBot -> codec -> gateway regressions; no QQ connection."""

from importlib import import_module
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import sys

import pytest

PACKAGE = "sl_private_echo_test"
package = ModuleType(PACKAGE)
package.__path__ = [str(Path(__file__).resolve().parents[1])]
sys.modules[PACKAGE] = package
QQInboundCodec = import_module(f"{PACKAGE}.codecs.inbound.message_codec").QQInboundCodec
QQEventRouter = import_module(f"{PACKAGE}.runtime.router").QQEventRouter
profile = import_module(f"{PACKAGE}.profile")


def setup_route(client_type="snowluma", ignore=False, banned=()):
    settings = SimpleNamespace(
        client=SimpleNamespace(connection_id="scope"),
        filters=SimpleNamespace(ban_user_id=list(banned), ban_qq_bot=False),
        debug=SimpleNamespace(ignore_self_message=ignore),
    )
    client_profile = profile.PROFILE_BY_TYPE[client_type]
    codec = QQInboundCodec(Mock(), Mock(), profile.ProfileState(client_profile), lambda: settings)
    codec.convert_segments_with_metadata = AsyncMock(return_value=([{"type": "text", "data": "same text"}], False, []))
    codec._resolve_group_name = AsyncMock(return_value="Group")
    runtime = SimpleNamespace(
        profile=client_profile, inbound_codec=codec,
        official_bot_guard=SimpleNamespace(should_reject=AsyncMock(return_value=False)),
        regex_filter=SimpleNamespace(is_message_allowed=Mock(return_value=True)),
    )
    gateway = SimpleNamespace(route_message=AsyncMock(return_value=True))
    router = QQEventRouter(gateway, Mock(), "gateway", lambda: settings)
    router.bind_runtime(runtime)
    return router, gateway, codec


def payload(**fields):
    return {
        "post_type": "message_sent", "message_type": "private", "self_id": 100,
        "user_id": 100, "target_id": 200, "message_id": -123, "time": 1791504000,
        "sender": {"nickname": "Bot"}, "message": [{"type": "text", "data": {"text": "same text"}}],
        **fields,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("client_type", ["snowluma", "napcat"])
async def test_self_sent_preserves_author_and_routes_to_peer(client_type):
    router, gateway, _ = setup_route(client_type)
    await router.handle_transport_payload(payload())
    message = gateway.route_message.call_args.kwargs["message"]
    assert message["message_info"]["user_info"]["user_id"] == "100"
    assert message["message_info"]["user_info"]["user_nickname"] == "Bot"
    assert message["message_info"]["additional_config"]["platform_io_target_user_id"] == "200"
    assert (message["account_id"], message["scope"], message["message_id"]) == ("100", "scope", "-123")


@pytest.mark.asyncio
@pytest.mark.parametrize("target", [None, "", 0, -1, True, [], "not-a-peer", 1.5])
async def test_missing_or_invalid_self_peer_is_rejected_before_media(target):
    router, gateway, codec = setup_route()
    await router.handle_transport_payload(payload(target_id=target))
    gateway.route_message.assert_not_awaited()
    codec.convert_segments_with_metadata.assert_not_awaited()


@pytest.mark.asyncio
async def test_incoming_private_uses_sender_not_recipient_or_temp_group():
    router, gateway, _ = setup_route()
    await router.handle_transport_payload(payload(post_type="message", user_id=200, target_id=100, group_id=300))
    message = gateway.route_message.call_args.kwargs["message"]
    assert "group_info" not in message["message_info"]
    config = message["message_info"]["additional_config"]
    assert config["platform_io_target_user_id"] == "200"
    assert config["platform_io_temp_group_id"] == "300"


@pytest.mark.asyncio
async def test_group_self_echo_does_not_require_private_target():
    router, gateway, _ = setup_route()
    await router.handle_transport_payload(payload(message_type="group", group_id=300, target_id=None))
    info = gateway.route_message.call_args.kwargs["message"]["message_info"]
    assert info["group_info"]["group_id"] == "300"
    assert "platform_io_target_user_id" not in info["additional_config"]


@pytest.mark.asyncio
async def test_group_without_identity_cannot_become_a_private_message():
    router, gateway, codec = setup_route()
    await router.handle_transport_payload(payload(message_type="group"))
    gateway.route_message.assert_not_awaited()
    codec._resolve_group_name.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("options", [{"ignore": True}, {"banned": ["100"]}, {"banned": ["200"]}])
async def test_existing_ignore_and_blacklist_choices_remain_effective(options):
    router, gateway, codec = setup_route(**options)
    await router.handle_transport_payload(payload())
    gateway.route_message.assert_not_awaited()
    codec.convert_segments_with_metadata.assert_not_awaited()


@pytest.mark.asyncio
async def test_private_peer_blacklist_survives_identity_lease_expiry_during_decode(monkeypatch):
    lease_module = import_module(f"{PACKAGE}.services.identity_only_lease")
    clock = SimpleNamespace(now=1000.0)
    monkeypatch.setattr(lease_module, "time", SimpleNamespace(
        monotonic=lambda: clock.now, time=lease_module.time.time,
    ))
    router, gateway, codec = setup_route(banned=["200"])
    router.identity_only_lease.update("100", "test-owner", True)
    original = codec.build_message_dict

    async def decode(*args):
        clock.now += 15
        return await original(*args)

    monkeypatch.setattr(codec, "build_message_dict", decode)
    await router.handle_transport_payload(payload())
    gateway.route_message.assert_not_awaited()
    assert not router.identity_only_lease.active("100")
    assert router.identity_observations.snapshot("100", ["200"])["observations"] == []
    codec.convert_segments_with_metadata.reset_mock()
    await router.handle_transport_payload(payload())
    codec.convert_segments_with_metadata.assert_not_awaited()
    gateway.route_message.assert_not_awaited()
