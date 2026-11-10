"""Preserve signed SnowLuma receipts and no replay across the OneBot merge."""
from importlib import import_module
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock
import sys

import pytest

PACKAGE = "sl_merge_delivery_test"
package = ModuleType(PACKAGE)
package.__path__ = [str(Path(__file__).resolve().parents[1])]
sys.modules[PACKAGE] = package
service_type = import_module(f"{PACKAGE}.services.action_service").QQActionService


@pytest.mark.asyncio
@pytest.mark.parametrize("receipt", ["confirmed", "zero_sequence", "wrong_group", "wrong_sign", "timeout"])
async def test_signed_receipt_requires_exact_group_and_real_sequence_without_resend(receipt):
    response = {"status": "ok", "retcode": 0, "data": {"message_id": -123}}
    detail = {"message_id": -123, "message_type": "group", "group_id": 200, "message_seq": 42}
    if receipt == "zero_sequence":
        detail["message_seq"] = 0
    elif receipt == "wrong_group":
        detail["group_id"] = 201
    elif receipt == "wrong_sign":
        detail["message_id"] = 123
    query = TimeoutError("unknown delivery") if receipt == "timeout" else {"status": "ok", "data": detail}
    transport = SimpleNamespace(call_action=AsyncMock(side_effect=[response, query]))
    service = service_type(Mock(), transport, SimpleNamespace(profile=SimpleNamespace(client_type="snowluma")))
    if receipt == "confirmed":
        assert await service.call_action("send_group_msg", {"group_id": "200"}) == response
    else:
        with pytest.raises(RuntimeError, match="投递未确认"):
            await service.call_action("send_group_msg", {"group_id": "200"})
    assert [call.args[0] for call in transport.call_action.await_args_list] == ["send_group_msg", "get_msg"]
    assert transport.call_action.await_args_list[1].args[1] == {"message_id": -123}
