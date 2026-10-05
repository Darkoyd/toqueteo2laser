# Laser Stuff

Trill sensors on a Bela send generic signals to the Signal Lab on the Mac, which maps them onto osc2laser knobs;
osc2laser drives a Helios laser DAC. The Bela also plays a sound per signal on its audio out whenever the signal moves,
if the signal is wired to a knob in the Signal Lab and not muted there.

```
Trill Flex/Craft --I2C--> trill-oled --udp 127.0.0.1:2346--> sclang (_main.scd) --/signal/* udp <mac>:2346--> signal-lab --OSC 127.0.0.1:2345--> osc2laser receiver --USB--> Helios DAC
pots, joystick, piezos, pulse sensor, buttons --> scsynth (Bela audio core) --^    \--> sounds --> Bela audio out
Open Stage Control (UI) --OSC 127.0.0.1:2345------------------------------------------------------------------------------------------------^
```

## Layout

- `osc2laser/` fork of https://github.com/oliverbyte/osc2laser (upstream `76c637a`, tagged `osc2laser-upstream`).
  **Leave the upstream stack alone.** Only change/audit what we added: `git diff osc2laser-upstream -- osc2laser`.
  Ours: perspective + curve code in `osc-receiver/models.py` (`apply_point_perspective`, `sort_path`,
  homography helpers, `AlgebraicCurve` → `Cubic`(→`Cubic2`)/`Conic`(→`Parabola`)/`Hyperelliptic`, `SvgObject`), perspective OSC in `osc_input.py`,
  `test_template.py`, `start.sh`, ILDA output fix in `laser_output.py` (y flipped `4095 - y`, since drawing is y-down
  but ILDA/Helios is y-up; x flipped `4095 - x`, the projection came out mirrored; intensity `i` = 255), macOS dylibs, `osc-senders/open-stage-control/pavillion-template.json`.
- `signal-lab/` The Signal Lab: `signal_lab.py` (router + web UI on http://127.0.0.1:8000) and `index.html`.
  Knob list and ranges come from `pavillion-template.json`; tabs are derived from knob addresses. Wiring lives in
  `mapping.json` (knob address -> signal, in_min, in_max, scale, and optional lfo signal + depth + hz_max;
  LFO knobs are sent by a 50 Hz loop with the main signal as the centre). Signals register themselves on their first message.
  Template toggles and dropdowns are targets too: a signal rising past the middle of in_min..in_max flips the toggle
  or steps the dropdown (`SKIP` lists values never stepped to, e.g. `/laserobject` 0 Blank).
  Each signal chip has a mute button for its Bela sound (`POST /mute`, saved in `muted.json`; it still drives its knobs).
- `laser-osc-controller/` Bela project, runs at boot. Folder name = Bela project name. Bela runs `run.sh`, which builds
  `trill-oled.cpp` (into `build/`, when the source is newer) and starts it, then `sclang _main.scd`.
  Only one program can own the Bela audio core, so SuperCollider owns it and `trill-oled` is a plain Linux program (no `Bela.h`).
  - `_main.scd` SuperCollider: reads pots, joystick, piezos, pulse sensor and buttons (`AnalogIn`/`SoundIn`/`DigitalIn` → `SendReply`),
    receives the Trill signals on udp `:2346`, forwards every `/signal/<name>` to the Mac and plays its sound if it's in `~audible`.
  - `trill-oled.cpp` Trill Flex + Craft + OLED on I2C, sends `/signal/*` to sclang, listens on udp `:2347`.
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
- Bela at boot: set with `make -C ~/Bela PROJECT=laser-osc-controller startup` (writes `/opt/Bela/startup_env`; it was
  `manglar-capacitivo` before). Log: `journalctl -fu bela_startup`. After a sync, restart it with `systemctl restart bela_startup`.
  `make run` (the VS Code task) stops the boot service first, since only one Bela audio program can run; it comes back
  on the next boot or with `systemctl start bela_startup`. `make -C ~/Bela stop` stops both.
- Bela detached without the boot service:
  `ssh root@bela.local '(nohup make -C ~/Bela PROJECT=laser-osc-controller runonly > /root/laser-osc-controller.log 2>&1 &)'`,
  stop with `killall sclang scsynth` (`trill-oled` exits with `run.sh`). Watch with `tail -f /root/laser-osc-controller.log`.
- I2C scan: `ssh root@bela.local i2cdetect -y -r 1`
- Wi-Fi: the Bela runs a hotspot `bela-laser` (WPA2, password in `/etc/hostapd/hostapd.conf` on the Bela) on its
  RTL8188FU USB dongle (`0bda:f179`, out-of-tree driver `kelebek333/rtl8188fu` in `/root/rtl8188fu`, kernel 4.14 has none).
  `wlan0` stanza in the Bela's `/etc/network/interfaces` (original in `interfaces.orig`): static `192.168.8.2`,
  starts hostapd and its own dhcpd (`/etc/dhcp/dhcpd-wlan0.conf`), which gives the Mac `192.168.8.1`
  (same scheme as USB: Bela `.7.2`, Mac `.7.1`). The Signal Lab sends `/hello` every second to the Bela's `:2346` on both
  links; `_main.scd` sends signals only to whoever said it last, and to nobody after `~helloTimeout` s of quiet
  (sending to a gone Mac made each `sendMsg` throw and pinned sclang's CPU). `/hello`'s args are the signals that may sound
  (`audible()`: wired to a knob as signal or LFO rate, not muted), also sent right away when wiring or mutes change;
  sclang keeps them in `~audible` (`[sc] sounds: …` log line), empty until the first hello. The Signal Lab replies (`/display`, `/craft`)
  to whichever address the signals come from. Over Wi-Fi: `ssh root@192.168.8.2`.

## Hardware

- Both Bela I2C connectors are bus 1. Trill Flex `0x48` (DIFF, CentroidDetection → `/signal/flex` 0..1),
  Trill Craft `0x30` (DIFF, 30 pads → `/signal/craft/<n>` 0..1, 0 below threshold, each pad auto-ranged to the
  strongest reading seen, starting at full). Craft settings (`kCraftDefaults`: prescaler, noise, bits, speed, threshold, full)
  are live-tunable as in Bela's craft-visual example: `/craft/<name> <float>` or `/craft/calibrate` to udp `<bela>:2347`,
  sent by the Signal Lab's "Trill Craft settings" panel (`POST /craft`). Not persisted: copy the logged `[craft] prescaler …` line back.
- `_main.scd` reads these (names in `~names`) and sends each as `/signal/<name>` 0..1:
  pots on Analog In 0–1 → `pot/0..1`; KY-023 joystick on 3.3V, VRx/VRy on Analog In 2–3 → `joy/x`, `joy/y`;
  piezos on Audio In L/R (1 MΩ across each) → `piezo/0..1` level envelope;
  PulseSensor on 3.3V, signal on Analog In 4 → `heart/beat` (1 on each beat, falls to 0 in `~heartRelease`) and
  `heart/bpm` (median of 5 beats, `~bpmRange` 40..160 → 0..1, 0 after 3 s without a beat, no sound; `[heart]` log line per beat);
  buttons on digital 0 (joystick SW), 1 and 2, 10k pull-up to 3.3V, pressed = GND → `joy/button`, `button`, `horn` (1 = pressed).
  `horn` plays `sounds/airhorn.wav` (`~hornFile`, `~hornVolume`; scsynth reads wav/aiff, not mp3) on every press, wired in the Signal Lab or not.
- SSD1306 128x64 OLED on I2C bus 1 `0x3C`, optional. Bela listens on udp `:2347` for `/display "<text>"`
  (`\n` = new line, lines ≤ 10 chars drawn double size). The Signal Lab sends the shape name on every `/laserobject` step.
- Sounds: `~sounds` in `_main.scd`, one per signal (oscillator, overtone ratio, release, midi note); Craft pad n = n-th
  note of A minor pentatonic. Every move restarts the sound, the value bends its pitch up to an octave. `~volume` per sound, limiter on the sum.
  Only signals in `~audible` sound, so unconnected (floating) inputs stay quiet unless wired to a knob.
- Tuning knobs: Trill consts at the top of `trill-oled.cpp` (`kPrescaler`, `kNoiseThreshold`, `kCraftDefaults`, `kMinChange`),
  analog/piezo ones at the top of `_main.scd` (`~minChange`, `~potMax`, `~potSmooth`, `~piezoGain`, `~piezoFloor`, `~piezoRelease`,
  `~heartFloor`, `~heartRelease`, `~bpmRange`).
  Keep them; sensors need tuning on the real hardware.
- `trill-oled` builds with `-ffast-math`, so no NaN sentinels. Bela SuperCollider is 3.12 (Bela fork).
- Receiver config: `osc-receiver/config_laser1.txt` (driver `libHeliosLaserDAC.dylib`, OSC `0.0.0.0:2345`).

## Receiver notes

- `main.py` starts the laser output + OSC threads; pygame preview runs on the main thread; shared state in `global_data.py`.
- `/laserobject N` indexes `NOTE_LASEROBJECT_MAPPING` in `osc_input.setup()`: 0 Blank, 1 Parabola, 2 Cubic, 3 Conic, 4 Hyperelliptic,
  5 Cubic 2 (`Cubic2`, own `cubic2_*` knobs). Objects are deep-copied from those prototypes.
- `/laserobject 6+` are `osc-receiver/svg/*.svg` sorted by name (`SvgObject`, needs `svgelements`). Add a matching
  `"N: SVG name": N` entry to the template's `laserobject` dropdown. Text must be paths; single-stroke (Hershey) fonts trace cleanest.
- Two perspective systems exist on purpose: `/effect/perspective/*` projects points, `/parameters/homography_*`
  transforms curve coefficients (keeps samples even on screen) and SVG points (sampled once, projected by `inv(M)`). Both are in the template.
- Upstream gotcha: `LaserObject.effects` is a class-level list shared by every object.
- When refactoring drawing code, snapshot `point_list` output before/after and diff it.
  `sort_path` tie-breaks can reorder points without changing the drawing.
