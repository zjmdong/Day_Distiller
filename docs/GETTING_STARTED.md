# Getting started

[English](GETTING_STARTED.md) · [简体中文](GETTING_STARTED.zh-CN.md) · [Project](../README.md)

This guide targets the committed `firmware-production` implementation: HW 2.0, firmware 2.1.0 and ESP-IDF 6.0.1. For software-only exploration, use the [desktop branch](https://github.com/zjmdong/Day_Distiller/tree/desktop-app).

## 1. Prepare the hardware

- A compatible ESP32-S3 board with **16 MB flash and 8 MB Octal PSRAM**, wired according to the [hardware guide](HARDWARE.md).
- The supported camera, microphone, IMU, RTC, fuel gauge and RGB chain, or deliberately ported replacements.
- A backed-up FAT32 microSD/TF card and a **USB data cable**, not a charging-only cable.
- Safe power and access to your board's documented bootloader/recovery mechanism.

The repository does not include the original manufacturing files. Verify your own wiring before flashing; similar-looking camera connectors are not proof of compatibility.

## 2. Set up ESP-IDF

Install **ESP-IDF 6.0.1** using the [official ESP32-S3 guide](https://docs.espressif.com/projects/esp-idf/en/v6.0.1/esp32s3/get-started/index.html). Use its activated terminal, or activate your own installation using `export.ps1` on Windows / `export.sh` on Linux or macOS.

```sh
idf.py --version
git clone --branch firmware-production https://github.com/zjmdong/Day_Distiller.git Day_Distiller
cd Day_Distiller
idf.py set-target esp32s3
idf.py build
```

`set-target` is intended for a fresh checkout; preserve any existing custom configuration before changing an established build. The component manager resolves dependencies from [`main/idf_component.yml`](../main/idf_component.yml) and [`dependencies.lock`](../dependencies.lock), so the initial build needs network access.

Important build assumptions:

| Setting | Reference |
| :--- | :--- |
| Target, flash and PSRAM | [`sdkconfig.defaults`](../sdkconfig.defaults) |
| Board pins and capture duration | [`main/include/day_pins.h`](../main/include/day_pins.h) |
| Default capture/settings values | [`main/src/config/app_config.c`](../main/src/config/app_config.c) |
| Captive portal DNS component | `${IDF_PATH}/examples/protocols/http_server/captive_portal/components/dns_server` |

The DNS component is taken from the **full ESP-IDF source tree** through the root `CMakeLists.txt`. An SDK installation missing its examples directory is insufficient. Start with the pinned version rather than assuming compatibility with another major release.

## 3. Flash

Close applications holding the device's serial ports. Enter the board's documented download mode if necessary, identify its flashing port and replace `PORT` below (for example, `COM5` on Windows):

```sh
idf.py -p PORT flash
```

This changes the device firmware; back up any configuration you need first. Do not routinely erase flash or format the card to work around a connection issue.

The running application exposes TinyUSB interfaces that can differ from the flashing interface. In serial mode, **CDC0 is the binary protocol and CDC1 is logging**. Do not send terminal text to the protocol port, and do not keep a serial monitor attached while the desktop app is using that port.

## 4. Configure and capture

1. Insert the test card. For initial Wi-Fi setup, use independent power and avoid an active host USB maintenance session.
2. Cold-start the device. It attempts time synchronisation with saved Wi-Fi settings before opening its configuration hotspot.
3. Connect promptly to **`AI_CAM_XXXX`**. The initial unconnected portal window is approximately **10 seconds**; this committed version extends the window for connected clients but also has a five-minute hard limit. Power-cycle to reopen it if needed.
4. Open the captive portal. If the automatic page does not appear, browse to the access point's gateway address (normally `http://192.168.4.1`).
5. Check camera preview, audio waveform, IMU, battery and storage. Set the time zone and, only if appropriate, test-network credentials.
6. Trigger one recording from the web interface or allow automatic capture after the configuration phase. The default interval is **300 seconds** and each capture is **five seconds**. Motion wake is disabled by default.
7. Verify the resulting bundle before enabling repeated capture or testing host import.

The default time-zone string is **`CST-8`**, using POSIX syntax for UTC+08:00. Set a suitable value for your location and verify recorded timestamps. An unset RTC/network clock can produce fallback dates; inspect `time_quality` rather than trusting a folder name alone.

### Configuration and privacy

The hotspot is open and the configuration interface uses HTTP without an authenticated user boundary. **Firmware 2.1.0 returns the saved Wi-Fi password in its web configuration response.** Use an isolated test environment, avoid sensitive network credentials, and do not expose the portal through routing or port forwarding. This is a runtime limitation, separate from whether the Git repository contains credentials.

USB configuration/status handling has its own capability and secret-reading rules; do not assume those rules protect the web interface. Changes in a developer's uncommitted working tree are not part of this documented release.

## 5. Understand the recording format

Completed recordings appear at the card root:

```text
REC_0001_260920_143000/
├── video.avi      MJPEG video
├── audio.wav      Mono PCM s16le audio
├── imu.json       Motion samples and timing
└── meta.json      Capture identity, time quality and stream results
```

The directory format is `REC_NNNN_YYMMDD_HHMMSS`. `NNNN` is a four-digit sequence component, not a globally unique identifier. Use the metadata's `record_id` and `device_id` when correlating records.

In-progress data lives under `.recording/REC_....partial`. A completed directory is published only after file/metadata finalisation. Do not import a `.partial` directory as a finished recording, and do not delete it automatically during troubleshooting.

For current metadata (`schema_version: 2`), inspect:

- `record_state` and **`capture_result`**: a finalised record can explicitly report `partial_stream_failure`; “published” does not mean every sensor succeeded.
- `time_quality`, `start_time_utc_ms`, `local_time` and `timezone`.
- `streams.video.actual_fps`, counts, per-stream errors and timing offsets.

Video, audio and IMU are coordinated by software. Check measured offsets/rates for your board rather than assuming sample-accurate synchronisation or the configured target FPS.

## 6. Connect the desktop companion

Use a separate checkout so firmware and desktop environments remain independent:

```sh
git clone --branch desktop-app https://github.com/zjmdong/Day_Distiller.git Day_Distiller_Desktop
```

Follow the [desktop setup guide](https://github.com/zjmdong/Day_Distiller/blob/desktop-app/README.md). Start with offline mock mode and expendable input data.

For a real device, the app discovers the protocol interface, requests a storage transition and verifies copied files. USB re-enumeration can change port names. Always eject before leaving MSC; never have another process writing the card during a transition. Real cloud generation and email delivery require separately configured services and may incur costs.

## 7. Develop and verify

Host-side firmware contract tests do not require attached hardware:

```sh
python -m unittest discover -s tests -p "test_*.py" -v
```

These tests inspect source-level contracts; they do not simulate ESP32 timing, prove electrical behaviour or replace an ESP-IDF build. The `tests/hardware_*.py` tools are opt-in diagnostics, **not** part of ordinary test discovery. Inspect each script's help and operations before using it on a device; use backed-up or disposable data.

| Change | Review together |
| :--- | :--- |
| New board / sensor | Pins, board initialisation, driver, power state and capture metadata |
| Capture format | Recorder, metadata, storage publication and desktop importer/media preprocessing |
| USB command | C protocol/session handling, Python protocol/device implementation and both test suites |
| Configuration | Defaults, validation, persistence/revision handling, web UI and desktop capability gates |
| AI or journal behaviour | Desktop provider interfaces, pipeline state, mocks and report generation |

## Troubleshooting

| Symptom | First checks |
| :--- | :--- |
| Camera or PSRAM fails | Exact module variant, Octal PSRAM config, rails, camera reset and FPC ordering |
| Hotspot is absent | Cold-start path, saved-network sync, portal timeout and active USB maintenance |
| TF card cannot mount | Back up first; inspect filesystem, SPI wiring and power. Automatic formatting is intentionally disabled. |
| Device appears as several COM ports | Distinguish protocol, logs and bootloader; let the desktop probe with `HELLO`. |
| Capture dates look wrong | RTC validity, NTP availability, time zone and `meta.json` time quality |
| Frame rate is lower than configured | Actual metadata, lighting/JPEG workload, PSRAM and SPI-card throughput |
| Desktop cannot process media | FFmpeg/FFprobe availability, valid input files and [desktop troubleshooting](https://github.com/zjmdong/Day_Distiller/blob/desktop-app/docs/usb-ffmpeg-troubleshooting.md) |

When reporting a problem, include the branch/commit, firmware version, board differences, reproduction steps and **redacted** logs. Never attach API keys, saved network settings, identifiable recordings or the original private design files.
