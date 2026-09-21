[Leia em português/BR](README.pt-BR.md)

# EatVenture AI/BOT

> ## Important observation
>
> The AI is still under development. The bot itself should already execute
> the main flow normally using the detector and templates.
>
> If an item is not detected, is detected in the wrong place, or causes an
> incorrect action, use `tools/template_selector.py` to register it whenever
> possible. If that is not enough, record a short video with the item clearly
> visible, including the device screen resolution. This information is needed
> to adjust templates, scale, search regions, and thresholds.

Vision bot for EatVenture: captures the device video through
scrcpy, locates elements by template matching, and taps
through adb.

## Requirements

Before running the bot, install and prepare the following:

1. Install Python 3.12 or newer.
2. From the project folder, create and activate the virtual environment.
   On Windows PowerShell:

   ```powershell
   py -3.12 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

   On Linux or macOS:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

   Activate `.venv` again whenever you open a new terminal.
3. Install the Python dependencies while `.venv` is active:

  ```bash
  python -m pip install -r requirements.txt
  ```

4. Install [scrcpy](https://github.com/Genymobile/scrcpy) and make sure
  `scrcpy` and `adb` are available on `PATH`. On Windows, you can also
  extract scrcpy under `tools/scrcpy/`; the project finds it automatically.
5. On the Android device, enable **Developer options** and **USB debugging**.
6. Connect the device by USB, accept the debugging authorization prompt, and
  verify the connection:

  ```bash
  adb devices
  ```

  The device must appear as `device`, not `unauthorized` or `offline`.

Use one device whenever possible. If multiple devices are connected, pass the
desired serial with `--device` (see [config.py](src/core/config.py)).

```bash
adb devices
```

## Run

Run every command from the project root, the folder containing `README.md`.
After completing the requirements above, start the bot with:

```bash
python src/main.py
```

Keep the Android game open and visible to the connected device. To check the
screen stream without running detection, use:

```bash
python src/test_main.py
```

If Windows does not recognize `python`, use `py` instead:

```bash
py src/main.py
```

Stop the bot with `Ctrl+C`. If more than one device is connected, specify it:

```bash
python src/main.py --device DEVICE_SERIAL
```

### Which device

With more than one device connected, both programs **ask**:

```
More than one device connected:

  1 - e2615705               22101320G            USB
  2 - 192.168.1.12:37889     22101320G            wifi (same device as 1)

Which one to use? [1]:
```

ENTER accepts the suggestion; you can also type the number or paste
the serial. The suggestion is always **USB** — the adb wifi connection
drops on its own, and when it does, capture dies in the middle of the session.

`(same device as 1)` appears when both have the same
`ro.serialno`: this is the common case of a phone connected by USB **and**
over wifi at the same time.

To avoid seeing the question every time:

```bash
python src/main.py --device e2615705
```

or set `DEVICE_SERIAL` in [config.py](src/core/config.py).

The selected serial is used throughout the entire program (capture **and**
taps). This is not a detail: without `-s`, with two devices in the list,
adb rejects every call with `more than one device` — and the error
appeared far from the cause, as "did not connect to the stream".

The same applies to the tools, which depend on the same
`screencap`:

```bash
python tools/template_selector.py --device e2615705
python tests/android_screenshot.py --device e2615705
```

Without `--device` they ask the same way.

### Which windows appear

In [config.py](src/core/config.py), one option per window —
all four combinations are valid, including both disabled:

```python
SHOW_AI_VISION = True    # AI window, with detection boxes
SHOW_SCRCPY    = False   # scrcpy mirror
```

`scrcpy.exe` is **only a mirror** — the bot's capture does not
go through it. `ScreenCapture` starts its own
`scrcpy-server` and reads the socket directly, while taps go through
adb. So `SHOW_SCRCPY = False` does not affect the bot; it only saves an
H.264 decode and a full render. In that case `scrcpy.exe` is not
started at all. With both disabled, the frame also stops being copied,
because the overlay was the only place that copied it.

The mirror is still useful for manual intervention: the AI window
does not accept taps. If it is your only window,
`SCRCPY_EXTRA_ARGS` accepts options such as `"--stay-awake"` and
`"--always-on-top"`.

**`ESC` only works with the AI window open** — it is the one that
receives keystrokes. Without it, exit with `Ctrl+C`.

What is drawn inside the AI window:

```python
SHOW_DETECTION_LABELS = True  # "category F=.. C=.." text in the boxes
SHOW_FPS              = True  # both FPS values, in the corner
SHOW_DETECTION_LAG    = True  # frame age, in the corner
```

It is worth disabling labels when the screen has many detections
close together: now that the detector finds multiple instances per
category, stacked text gets in the way more than it helps.

### The HUD

```
capture   30.0 fps
detector   3.2 fps  304 ms
lag        535 ms
battery      46 %  charging
renovation   2  1m33s  (last 2m23s)
```

- **capture** — frames per second arriving from the device.
- **detector** — detector passes per second, and the average cost
  of one.
- **lag** — age of the frame that generated the detections on screen.
- **battery** — device level, with a charging indicator.
- **renovation** — how many the bot has completed, the elapsed time since
  the last one, and how long the previous one took.

The terminal also shows a fixed status panel:

- **Dispositivo conectado** — serial of the Android device in use.
- **Voou** — number of completed flight actions.
- **Renovou** — number of completed renovations.
- **Ação** — last executed action; `(Ns atrás)` shows its age.
- **estado** — current state, such as `NORMAL`, `RENOVATE`, `FOOD`,
  `NEW_POINT`, or `UPGRADE`.
- **ações** — total actions executed in the session.
- **rodando** — elapsed session time.
- **fps** — detector rate reported by the vision worker.
- **Sem deteccao** — categories not found and their best observed result,
  when `DETECTOR_DEBUG_MISSES` is enabled.

The AI window overlay also shows:

- **templates** — searched templates over the total available for that pass;
  a partial search can be intentional because of state priority and budgets.
- **template N/M da resolucao** — learned resolution overrides over total
  default templates.
- **deteccoes** — approved detections in the latest pass; zero can be normal
  while the screen settles after an action.
- **aguardando a tela parar** — vision worker is waiting for the action effect
  to settle before accepting another detection.
- **VISAO** — vision thread error; detection has stopped and the bot should not
  act until the problem is fixed.

Each detection box can show the template origin (`default <resolution>` or a
resolution override), category or food name, `F` for shape/template
confidence, and `C` for color similarity.

When investigating a detection failure, record the exact frame resolution, the
HUD text, and a short video in which the item remains clearly visible long
enough to observe the attempted detection. Do not crop the item out or hide
the surrounding screen context.

The two FPS values are very different, and that comparison is exactly
what diagnoses the issue: capture at 60 with the detector at 3
means detection is the bottleneck, not capture.

The detector turns **red** below `1 / MAX_DETECTION_AGE`
— at that point detections are born older than the limit and
the state machine stops clicking instead of hitting where the
object was. It is the same limit used by the age check, not a
separate number. Lag turns red at half the limit.

The **battery** turns red below `BATTERY_WARNING_LEVEL`
(20%) when it is not charging. This matters more than it seems: the bot
runs for hours, and a session that dies from an empty battery leaves
no trace in the log — the bot simply stops acting.

`dumpsys battery` costs **~56 ms**, more than 3x a full detector pass
in the `UPGRADE` state. That is why it is **never** called from
the render loop: it runs in its own thread
([core/battery.py](src/core/battery.py)) every
`BATTERY_POLL_INTERVAL` (30 s), and the HUD only reads the latest value from
memory. If the reading goes more than twice the interval without updating,
the HUD shows its age alongside it — a frozen number looks current, and
that is worse than a missing number.

The level is calculated as `level / scale`, not `level` directly:
the `dumpsys` scale is not always 100.

### Elapsed time between renovations

It is the only HUD number that measures **progress**. FPS values say
that vision is healthy; they do not say that the bot is moving — it
could be clicking nothing at 30 fps for twenty minutes.

The timer resets when the bot acts on `build` or `plane`
(`CYCLE_CATEGORIES`), which are the two paths to `RENOVATE`, that is,
the two ways to move on from a restaurant.

The timestamp is recorded inside `_act`, not in `_apply_rules`:
`_act` is the point where the action actually **went out**. Recording it earlier
would count a cycle for an attempt blocked by cooldown or by a busy worker
— and the counter would then increase every frame.

It turns **red** when the elapsed time exceeds
`CYCLE_STALL_FACTOR` (3x) the previous cycle. The reference is the
previous cycle rather than a fixed number, because there is no "normal
time": each restaurant takes as long as it takes, and they keep getting
slower. Without a previous cycle it never turns red — without a reference,
"slow" means nothing.

Before the first renovation, elapsed time counts from startup,
which is also useful information: "8 minutes and it still has not moved
on from the restaurant".

## Structure

| Module | Responsibility |
|---|---|
| [capture/screen.py](src/capture/screen.py) | H.264 stream from scrcpy-server, versioned frame |
| [vision/detector.py](src/vision/detector.py) | two-stage template matching |
| [vision/worker.py](src/vision/worker.py) | runs the detector outside the main loop |
| [core/state_machine.py](src/core/state_machine.py) | what to do with each detection |
| [actions/manager.py](src/actions/manager.py) | action → tap, in its own thread |
| [actions/android.py](src/actions/android.py) | adb commands |
| [core/devices.py](src/core/devices.py) | lists and chooses the device |
| [core/battery.py](src/core/battery.py) | reads the battery outside the critical path |
| [dataset/recorder.py](src/dataset/recorder.py) | records frame + labels for training |
| [dataset/store.py](src/dataset/store.py) | dataset index in PostgreSQL |
| [core/config.py](src/core/config.py) | **every** adjustable value |
| [tools/regras.py](tools/regras.py) | prints and validates priorities |
| [tools/renumerar.py](tools/renumerar.py) | compacts template numbering |
| [tools/selector_layout.py](tools/selector_layout.py) | selector scale and coordinates |

## Adjusting templates and thresholds

To crop a new template:

```bash
python tools/template_selector.py
```

With more than one device connected it asks which one to use, like
`main.py` (see [Which device](#which-device)).

`R`/`F5` captures again, dragging selects, and `ENTER` saves
in the selected category. The rectangle shows the crop size in
**device pixels**, which is what matters for the template.

### Window size

The window used to be fixed at 500x900, which on a 1080x2400 device
screen gives a scale of 0.375 — 1 pixel on screen was worth 2.7 device
pixels, making cropping imprecise.

Now it **follows the height of your screen** (80% of it by
default) and maintains the image aspect ratio. The device screen appears
in full, all at once.

Measured on a 3440x1440 monitor at the time of writing:

| `SELECTOR_HEIGHT_FRACTION` | Window | Scale | 1 px on screen = |
|---|---|---|---|
| before (fixed 500x900) | 500x900 | 0.375 | 2.7 px |
| 0.70 | 453x1007 | 0.420 | 2.4 px |
| **0.80 (default)** | **518x1152** | **0.480** | **2.1 px** |
| 0.90 | 583x1296 | 0.540 | 1.9 px |
| 1.00 | 648x1440 | 0.600 | 1.7 px |

The scale never exceeds 1.0: enlarging does not create detail; it only
makes the crop blurry and harder to get right.

Settings in [config.py](src/core/config.py):

```python
SELECTOR_HEIGHT_FRACTION = 0.80   # fraction of screen height
SELECTOR_WIDTH_FRACTION = 0.95    # only protects against a landscape monitor
SELECTOR_MAX_WIDTH = None         # None = detects the screen
SELECTOR_MAX_HEIGHT = None
```

The scale and coordinate math lives in
[tools/selector_layout.py](tools/selector_layout.py), separate from the
GUI and covered by [tests/test_selector_layout.py](tests/test_selector_layout.py)
— a mapping error here would silently save the wrong crop, and the bad
template would only show up as "the bot clicks in the wrong place"
weeks later.

### Automatic numbering

When saving, the selector **compacts the sequence** before writing:
if `item_005` is missing between 004 and 006, 006 becomes 005, 007
becomes 006, and the new template gets the last number.

This makes "next number" equal to `len + 1` again, without ambiguity —
previously a gap made the calculation point to a file that already
existed, and the new template would silently **overwrite** the old one.

To compact manually (useful after deleting templates):

```bash
python tools/renumerar.py              # shows what would change
python tools/renumerar.py --aplicar    # renames
python tools/renumerar.py --aplicar --categoria food
```

The default is to only show changes, because renaming is irreversible. It
processes in ascending order — which makes collisions impossible,
since each file's target is always less than or equal to its number,
and anything not yet processed has a higher number. This is verified
by brute force in `tests/test_renumerar.py`, across all combinations of
gaps up to 11 files.

Renaming does not invalidate the regression: the golden stores category,
position, and confidence — not the filename.

After cropping or changing a threshold, **run the
regression**:

```bash
python tests/test_detection.py          # what changed?
python tests/test_detection.py --bench  # how much does a pass cost?
python tests/test_detection.py --update # accept the new expected result
```

It compares the result with [tests/golden/expectations.json](tests/golden/expectations.json)
and points out what is no longer detected (`MISSED`) and what
started being detected (`EXTRA`, a false-positive candidate).

To expand the set:

```bash
python tests/android_screenshot.py --fixture name.png
python tests/test_detection.py --update
```

and **check the diff before committing** — the golden file is only
as trustworthy as that review. Screens where nothing should be
detected are just as useful as the others: they catch false
positives.

There are two image folders, and the distinction matters:

| Folder | What | Git |
|---|---|---|
| `tests/images/` | regression fixtures | tracked |
| `tests/capture/` | selector working capture | ignored |

`template_selector.py` writes to the second one. It used to always write
to `tests/images/screen.png`, so every cropped template overwrote the
regression fixture and invalidated the baseline without warning.

Name fixtures for what they cover — `new_point.png`,
`food_stations.png`, `up_food.png`. **Never `screen.png`**: that is
the name of the selector's working capture. An image in
`tests/images/` without a golden entry becomes a warning, not a failure,
so a stray capture there does not break the test.

While cropping templates, disable
`VISION_FILTER_BY_STATE` in [config.py](src/core/config.py):
with the filter enabled, the overlay only shows categories from the
current state, which is easy to mistake for "the detector stopped
finding things".

## Tests

```bash
python tests/test_detection.py       # detection regression
python tests/test_state_machine.py   # priority, cooldown, timeout
python tests/test_pipeline.py        # integration, with fake adb (includes headless mode)
python tests/test_renumerar.py       # numbering compaction
python tests/test_selector_layout.py # selector coordinates
python tests/test_devices.py         # device selection
python tests/test_ciclo.py           # elapsed time between renovations
python tests/test_dataset.py         # training dataset recording
```

None of them needs a device.

## State machine priorities

The order of the rules in [state_machine.py](src/core/state_machine.py)
**is** the priority. In `NORMAL`:

| # | Category | Action | Goes to |
|---|---|---|---|
| 1 | `open_store` | taps | — |
| 2 | `close` | clicks the X | — |
| 3 | `gray_max` | taps a neutral point | — |
| 4 | `gray_coin` | taps a neutral point | — |
| 5 | `up_food` | **long press** on the button, upgrading food | — |
| 6 | `plane` | clicks | `RENOVATE` |
| 7 | `build` | clicks | `RENOVATE` |
| 8 | `upgrade` | clicks | `UPGRADE` |
| 9 | `new_point` | clicks | `NEW_POINT` |
| 10 | `box` | clicks | — |
| 11 | `food` | clicks | `FOOD` |

The first four close things that should not be open, which is why
they come before any game action.

The fifth is different: `up_food` in `NORMAL` does the **same** as
in `FOOD` — a long press of `UPGRADE_FOOD_PRESS` seconds on the button,
upgrading food. This is deliberate, and it is worth knowing the cost:
the food panel sometimes opens accidentally, in which case the bot
spends currency and remains stuck for the duration of the press. It also
does not enter the dismissal ladder (see below), because
`upgrade_food` is not a `DISMISS_ACTION` — if the panel does not
close, the only signal is `REPEATED_ACTION_WARNING`.

**There is no priority number written anywhere** — the order of the
list IS the priority. Previously there were comments such as
`# PRIORITY 1 → PLANE` scattered across 200 lines of `if`, which
meant two sources of truth: changing the order without changing the
comment left the code lying. To reorder, move the line.

### How to monitor

```bash
python tools/regras.py
```

Prints numbering derived from the order, in every state, with
timeout, each action's behavior, and destination. It also checks three
things that runtime does not report:

- rule pointing to a category **without a template** (dead rule,
  can never trigger)
- rule pointing to a **nonexistent action** in `ACTION_TABLE`
- category template that **no rule uses** (detection cost with no use)

Exits with code 1 if it finds a problem, so it works in a commit
hook. It was the tool that caught the rule pointing to
`renovate_coin` after the folder was renamed to `renovate`.

To follow decisions **in real time**, set
`LOG_LEVEL = "DEBUG"` in [config.py](src/core/config.py):

```
D [state] NORMAL prio 7/10: upgrade -> upgrade | on screen: food(0.97) box(0.93) upgrade(1.00)
I [state] NORMAL -> UPGRADE
```

The line says which priority won **and what it beat** — answering
"why did it click this and not that?".

`up_food` upgrades food in both states, deliberately:

| State | What it does | Where | Duration | Spends currency? |
|---|---|---|---|---|
| `NORMAL` | upgrades food | center of the detection | 4 s | **yes** |
| `FOOD` | upgrades food | center of the detection | 4 s | **yes** |

The state machine intentionally keeps the same behavior in both states.
It can spend currency and remain busy for the duration of the press if
the food panel opens unexpectedly. To restore panel dismissal, change the
`NORMAL` rule to the `dismiss` action; that action is implemented and
covered by `tests/test_pipeline.py::test_dismiss_toca_no_ponto_neutro`.

Because `NORMAL` has no timeout (it is the base state), a rule that
triggers without resolving anything would repeat forever — and finding
something resets exploration, so the swipe does not come to the rescue.
Hence `REPEATED_ACTION_WARNING`: after N identical consecutive actions,
a warning appears in the log.

### Why the bot does not act twice on the same screen

Symptom: it closed "MAX" and tapped **again** at the same point, which
**reopened** the panel — because `DISMISS_POINT` is also a point that
opens something.

Two causes combined:

1. `ACTION_COOLDOWN` (0.5 s) expired before there was a frame
  after the action, because detector lag is ~0.535 s. The
  machine was deciding based on a screen from **before** its own tap.
2. Even a frame after the tap still shows the panel while the closing
  animation has not finished.

That is why the condition in `_can_act` is not temporal, but **causal**:
it only acts on a frame **captured** at least `ACTION_SETTLE` after
the last action. Increasing the cooldown would not solve it — a slower
detector would exceed the margin again.

Simulated in the time domain, with the panel genuinely opening and closing
and the machine seeing with delay (taps at the point, and how many of them
with the panel **already closed**):

| `ACTION_SETTLE` | anim 0.10 s | anim 0.20 s | anim 0.30 s | anim 0.40 s |
|---|---|---|---|---|
| 0 (causal guard only) | 5t **2miss** | 5t **2miss** | 5t **2miss** | 5t **2miss** |
| 0.20 | 1t 0miss | 1t 0miss | 5t **2miss** | 5t **2miss** |
| **0.40 (config)** | 1t 0miss | 1t 0miss | 1t 0miss | 1t 0miss |

The rule is `ACTION_SETTLE >= game animation`. Lowering it to 0.1
brings the double tap back, and there is a test for it.

**The cost**: the interval between actions becomes
`settle + detector lag`. With lag at 0.535 s, the ceiling
drops from ~1.9 to ~1.1 actions per second. This is deliberate — an
incorrect action that undoes the previous one costs more than half an
action per second.

### Closing the panel: why a ladder exists

**No fixed point is safe at an arbitrary scroll position.**
`DISMISS_POINT`, currently (10, 2200), is next to the bottom button
bar, so it can itself OPEN a panel. If that panel shows "max", the
`gray_max` rule taps the same point, reopening it: an infinite cycle
where the point that caused the problem is used to solve it.

Across the 4 fixtures, the only truly inert areas are in the Android
status bar — where tapping is worse. And the interior changes completely
between restaurants.

**But there is a scroll position where the bottom corner is empty:
with the screen scrolled all the way down.** Hence the ladder:

```
gray_max → gray_max → gray_max → scroll_bottom → gray_max → ...
```

| Config | What |
|---|---|
| `DISMISS_ACTIONS` | which actions escalate (`dismiss`, `gray_max`) |

| `DISMISS_ATTEMPTS_BEFORE_SCROLL` | attempts at the point before scrolling (3) |
| `SCROLL_BOTTOM_DIRECTION` | `"up"` — finger up, **view moves down** |
| `SCROLL_BOTTOM_SWIPES` | 6, enough to reach the end |

The cycle **repeats** instead of giving up: one warning is emitted per
round, and the count resets when a normal action occurs (the bot escaped
the hole) or when the state changes.

> **Do not use Android BACK here: in this game it EXITS THE GAME.**
> `android.back()` remains implemented, but is deliberately outside
> `ACTION_TABLE`, and two tests fail if someone reintroduces it.

Swipe alone does not solve it — swiping does not close a panel, it only
makes the loop slower. It is used to *reach* the scroll position where
the point works.

Today **only `gray_max` escalates**: no rule uses the action
`dismiss`, because `up_food` in `NORMAL` performs `upgrade_food`. The
`dismiss` action (a `DISMISS_HOLD_DURATION` hold at `DISMISS_POINT`,
converted to the device resolution) remains implemented and covered by
`tests/test_pipeline.py::test_dismiss_toca_no_ponto_neutro`,
ready to return to the rules — code that nobody exercises decays
without anyone noticing.

## When it does not open

There were **three** stacked causes, and one hid the other.

### 1. Socket without `scid` (the underlying cause)

In scrcpy 4.1 the server's abstract socket is **always**
`scrcpy_<8 hex>` — a plain `scrcpy` does not exist. The code
forwarded to `localabstract:scrcpy`, which never exists:

```
$ adb shell cat /proc/net/unix | grep scrcpy
@scrcpy_7d7c122f
@scrcpy_6edc9dfd     ← what the server creates
$ adb forward --list
tcp:27283 localabstract:scrcpy    ← where we pointed
```

adb accepts the TCP connection and **only then** tries to open the socket
on the device. If it does not exist, you receive 0 bytes. That is why the
log said "Socket connected" and then died.

Fixed: a random `scid` per run, passed to the server and used in the
forward.

### 2. Port shared with the mirror

`scrcpy.exe` uses 27183-27199 by default, and capture used
27183. The symptom in the scrcpy log was:

```
WARN: Could not listen on port 27183, retrying on 27184
```

**This is what made it "sometimes work":** when the mirror
got port 27183, its forward pointed to a valid
`scrcpy_<scid>` socket — and we connected to *its* tunnel,
accidentally receiving the mirror's stream. Fixing the port removed
this crutch and exposed cause no. 1.

Fixed: `SCRCPY_PORT = 27283` and a dedicated `SCRCPY_DEVICE_JAR`
(both used to `adb push` to the same file at the same time, and our
`stop()` deleted it).

### 3. Connecting too early and giving up too quickly

The server takes **~1.4 s** between starting and serving the first byte,
and takes longer with the mirror running. The code slept 0.5 s and
treated EOF as fatal.

The fix distinguishes two cases that look the same:

| `recv` result | Meaning | What to do |
|---|---|---|
| **0 bytes** | abstract socket does not exist yet | close and reconnect |
| **timeout** | server accepted, but sent nothing yet | **wait on the same socket** |

Closing on timeout drops a good connection — and the server accepts
**only one client**, so the second attempt finds a dead server.

### Verified on the device

| Scenario | Result |
|---|---|
| Capture alone | start in 1.49 s, 71 fps |
| Capture + mirror together | start in 1.98 s, 31 fps |
| Mirror survives our `stop()` | yes |

### Other causes

| Symptom | Likely cause |
|---|---|
| No frames, device screen off | the encoder captures the display; wake the screen |
| `Error sending scrcpy-server` | incorrect `SCRCPY_SERVER_PATH` in config |
| Frames arrive but nothing is detected | `VISION_FILTER_BY_STATE` enabled; disable it to see all categories |
| Bot sees but does not click | check `lag` in the HUD — above `MAX_DETECTION_AGE` it deliberately stops clicking |

To investigate manually:

```bash
adb shell cat /proc/net/unix | grep scrcpy   # does the socket exist?
adb forward --list                            # where does it point?
```

Emergency shortcut: `SHOW_SCRCPY = False`. The mirror is not
used by the bot.

## Recording a training dataset

Dataset recording is **off by default**. Enable it for a run with
`--ai-collect`, or set `DATASET_SAVE = True` in [config.py](src/core/config.py):

```python
DATASET_SAVE = False
DATASET_DIR = PROJECT_ROOT / "dataset"
DATASET_IMAGE_FORMAT = "jpg"    # 4.3x smaller than png
```

It records 19 dataset action categories, including the two exploration
swipes.

For each action, it records the frame that motivated the decision and the
labels produced by template matching: **all boxes** in the frame with
their category, which one became the action, the tap point in frame
pixels, and the **result** (did the target leave the screen?).

Boxes rather than only the click point because one point per image is
ambiguous when there are multiple targets and does not teach how many
exist — with boxes, the task is object detection, the same as the matcher,
with much more labeling per image. And the result because it allows
training only on actions that **worked**, instead of inheriting every
mistake from the teacher.

Historical device measurement: **no impact** on the bot (lag 58 → 50 ms,
capture 30 fps in both cases) — recording runs in a thread with a
queue that discards items when full.

Space: **2.10 MB** per frame in PNG, **0.49 MB** in JPG q92. With
the bot acting ~1x/s, 7.6 GB/hour versus 1.8 GB/hour. There is a cap in
`DATASET_MAX_DISK_MB`.

**`samples.jsonl` is what trains** — it contains the image path,
all boxes with their category, the click point, and the result.
The database is optional and stores only **metadata**: the image stays in
a file, because training by fetching BLOBs every epoch is slow and the
dataset would no longer be copyable with `rsync`.

```bash
psql -h host -U usuario -d eatventure -f docs/schema.sql
python tools/dataset_import.py --dsn postgresql://user:password@host:5432/eatventure
```

The PostgreSQL driver is optional and is commented out in
`requirements.txt`; install `psycopg[binary]` separately when using the
database. The importer also accepts `DATASET_DB_DSN` from the config. Its
default `dataset/samples.jsonl` path is the legacy flat layout; for a current
collection, pass a shard explicitly, for example:

```bash
python tools/dataset_import.py --jsonl dataset/data/1088x1742/samples.jsonl
```

Format, table DDL, indexes, and useful queries:
**[docs/dataset.md](docs/dataset.md)**.

## Future plans

- [docs/detector-ia.md](docs/detector-ia.md) — replace template
  matching with a trained detector: what can be reused, what goes away,
  and the step-by-step process.

## Detector cost

The cost is **linear in the number of templates** — each one is a
full-screen scan. The historical measurement below used **106** templates,
**75 of them are
`food`**.

### The bottleneck: global scale constrained by the smallest template

The coarse stage searches a reduced copy of the frame. Below the detector's
internal `MIN_COARSE_SIDE` threshold (12 px), the template does not survive
the reduction,
the coarse stage is abandoned, and the search falls back to full
resolution — which is precisely the slow one.

With a **global** scale, it is constrained by the SMALLEST template
of all (`up_upgrade`, 32 px → 0.40), and the large ones pay the cost.
`food` has a median size of 80x93: it can handle 0.15–0.20.

The scale is now **derived per template**, as
`MIN_COARSE_SIDE / smallest_side`, rounded up to the
`COARSE_SCALE_STEP` grid. There is no category table to maintain
by hand, because the rule is exact: the cost explodes *precisely*
when the scale falls below this floor.

Historical device measurement (1080x2400, 106 templates, 30 fps capture):

| | detect | average lag | p95 | worst |
|---|---|---|---|---|
| `NORMAL` before (global scale) | 916 ms | 1360 ms | 2163 ms | 2251 ms |
| **`NORMAL` now** | **156 ms** | **255 ms** | **342 ms** | **372 ms** |
| `UPGRADE` before | 33 ms | 73 ms | 102 ms | 112 ms |
| **`UPGRADE` now** | **16 ms** | **34 ms** | **45 ms** | **65 ms** |

**5.9x** in `NORMAL`. Detections remain identical — same coordinates,
same set, confidence differing at the 6th decimal place (refinement
noise). The cost was buying no precision at all.

This also removed lag from the danger zone: p95 was 2163 ms against a
`MAX_DETECTION_AGE` of 2000 ms — half of the slow detections were being
discarded as too old before becoming actions.

The reduced frame is built **on demand**, one per scale used: in
`UPGRADE`, with 4 templates, there is 1 resize rather than 7. The 7
resizes cost 7.9 ms together, versus ~700 ms for searching.

### What remains

- **`CATEGORY_ROIS`** in [config.py](src/core/config.py), still
  empty. Restricting each category to the part of the screen where it
  can appear reduces cost and false positives together. A wrong ROI
  hides good detections, so it must be checked in the game.
- **Priority cascade**: `food` is the last `NORMAL` rule, so searching
  for it was always wasteful when something with higher priority was on
  screen. It helps less than it seems — sampling 12 real screens, none
  had any detection, which is the cascade's worst case.
- **Cropping templates does not solve it**: cross-comparing the 75
  `food` templates, only 3 pairs overlap (~25 ms out of 876). The dishes
  are genuinely distinct.
