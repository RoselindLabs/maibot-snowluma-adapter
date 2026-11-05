"""Recover SnowLuma private recall actors from an exact existing Host session."""

from typing import Any, Mapping

import asyncio

from ..codecs.notice.helpers import normalize_qq_user_id


class QQPrivateRecallResolver:
    """Read through existing SDK capabilities only; never infer from a global ID."""

    def __init__(self, context: Any, logger: Any) -> None:
        self._context = context
        self._logger = logger

    async def resolve(self, payload: Mapping[str, Any], account_id: str, scope: str, peer: str) -> str:
        operator = normalize_qq_user_id(payload.get("operator_id"))
        if operator:
            return operator if operator in {account_id, peer} else ""
        message_id = str(payload.get("message_id") or "").strip()
        if not self._context or not account_id or not peer or not message_id:
            return ""
        try:
            async with asyncio.timeout(2):
                return await self._from_history(message_id, account_id, scope, peer)
        except Exception:
            # No payloads, message contents or identifiers in diagnostics; cancellation propagates.
            self._logger.debug("私聊撤回原消息身份查询失败")
            return ""

    async def _from_history(self, message_id: str, account_id: str, scope: str, peer: str) -> str:
        streams = await self._context.chat.get_all_streams(platform="qq")
        # SDK unwraps {success, streams} and {success, message} itself.
        if not isinstance(streams, list) or len(streams) > 512:
            return ""
        matches = [
            stream for stream in streams
            if isinstance(stream, Mapping)
            and stream.get("platform") == "qq"
            and stream.get("account_id") == account_id
            and (stream.get("scope") or "") == scope
            and stream.get("chat_type") == "private"
            and stream.get("is_group_session") is False
            and not stream.get("group_id")
            and stream.get("user_id") == peer
        ]
        if len(matches) != 1:
            return ""
        session_id = matches[0].get("session_id")
        if not isinstance(session_id, str) or not session_id.strip():
            return ""
        message = await self._context.message.get_by_id(
            message_id, stream_id=session_id, include_binary_data=False,
        )
        if not isinstance(message, Mapping) or (
            message.get("platform") != "qq"
            or message.get("account_id") != account_id
            or (message.get("scope") or "") != scope
            or message.get("session_id") != session_id
            or message.get("message_id") != message_id
            or message.get("is_notify") is not False
        ):
            return ""
        info = message.get("message_info")
        if not isinstance(info, Mapping) or info.get("group_info"):
            return ""
        user = info.get("user_info")
        config = info.get("additional_config") or {}
        if not isinstance(user, Mapping) or not isinstance(config, Mapping):
            return ""
        sender = normalize_qq_user_id(user.get("user_id"))
        target = normalize_qq_user_id(config.get("platform_io_target_user_id"))
        if sender == account_id:
            return sender if target == peer else ""
        if sender == peer and (not target or target == peer):
            return sender
        return ""
