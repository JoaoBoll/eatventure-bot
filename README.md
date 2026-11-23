# EatVenture AI/BOT

## Languages

- [English](README.md)
- [Português do Brasil](README.pt-BR.md)
- [Français (configuration requise)](docs/requirements.fr-FR.md)

Would you like to add or update a translation? See
[CONTRIBUTING.md](CONTRIBUTING.md).

Bot that automates EatVenture actions on an Android device. The default mode recognizes elements using templates (reference images); the AI model is still experimental.

This is exclusively a local/personal project, including use on a local network.
It is not designed as a public service or a product for end users. The local
dashboard therefore does not require authentication by default; tokens remain optional.

The web dashboard supports both template detection and trained AI models.
Select the recognition mode in the device settings, save, and restart. The
Vision tab streams the active detector's boxes and confidence scores in both
modes. AI mode requires a trained category model; automatic selection uses
the capture resolution. Region proposals can come from templates or the
experimental color/contour proposer.

BLOCK regions affect only `box`, `food` and `new_point`. Other
categories, including `engine`, keep their normal search areas. Use
`python tools/block_selector.py` to select multiple regions visually, or use
**Add BLOCK** in the dashboard. Both use the same device profile under
`local/devices/`; restart the bot after saving to apply the changes.

The bot retries failures up to three times, waiting three seconds between
attempts and logging the outcome. Manual stops are not retried. Five consecutive
detector failures or an unexpected capture shutdown trigger recovery as well.

## Requirements

- 64-bit Windows 11, Windows 10 22H2, or Linux (for example, Ubuntu 24.04 LTS or later). For a new installation, prefer Windows 11 or Linux. Regular support for Windows 10 ended on October 14, 2025. [Windows 10 lifecycle](https://support.microsoft.com/en-us/windows/deployment/updates-lifecycle/windows-10-support-has-ended-on-october-14-2025)
- 64-bit Python 3.12 with `pip` and `venv`. Install the dependencies from [requirements.txt](requirements.txt) and, for the dashboard, also install those from [requirements-web.txt](requirements-web.txt).
- To play or collect data, use an Android device that can run EatVenture, with authorized USB or wireless debugging, ADB, and [scrcpy](https://github.com/Genymobile/scrcpy) 1.23 or later. You can train on saved captures with the phone disconnected.

### Hardware for the operating system and EatVentureAI

These estimates include the operating system and assume **one phone**, capture at up to **1080 × 2400**, about **10,000 images**, **30,000 replay crops per resolution**, and **200 trees**. Training runs on the CPU. Integrated graphics are sufficient for regular use.

| Environment | Bot and dashboard without active training | Bot, dashboard, and training on the same machine |
| --- | --- | --- |
| Windows 10 or 11 | 4 cores, 8 GB of RAM | 6 cores, 16 GB of RAM |
| Linux with a desktop environment | 4 cores, 8 GB of RAM | 6 cores, 16 GB of RAM |
| Headless Linux | 2 cores, 4 GB of RAM | 4 cores, 8 GB of RAM with the bot paused during training and a low job count. For more headroom, use 16 GB. |

For Windows or Linux with a desktop environment, the recommended configuration for this scenario is **6 cores, 16 GB of RAM, and a 256 GB SSD**. Consider **32 GB of RAM** if you run multiple bots, train without pausing the bot, or increase the replay limit to around 100,000 crops. Performance depends on the CPU, resolution, batch size, and job count.

Start with the bot set to pause during training. In the dashboard, use 1 to 2 jobs on a machine with 8 GB of RAM or 2 to 4 jobs with 16 GB. Adjust based on CPU and memory usage.

Leave **30 GB of free SSD space after installing the operating system and the application** for captures, caches, models, and logs. For reference, 10,000 images averaging 300 to 600 KB take up 3 to 6 GB. Feature vectors for 30,000 crops take up about 187 MB on disk in `float16`, before indexes and metadata. The feature archive and test set grow as you collect more captures and add resolutions. Allow space to back up those files.

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

### Web control panel

With the virtual environment activated, install the panel dependencies:

```powershell
python -m pip install -r requirements-web.txt
```

On Linux, start the panel in the background:

```bash
python3 src/start-web.py
```

On Windows, run `py src/start-web.py`. With the virtual environment activated, you can run `python src/start-web.py` on either system. The launcher uses the project's `.venv` when available and writes output to `logs/web.log`. On Linux, the panel keeps running after the SSH session closes.

Open `http://localhost:8765` on the same computer. To allow local access only, pass `--host 127.0.0.1` to the launcher. The panel finds ADB-authorized devices and can start, stop and configure their bots. See [the panel guide](docs/painel-web.md) for access tokens, another port, LAN use and a `systemd` service that starts the panel after a reboot.

To stop the panel, use **Encerrar painel** in the interface or run:

```bash
python src/stop-web.py
```

On Debian or Ubuntu, if `pip` reports `externally-managed-environment`, install virtual-environment support and use `.venv`; do not use `--break-system-packages`:

```bash
sudo apt update
sudo apt install -y python3-venv
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-web.txt
python src/start-web.py
```

If Python was installed separately and `venv` still fails, install its matching package, for example `sudo apt install -y python3.14-venv`.

The optional [web control panel](docs/painel-web.md) manages multiple devices, starts/stops bots and saves local settings per device. It runs on a headless server; the terminal commands below remain available. The panel and its setup guide are in Portuguese.

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
