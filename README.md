# EatVenture AI/BOT

[Leia em português](README.pt-BR.md)

Vision bot for EatVenture. It receives the Android video stream through scrcpy-server, finds elements by template matching, and sends taps through ADB.

> Template detection is the stable mode. Data collection and the model in `IA/` are experimental.

## Requirements

- Python 3.12 or later;
- [scrcpy](https://github.com/Genymobile/scrcpy) and `adb` on `PATH` — on Windows, scrcpy may also be placed under `tools/scrcpy/`;
- An Android device with USB debugging or Wireless debugging enabled.

## Setup

From the project root, create a virtual environment and install dependencies:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

On Linux or macOS, activate the environment with:

```bash
source .venv/bin/activate
```

### scrcpy and ADB

On Windows, the project includes a scrcpy installer:

```powershell
.\tools\install_scrcpy.ps1
```

It installs to `tools/scrcpy/`, which the project detects automatically. On other systems, install scrcpy using your distribution's recommended method and confirm that `scrcpy` and `adb` are on `PATH`:

```bash
scrcpy --version
adb version
```

### Prepare the Android device

1. Open **Settings > About phone** and tap **Build number** seven times. On some devices, this is under **Software information**.
2. Return to Settings and open **Developer options**.
3. Enable **USB debugging**. For Wi-Fi on Android 11 or later, also enable **Wireless debugging**.

## Connect the device

### USB

Connect the device by cable and accept the debugging prompt on Android. Confirm the connection:

```bash
adb devices
```

The device must have the `device` status, not `unauthorized` or `offline`.

### Wi-Fi: Android 11 or later

The computer and device must be on the same network. Under **Developer options > Wireless debugging**, select **Pair device with pairing code** and run:

```bash
adb pair IP:PAIRING_PORT
```

Enter the code shown on Android. Then, on the main **Wireless debugging** screen, use the IP and connection port (it may differ from the pairing port):

```bash
adb connect IP:CONNECTION_PORT
adb devices
```

### Wi-Fi: Android 10 or earlier

First connect and authorize the device over USB. With both devices on the same Wi-Fi network, run:

```bash
adb tcpip 5555
adb connect DEVICE_IP:5555
adb devices
```

The device IP is available in its Wi-Fi network details. After confirming the `device` status, you can disconnect the cable. Restarting Android usually requires connecting over USB and running `adb tcpip 5555` again.

## Usage

Keep the game open, unlocked, and with its screen on. Before allowing taps, check the capture:

```bash
python src/test_main.py
```

Close the window with `Esc`, then start the bot:

```bash
python src/main.py
```

When more than one device is connected, the program asks which one to use. To select one explicitly:

```bash
python src/main.py --device DEVICE_SERIAL
```

You may also set `DEVICE_SERIAL` in [src/core/config.py](src/core/config.py). The same `--device` option works in Android-related utilities.

`Esc` stops the bot only while `SHOW_AI_VISION` is enabled; `Ctrl+C` always stops it from the terminal.

## Configuration

Bot settings are in [src/core/config.py](src/core/config.py). The most useful options are:

| Option | Purpose |
|---|---|
| `SHOW_AI_VISION` | Shows the detection and status window. |
| `SHOW_SCRCPY` | Opens the interactive scrcpy mirror. It is not required by the bot. |
| `VISION_FILTER_BY_STATE` | Searches only categories relevant to the current state. |
| `SHOW_DETECTION_LABELS`, `SHOW_FPS`, `SHOW_DETECTION_LAG` | Control details in the vision window. |
| `DEVICE_SERIAL` | Defines the default ADB device. |

The bot uses the direct scrcpy-server stream; the scrcpy window is only a mirror for monitoring or manual interaction.

To interact with the game manually, enable `SHOW_SCRCPY`. The vision window (`SHOW_AI_VISION`) is informational only and does not receive taps.

### Resolution-specific templates

By default, the detector learns templates specific to the device resolution while using default templates. To run the bot using only those resolution-specific templates, keep the updater open in one terminal:

```bash
python src/main_layouts.py --device DEVICE_SERIAL
```

Then run the bot in another terminal:

```bash
python src/main.py --layout-only --device DEVICE_SERIAL
```

Use `--layout-only` only after templates have been learned for that resolution; categories without them will not be detected.

## Templates

When an element is not detected correctly, register a new template:

```bash
python tools/template_selector.py
```

Drag to select the area and press `Enter` to save it. Use `R` or `F5` to capture the screen again.

To inspect and compact template numbering:

```bash
python tools/renumerar.py
python tools/renumerar.py --aplicar
```

The first command only previews changes; `--aplicar` renames files.

## Dataset and AI

To record data during a session:

```bash
python src/main.py --ai-collect
```

Alternatively, enable `DATASET_SAVE` in the configuration. PostgreSQL is optional; dataset format and importing are documented in [docs/dataset.md](docs/dataset.md).

Collection writes images under `dataset/`. Check available storage before enabling it; the default limit is 20 GB (`DATASET_MAX_DISK_MB`).

The training and model execution workflow is documented in [IA/README.md](IA/README.md). The plan to replace templates with a trained detector is in [docs/detector-ia.md](docs/detector-ia.md).

## Tests

Tests are Python scripts and do not require a device:

```bash
python tests/test_detection.py
python tests/test_state_machine.py
python tests/test_pipeline.py
python tests/test_renumerar.py
python tests/test_selector_layout.py
python tests/test_devices.py
python tests/test_ciclo.py
python tests/test_dataset.py
python tests/test_ia.py
```

After changing templates or thresholds, validate the detection regression:

```bash
python tests/test_detection.py
```

`python tests/test_detection.py --update` changes the expected results. Use it only to accept an intentional change and review the diff before saving it.

## Structure

| Path | Responsibility |
|---|---|
| [src/capture/screen.py](src/capture/screen.py) | scrcpy-server video stream. |
| [src/vision/detector.py](src/vision/detector.py) | Template-based detection. |
| [src/vision/worker.py](src/vision/worker.py) | Background vision processing. |
| [src/core/state_machine.py](src/core/state_machine.py) | State priorities and transitions. |
| [src/actions/](src/actions) | Taps and ADB commands. |
| [src/core/config.py](src/core/config.py) | Central configuration. |
| [tools/](tools) | Template, dataset, and maintenance utilities. |
