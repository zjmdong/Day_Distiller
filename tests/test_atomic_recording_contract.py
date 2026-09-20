import json
import os
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FILES = ("video.avi", "audio.wav", "imu.json")


def reference_atomic_record(root: Path, fail_after=None):
    name = "REC_0042_260717_110000"
    staging_root = root / ".recording"
    staging_root.mkdir(exist_ok=True)
    partial = staging_root / f"{name}.partial"
    final = root / name
    partial.mkdir()
    if fail_after == "mkdir":
        raise OSError("injected")
    for filename in FILES:
        (partial / filename).write_bytes(filename.encode())
        if fail_after == filename:
            raise OSError("injected")
    metadata = {
        "schema_version": 2,
        "record_state": "complete",
        "record_id": "DD-A1B2C3D4E5F6-1784266800000-0000000000042",
        "device_id": "DD-A1B2C3D4E5F6",
        "sequence": 42,
        "time_quality": "rtc_valid",
        "capture_epoch_monotonic_us": 12800421,
        "actual_duration_ms": 5038,
        "streams": {},
    }
    (partial / "meta.json").write_text(json.dumps(metadata), encoding="utf-8")
    if fail_after == "meta.json":
        raise OSError("injected")
    os.rename(partial, final)
    return final


def choose_sequence(initial, occupied):
    for attempt in range(10000):
        sequence = (initial + attempt) % 10000
        if sequence not in occupied:
            return sequence
    raise RuntimeError("all sequences occupied")


class AtomicRecordingContractTests(unittest.TestCase):
    def test_every_pre_rename_failure_preserves_partial_and_hides_formal_record(self):
        for failure in ("mkdir", "video.avi", "audio.wav", "imu.json", "meta.json"):
            with self.subTest(failure=failure):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    with self.assertRaises(OSError):
                        reference_atomic_record(root, failure)
                    self.assertFalse((root / "REC_0042_260717_110000").exists())
                    self.assertTrue((root / ".recording/REC_0042_260717_110000.partial").is_dir())

    def test_success_exposes_all_legacy_filenames_only_after_final_rename(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            final = reference_atomic_record(root)
            self.assertTrue(final.is_dir())
            self.assertEqual({item.name for item in final.iterdir()}, set(FILES) | {"meta.json"})
            metadata = json.loads((final / "meta.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["schema_version"], 2)
            self.assertEqual(metadata["record_state"], "complete")

    def test_four_digit_sequence_wrap_probes_without_overwrite(self):
        self.assertEqual(choose_sequence(9999, {9999, 0, 1}), 2)
        self.assertEqual(choose_sequence(42, {42}), 43)
        with self.assertRaises(RuntimeError):
            choose_sequence(0, set(range(10000)))

    def test_storage_uses_persistent_counter_and_checks_both_collision_targets(self):
        source = (ROOT / "main/src/storage/storage_service.c").read_text(encoding="utf-8")
        for token in ("nvs_get_u64", "nvs_set_u64", "nvs_commit", ".recording", ".partial",
                      "final_exists", "partial_exists", "DAY_RECORD_SEQUENCE_MODULUS"):
            self.assertIn(token, source)
        self.assertIn("rename(paths->dir_path, paths->final_dir_path)", source)
        self.assertNotIn("rmdir(", source)
        self.assertNotIn("unlink(", source)

    def test_ready_barrier_and_atomic_completion_order_are_explicit(self):
        source = (ROOT / "main/src/media/recorder.c").read_text(encoding="utf-8")
        ready = source.index("xEventGroupWaitBits(ctx->events, REC_BITS_READY")
        epoch = source.index("ctx->metadata.capture_epoch_monotonic_us = esp_timer_get_time()")
        start = source.index("xEventGroupSetBits(ctx->events, REC_BIT_START)")
        sync = source.index("result = synchronize_media_files(ctx)")
        metadata = source.index("day_record_metadata_write_atomic(ctx->paths.meta_path")
        finalize = source.index("day_storage_finalize_record(&ctx->paths)")
        self.assertLess(ready, epoch)
        self.assertLess(epoch, start)
        self.assertLess(sync, metadata)
        self.assertLess(metadata, finalize)
        self.assertLess(source.index("day_storage_require_free_bytes"),
                        source.index("day_storage_make_record_paths"))

    def test_transaction_context_and_unused_callback_paths_do_not_consume_main_stack(self):
        recorder = (ROOT / "main/src/media/recorder.c").read_text(encoding="utf-8")
        app = (ROOT / "main/src/app_core.c").read_text(encoding="utf-8")
        self.assertIn("record_ctx_t *ctx = calloc(1, sizeof(*ctx))", recorder)
        self.assertIn("free(ctx);", recorder)
        self.assertNotIn("record_ctx_t ctx =", recorder)
        self.assertIn("day_recorder_record_once(&s_config, NULL)", app)
        self.assertNotIn("day_record_paths_t paths;", app)

    def test_streams_are_written_incrementally_on_one_capture_epoch(self):
        recorder = (ROOT / "main/src/media/recorder.c").read_text(encoding="utf-8")
        imu = (ROOT / "main/src/drivers/imu.c").read_text(encoding="utf-8")
        self.assertNotIn("heap_caps_malloc", recorder)
        self.assertNotIn("max_samples", recorder)
        self.assertIn("fwrite(samples", recorder)
        self.assertIn("day_imu_write_json_sample(file", recorder)
        self.assertIn("sample.t_us -= ctx->metadata.capture_epoch_monotonic_us", recorder)
        self.assertIn('\\"t_us\\"', imu)

    def test_metadata_v2_has_real_timing_identity_quality_and_null_unknown_offsets(self):
        source = (ROOT / "main/src/media/record_metadata.c").read_text(encoding="utf-8")
        for field in ("schema_version", "record_state", "record_id", "device_id", "wake_reason",
                      "start_time_utc_ms", "local_time", "timezone", "time_quality",
                      "capture_epoch_monotonic_us", "actual_duration_ms", "streams",
                      "first_frame_offset_us", "last_frame_offset_us", "actual_fps"):
            self.assertIn(f'"{field}"', source)
        self.assertIn("cJSON_AddNullToObject", source)
        self.assertIn('"partial_stream_failure"', source)
        self.assertIn('"duration_s"', source)
        self.assertIn('"video_frames"', source)

    def test_export_manifest_reuses_validated_schema_v2_record_identity(self):
        source = (ROOT / "main/src/export/export_service.c").read_text(encoding="utf-8")
        self.assertIn("load_record_identity", source)
        self.assertIn('strcmp(state->valuestring, "complete")', source)
        self.assertIn('strcmp(device->valuestring, day_device_id())', source)
        self.assertIn('cJSON_AddStringToObject(record, "record_id", records[i].record_id)', source)
        status = (ROOT / "main/src/usb/usb_link.c").read_text(encoding="utf-8")
        self.assertIn("cJSON_CreateIntArray((const int[]){1, 2}, 2)", status)
        self.assertIn('json_add_item(root, "metadata_schemas", schemas)', status)

    def test_avi_uses_observed_frame_span_not_configured_duration(self):
        recorder = (ROOT / "main/src/media/recorder.c").read_text(encoding="utf-8")
        camera = recorder[recorder.index("static void camera_task"):recorder.index("static void audio_task")]
        self.assertIn("last_offset_us -", camera)
        self.assertIn("video.item_count - 1", camera)
        self.assertNotIn("DAY_RECORD_SECONDS * 1000000", camera)
        avi = (ROOT / "main/src/media/avi_writer.c").read_text(encoding="utf-8")
        self.assertIn("fflush(writer->file) != 0", avi)
        self.assertIn("fwrite(data, 1, len, writer->file) != len", avi)

    def test_invalid_rtc_cannot_be_normalized_into_valid_time(self):
        source = (ROOT / "main/src/drivers/rtc_clock.c").read_text(encoding="utf-8")
        self.assertIn("bcd_is_valid", source)
        self.assertIn("calendar_exact", source)
        self.assertIn("status->unix_time = status->valid ? now : 0", source)

    def test_all_record_failures_preserve_staging_data(self):
        recorder = (ROOT / "main/src/media/recorder.c").read_text(encoding="utf-8")
        metadata = (ROOT / "main/src/media/record_metadata.c").read_text(encoding="utf-8")
        self.assertNotIn("unlink(", recorder)
        self.assertNotIn("rmdir(", recorder)
        self.assertNotIn("unlink(", metadata)
        self.assertNotIn("rmdir(", metadata)
        self.assertIn("preserving temporary file", metadata)
        for operation in ("fwrite(json", "fflush(file) != 0", "fsync(fileno(file)) != 0",
                          "fclose(file) != 0", "rename(temporary, path) != 0"):
            self.assertIn(operation, metadata)


if __name__ == "__main__":
    unittest.main()
