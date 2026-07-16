from day_distiller_client.device_workflow import SyncedDay


def test_synced_day_keeps_verified_job_and_visible_record_names() -> None:
    synced = SyncedDay("job-1", ("REC_0001_260717_080000", "REC_0002_260717_120000"))
    assert synced.job_id == "job-1"
    assert len(synced.record_names) == 2
