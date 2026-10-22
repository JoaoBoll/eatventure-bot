# EatVenture AI/BOT

[Leia em português](README.pt-BR.md)

Bot that automates EatVenture actions on an Android device. The default mode recognizes elements using templates (reference images); the AI model is still experimental.

## Requirements

- Python 3.12 or later.
- [scrcpy](https://github.com/Genymobile/scrcpy) and ADB installed.
- Android with USB or Wireless debugging enabled.

## Setup

Run these commands from the project root in PowerShell:

```powershell
py -3.12 -m venv .venv
```

Creates the project's Python environment.

```powershell
.\.venv\Scripts\Activate.ps1
```

Activates the environment. Repeat when opening a new terminal. On Linux or macOS, create the environment with `python3 -m venv .venv` and activate it with `source .venv/bin/activate`.

```powershell
python -m pip install -r requirements.txt
```

Installs the dependencies needed to run the bot.

```powershell
.\tools\install_scrcpy.ps1
```

Installs scrcpy and ADB on Windows and adds them to `PATH`. On other systems, install them through the available package manager.

On Ubuntu, install `scrcpy`, `scrcpy-server` and `adb` through apt. The bot finds the server beside the executable or in the installation's `share/scrcpy` or `lib/scrcpy` directory, without copying the JAR into the project. The version is detected automatically, supporting scrcpy 1.23 or later; standalone files under `tools/scrcpy` still use the version configured in `SCRCPY_SERVER_VERSION`.

On Linux without `DISPLAY` or `WAYLAND_DISPLAY`, windows are disabled automatically for server use.

## Connect Android

Enable **USB debugging** in **Developer options**, connect the cable, and accept the authorization prompt on the phone.

```bash
adb devices
```

Lists connected devices. The phone should appear with the `device` status.

For Wi-Fi on Android 11 or later, keep the computer and phone on the same network. Enable **Wireless debugging** and open **Pair device with pairing code**.

```bash
adb pair IP:PAIRING_PORT
```

Pairs the phone with the computer. Enter the code shown on Android.

```bash
adb connect IP:CONNECTION_PORT
```

Connects to the paired phone. Use the address on the main **Wireless debugging** screen; its port may differ from the pairing port.

For Android 10 or earlier, connect and authorize over USB first:

```bash
adb tcpip 5555
adb connect PHONE_IP:5555
```

Enables a connection over the same Wi-Fi network. Confirm with `adb devices` before removing the cable.

## Run commands

Keep EatVenture open, with the screen on and unlocked.

```bash
python src/main.py
```

Starts the bot with automatic taps. It also learns and updates templates for the device resolution during use.

By default, messages appear only in the terminal. To save the complete log to a file:

```bash
py src/main.py --log
```

With `--log`, each run saves a UTF-8 file under `logs/eatventure_<date>_<time>_<microseconds>_<pid>.log` without overwriting earlier runs. The file includes logs from `DEBUG` upward, Python text output and errors, plus scrcpy and automatic training output. `LOG_LEVEL` controls only the console; `LOG_DIR` in `src/core/config.py` sets the log directory.

```bash
python src/test_main.py
```

Shows the phone capture without tapping. Use it to check the image before starting the bot.

```bash
python src/main.py --device DEVICE_SERIAL
```

Selects a device using the serial shown by `adb devices`. If neither the option nor the configuration specifies a device, the program asks when more than one is connected. The remaining commands below and the capture viewer also accept `--device`.

```bash
python src/main_layouts.py
```

Learns and updates templates for the current resolution without tapping. Elements must be visible in the game to be recognized.

```bash
python src/main.py --layout-only
```

Runs the bot using only templates already learned for the current resolution. Categories without these templates will not be detected.

```bash
python src/main.py --ai-collect
```

Runs the template bot and collects images and records under `dataset/data/<resolution>/`. Every 10,000 new records across resolutions trigger AI training and a separate JSON report. During training, processes and windows stay open while bot actions and collection pause; both resume automatically when training finishes or fails. After success, consumed records and images are removed while the training cache is preserved. See the [AI commands](IA/README.md) for manual training and using the updated model.

To keep playing and collecting during automatic training, set `AI_PAUSE_BOT_WHILE_TRAINING = False` in [src/core/config.py](src/core/config.py).

```bash
python tools/template_selector.py
```

Registers a template manually. Drag to select an area and press `Enter` to choose a category and save. Use `r` or `F5` to refresh the capture and `Esc` to exit.

Use `Ctrl+C` to stop from the terminal. In the capture viewer, use `Esc`. In the bot and updater, `Esc` also stops execution when the vision window is enabled.

Display settings are in [src/core/config.py](src/core/config.py): `SHOW_AI_VISION` shows detections; `SHOW_SCRCPY` opens the mirror for manual interaction with the game.

## Future plans

Expand data collection and validate operation at the resolutions below. Values are in **short side × long side**, in pixels, and represent coverage targets that still require validation.

| Devices | Planned resolutions |
|---|---|
| Phones | 720 × 1600, 1080 × 2340, 1080 × 2400, 1220 × 2712, 1440 × 3120 |
| Tablets | 800 × 1340, 1200 × 1920, 1600 × 2560, 2136 × 3200 |

- Later validate 1080 × 1920 and the inner screens of foldable phones.
- Implement continued training from the previous model while maintaining performance on previously learned resolutions.
- Improve and validate the experimental AI detector to reduce dependence on templates.
