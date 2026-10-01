# Laser Stuff

Trill sensors on a Bela send generic signals to the Signal Lab on the Mac, which maps them onto osc2laser knobs;
osc2laser drives a Helios laser DAC.

```
Trill Flex/Craft --I2C--> Bela (laser-osc-controller) --/signal/* udp 192.168.7.1:2346--> signal-lab --OSC 127.0.0.1:2345--> osc2laser receiver --USB--> Helios DAC
Open Stage Control (UI) --OSC 127.0.0.1:2345-----------------------------------------------------------------------^
```

## Layout

- `osc2laser/` fork of https://github.com/oliverbyte/osc2laser (upstream `76c637a`, tagged `osc2laser-upstream`).
  **Leave the upstream stack alone.** Only change/audit what we added: `git diff osc2laser-upstream -- osc2laser`.
  Ours: perspective + curve code in `osc-receiver/models.py` (`apply_point_perspective`, `sort_path`, `Parabola`,
  homography helpers, `AlgebraicCurve` → `Cubic`/`Conic`/`Hyperelliptic`), perspective OSC in `osc_input.py`,
  `test_template.py`, `start.sh`, macOS dylibs, `osc-senders/open-stage-control/pavillion-template.json`.
- `signal-lab/` The Signal Lab: `signal_lab.py` (router + web UI on http://127.0.0.1:8000) and `index.html`.
  Knob list and ranges come from `pavillion-template.json`; tabs are derived from knob addresses. Wiring lives in
  `mapping.json` (knob address -> signal, in_min, in_max, scale, and optional lfo signal + depth + hz_max;
  LFO knobs are sent by a 50 Hz loop with the main signal as the centre). Signals register themselves on their first message.
  Template toggles and dropdowns are targets too: a signal rising past the middle of in_min..in_max flips the toggle
  or steps the dropdown (`SKIP` lists values never stepped to, e.g. `/laserobject` 0 Blank).
- `laser-osc-controller/` Bela project: `render.cpp` only. Folder name = Bela project name.
- `.bela-sdk/` Bela headers for IntelliSense (gitignored). Has no Trill/OscSender libs, so compile on the board.
- `.vscode/tasks.json` Bela sync/build/run over ssh, receiver, Open Stage Control, tests.

## Commands

- Everything: `./start.sh` (receiver + Signal Lab, log lines prefixed `[osc2laser]` / `[signal-lab]`, Ctrl-C stops both; quiet by default,
  `-v` logs every signal in/out plus the receiver's `osc_server_*` logging via a temp copy of its config)
- Receiver: `osc2laser/osc-receiver/start.sh` (uses `osc-receiver/.venv`, Python 3.11, made with uv)
- Signal Lab: `osc2laser/osc-receiver/.venv/bin/python signal-lab/signal_lab.py` (`--selftest` runs its asserts)
- Tests: `cd osc2laser/osc-receiver && .venv/bin/pytest -q`
  Feeds every Open Stage Control widget through `handle_osc_message` + renderer. It bypasses the pythonosc
  dispatcher, so new `disp.map` patterns need a separate check against `Dispatcher.handlers_for_address`.
- Bela: VS Code task "Bela: build & run", or `rsync` to `root@bela.local:Bela/projects/laser-osc-controller/`
  then `ssh root@bela.local 'make -C ~/Bela PROJECT=laser-osc-controller run'`.
- Bela detached (survives ssh logout): `make runbg` started nothing here, so use
  `ssh root@bela.local 'cd ~/Bela/projects/laser-osc-controller && (nohup stdbuf -oL ./laser-osc-controller > /root/laser-osc-controller.log 2>&1 &)'`.
  A program started from the Bela IDE keeps running the old binary until restarted. Stop it with `killall laser-osc-controller`
  (not `pkill -f`, which matches and kills the ssh shell running it). Watch with `tail -f /root/laser-osc-controller.log`.
- I2C scan: `ssh root@bela.local i2cdetect -y -r 1`

## Hardware

- Both Bela I2C connectors are bus 1. Trill Flex `0x48` (DIFF, CentroidDetection → `/signal/flex` 0..1),
  Trill Craft `0x30` (DIFF, 30 pads → `/signal/craft/<n>` 0..1, 0 below threshold, each pad auto-ranged to the
  strongest reading seen, starting at full). Craft settings (`kCraftDefaults`: prescaler, noise, bits, speed, threshold, full)
  are live-tunable as in Bela's craft-visual example: `/craft/<name> <float>` or `/craft/calibrate` to udp `192.168.7.2:2347`,
  sent by the Signal Lab's "Trill Craft settings" panel (`POST /craft`). Not persisted: copy the logged `[craft] prescaler …` line back.
- `render()` fills `gIn[]` (names in `kNames`), the aux task sends each as `/signal/<name>` 0..1:
  pots on Analog In 0–1 → `pot/0..1`; KY-023 joystick on 3.3V, VRx/VRy on Analog In 2–3 → `joy/x`, `joy/y`;
  piezos on Audio In L/R (1 MΩ across each) → `piezo/0..1` level envelope;
  buttons on digital 0 (joystick SW) and 1, 10k pull-up to 3.3V, pressed = GND → `joy/button`, `button` (1 = pressed).
- SSD1306 128x64 OLED on I2C bus 1 `0x3C`, optional. Bela listens on udp `:2347` for `/display "<text>"`
  (`\n` = new line, lines ≤ 10 chars drawn double size). The Signal Lab sends the shape name on every `/laserobject` step.
- Tuning knobs live as consts at the top of `render.cpp` (`kPrescaler`, `kNoiseThreshold`, `kCraftDefaults`,
  `kMinChange`, `kPotMax`, `kPotSmooth`, `kPiezoGain`, `kPiezoFloor`, `kPiezoRelease`).
  Keep them; sensors need tuning on the real hardware.
- Bela rules: I2C reads in the aux task, never `render()`; OSC via `sendNonRt()`; builds use `-ffast-math`, so no NaN sentinels.
- Receiver config: `osc-receiver/config_laser1.txt` (driver `libHeliosLaserDAC.dylib`, OSC `0.0.0.0:2345`).

## Receiver notes

- `main.py` starts the laser output + OSC threads; pygame preview runs on the main thread; shared state in `global_data.py`.
- `/laserobject N` indexes `NOTE_LASEROBJECT_MAPPING` in `osc_input.setup()` (0–11). Objects are deep-copied from those prototypes.
- Two perspective systems exist on purpose: `/effect/perspective/*` projects points, `/parameters/homography_*`
  transforms curve coefficients (keeps samples even on screen). Both are in the template.
- Upstream gotcha: `LaserObject.effects` is a class-level list shared by every object.
- When refactoring drawing code, snapshot `point_list` output before/after and diff it.
  `sort_path` tie-breaks can reorder points without changing the drawing.
