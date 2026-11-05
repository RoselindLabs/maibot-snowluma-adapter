"""通知事件编解码器。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from uuid import uuid4

import time

from ...services import QQQueryService
from ...types import QQPayload, QQPayloadDict
from .enricher import QQNoticeEntityResolver
from .helpers import build_payload_digest, resolve_actor_user_id, resolve_friend_recall_peer
from .meta_event_logger import QQMetaEventObserver
from .renderer import QQNoticeTextRenderer


class QQNoticeCodec:
    """QQ 通知事件编码器。"""

    def __init__(self, logger: Any, query_service: QQQueryService) -> None:
        """初始化通知事件编码器。

        Args:
            logger: 插件日志对象。
            query_service: QQ 查询服务。
        """
        self._entity_resolver = QQNoticeEntityResolver(query_service)
        self._meta_event_observer = QQMetaEventObserver(logger)
        self._renderer = QQNoticeTextRenderer()

    def clear_cache(self) -> None:
        """清空通知编解码层的短期缓存（断连时调用）。"""
        self._entity_resolver.clear_cache()

    async def build_notice_message_dict(
        self, payload: QQPayload, *, recall_actor_user_id: Optional[str] = None,
    ) -> Optional[QQPayloadDict]:
        """将 ``notice`` 事件转换为 Host 可接受的消息字典。

        Args:
            payload: 对端推送的原始通知事件。

        Returns:
            Optional[QQPayloadDict]: 成功时返回标准 ``MessageDict``；无法识别时返回 ``None``。
        """
        notice_type = str(payload.get("notice_type") or "").strip()
        if not notice_type:
            return None

        group_id = str(payload.get("group_id") or "").strip()
        user_id = resolve_actor_user_id(payload)
        self_id = str(payload.get("self_id") or "").strip()
        private_peer = user_id
        if notice_type == "friend_recall":
            private_peer = resolve_friend_recall_peer(payload)
            if not private_peer or group_id:
                return None
            if recall_actor_user_id is not None:
                user_id = recall_actor_user_id
            # Host cannot route an unknown actor by peer yet. Do not create a notice/self stream.
            if not user_id:
                return None

        user_info = await self._entity_resolver.build_user_info(group_id=group_id, user_id=user_id)
        group_info = await self._entity_resolver.build_group_info(group_id)
        notice_text = await self._renderer.build_notice_text(
            payload,
            self._entity_resolver.resolve_display_name,
            self_id,
            recall_actor_user_id=user_id if notice_type == "friend_recall" else None,
        )
        if not notice_text:
            return None

        additional_config: Dict[str, Any] = {
            "napcat_notice_type": notice_type,
            "napcat_notice_sub_type": str(payload.get("sub_type") or "").strip(),
            "napcat_notice_payload": dict(payload),
        }
        if group_id:
            additional_config["platform_io_target_group_id"] = group_id
        elif private_peer:
            additional_config["platform_io_target_user_id"] = private_peer

        message_info: Dict[str, Any] = {"user_info": user_info, "additional_config": additional_config}
        if group_info is not None:
            message_info["group_info"] = group_info

        timestamp_seconds = payload.get("time")
        if not isinstance(timestamp_seconds, (int, float)):
            timestamp_seconds = time.time()

        segments: List[Dict[str, Any]] = [{"type": "text", "data": notice_text}]
        if notice_type in ("group_upload", "offline_file") and isinstance(payload.get("file"), dict):
            file = payload["file"]
            segments.append({"type": "file", "data": {
                "name": str(file.get("name") or "附件"), "size": file.get("size") or 0,
                "file_id": str(file.get("id") or file.get("file_id") or ""),
                "busid": int(file.get("busid") or 0),
            }})

        return {
            "account_id": self_id,
            "scope": None,
            "message_id": f"qq-notice-{uuid4().hex}",
            "timestamp": str(float(timestamp_seconds)),
            "platform": "qq",
            "message_info": message_info,
            "raw_message": segments,
            "is_mentioned": False,
            "is_at": False,
            "is_emoji": False,
            "is_picture": False,
            "is_command": False,
            "is_notify": True,
            "session_id": "",
            "processed_plain_text": notice_text,
            "display_message": notice_text,
        }

    def build_notice_dedupe_key(self, payload: QQPayload) -> Optional[str]:
        """为 对端 ``notice`` 事件构造稳定的技术性去重键。

        Args:
            payload: 对端推送的原始通知事件。

        Returns:
            Optional[str]: 若可以构造稳定去重键则返回该键，否则返回 ``None``。
        """
        notice_type = str(payload.get("notice_type") or "").strip()
        if not notice_type:
            return None

        # 撤回通知的 message_id 是被撤回消息的 ID，不能与普通消息共用去重键。
        sub_type = str(payload.get("sub_type") or "").strip()
        payload_digest = build_payload_digest(payload)
        suffix = f":{sub_type}" if sub_type else ""
        return f"notice:{notice_type}{suffix}:{payload_digest}"

    async def handle_meta_event(self, payload: QQPayload) -> None:
        """处理 ``meta_event`` 事件的日志与状态观测。

        Args:
            payload: 对端推送的原始元事件。
        """
        await self._meta_event_observer.handle_meta_event(payload)
