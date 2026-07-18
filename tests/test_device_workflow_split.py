from datetime import date
from unittest.mock import MagicMock

import pytest

from day_distiller_client.device import DeviceProfile
from day_distiller_client.device_workflow import LegacyDeviceWorkflow, SyncedDay
from day_distiller_client.protocol import ProtocolError


def test_synced_day_keeps_verified_job_and_visible_record_names() -> None:
    synced = SyncedDay("job-1", ("REC_0001_260717_080000", "REC_0002_260717_120000"))
    assert synced.job_id == "job-1"
    assert len(synced.record_names) == 2


def _profile() -> DeviceProfile:
    return DeviceProfile(
        firmware_version="2.1.1",
        serial_number="DD-TEST",
        device_id="DD-TEST",
        protocol_version=1,
        adapter_name="transactional_export_v2",
        capabilities=frozenset({"transactional_export_v2"}),
    )


def _export_response(state: str, export_id: str) -> dict[str, object]:
    return {
        "export_id": export_id,
        "date": "2026-07-16",
        "manifest_sha256": "a" * 64,
        "record_count": 3,
        "manifest_path": f"/.day_distiller/exports/{export_id}.json",
        "state": state,
    }


@pytest.mark.parametrize("terminal_state", ["aborted", "committed"])
def test_terminal_export_transaction_creates_a_fresh_attempt(terminal_state: str) -> None:
    device = MagicMock()
    device.begin_export.side_effect = [
        _export_response(terminal_state, "exp-old"),
        _export_response("prepared", "exp-new"),
    ]
    messages: list[str] = []
    workflow = LegacyDeviceWorkflow(None, status=messages.append)  # type: ignore[arg-type]

    transaction = workflow._prepare_v2_export(
        device,
        _profile(),
        {"date": "2026-07-16", "record_count": 3, "total_bytes": 1024},
    )

    assert transaction.state == "prepared"
    assert transaction.export_id == "exp-new"
    assert device.begin_export.call_count == 2
    first_id = device.begin_export.call_args_list[0].args[1]
    retry_id = device.begin_export.call_args_list[1].args[1]
    assert retry_id != first_id
    assert retry_id.startswith(f"{first_id}-retry-")
    assert len(retry_id) <= 64
    assert terminal_state in messages[0]


def test_date_already_prepared_reuses_only_the_active_transaction() -> None:
    device = MagicMock()
    device.begin_export.side_effect = ProtocolError("BAD_STATE: date_already_prepared")
    prepared = _export_response("prepared", "exp-active")
    aborted = _export_response("aborted", "exp-aborted")

    def export_status(*args, **kwargs):
        if kwargs:
            return {"items": [aborted, prepared], "next_cursor": None}
        assert args == ("exp-active",)
        return prepared

    device.get_export_status.side_effect = export_status
    workflow = LegacyDeviceWorkflow(None)  # type: ignore[arg-type]

    transaction = workflow._prepare_v2_export(
        device,
        _profile(),
        {"date": "2026-07-16", "record_count": 3, "total_bytes": 1024},
    )

    assert transaction.export_id == "exp-active"
    assert transaction.state == "prepared"
