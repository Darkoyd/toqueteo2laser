# toqueteo2laser

Touch, turn, tap or breathe on a box of sensors and a laser draws and bends curves on the wall, while the Mac plays a
sound for every sensor you move. Trill sensors, pots, a joystick, piezos and a pulse sensor on a
[Bela](https://bela.io) drive a Helios laser through the Signal Lab and [osc2laser](#credits).

```
sensors -> Bela (laser-osc-controller) --OSC, USB or Wi-Fi--> Signal Lab (Mac) --OSC--> osc2laser --USB--> Helios DAC -> laser
                                                                     \--> sounds on the Mac (SuperCollider)
                                                                                  ^
                                                       Open Stage Control (UI) --/
```

| Folder | What it is |
|---|---|
| `laser-osc-controller/` | Bela project, runs at boot. SuperCollider (`_main.scd`) reads the sensors; `trill-oled.cpp` reads the Trills and drives the OLED. Sends every reading as `/signal/<name>` 0..1. |
| `signal-lab/` | The Signal Lab: web page on http://127.0.0.1:8000 that wires each signal to a laser knob. |
| `osc2laser/` | Fork of [osc2laser](https://github.com/oliverbyte/osc2laser): turns OSC into laser points for the Helios DAC, with a 2D preview window. |
| `start.sh` | Starts osc2laser and the Signal Lab together. |

## Hardware

- Bela (with its USB cable to the Mac), Helios laser DAC + laser
- Trill Flex and Trill Craft, SSD1306 128x64 OLED (optional): all on the Bela's I2C connectors
- 2 pots, KY-023 joystick, 2 piezos, PulseSensor, 3 push buttons
- SuperCollider on the Mac (`/Applications/SuperCollider.app`) for the sounds

| Sensor | Bela pin | Signal |
|---|---|---|
| Pots | Analog In 0, 1 | `pot/0`, `pot/1` |
| Joystick VRx, VRy (on 3.3V) | Analog In 2, 3 | `joy/x`, `joy/y` |
| PulseSensor (on 3.3V) | Analog In 4 | `heart/beat` (1 on each beat), `heart/bpm` (40..160 → 0..1) |
| Piezos (1 MΩ across each) | Audio In L, R | `piezo/0`, `piezo/1` |
| Joystick SW, button, horn button (10k pull-up to 3.3V, pressed = GND) | Digital 0, 1, 2 | `joy/button`, `button`, `horn` |
| Trill Flex | I2C `0x48` | `flex` |
| Trill Craft (30 pads) | I2C `0x30` | `craft/0` … `craft/29` |
| OLED | I2C `0x3C` | shows the current shape name |

The horn button plays `signal-lab/sounds/airhorn.wav` on every press. Check the I2C wiring with `ssh root@bela.local i2cdetect -y -r 1`.

## Setup

**Mac.** One Python venv, `osc2laser/osc-receiver/.venv`, runs both osc2laser and the Signal Lab. It is not checked in.
Create it with [uv](https://docs.astral.sh/uv/) (`brew install uv`):

```sh
cd osc2laser/osc-receiver
uv venv -p 3.11 .venv
uv pip install -p .venv -r requirements.txt
```

Without uv: `python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt`.

**Bela.** Plug it in over USB, then copy the project over and set it to run at boot:

```sh
rsync -av laser-osc-controller/ root@bela.local:Bela/projects/laser-osc-controller/
ssh root@bela.local 'make -C ~/Bela PROJECT=laser-osc-controller startup && systemctl restart bela_startup'
```

The first start compiles `trill-oled.cpp` on the board. Watch it with `ssh root@bela.local journalctl -fu bela_startup`.
After later edits, rsync again and run `systemctl restart bela_startup`, or use the VS Code task "Bela: build & run".

## Run

```sh
./start.sh        # osc2laser + Signal Lab, Ctrl-C stops both (-v logs every signal)
```

1. Open http://127.0.0.1:8000. Each sensor shows up as a chip the first time it sends.
2. Pick a tab, then pick a signal in a knob's **Signal** column. The knob now follows the sensor.
3. Move the sensor to its lowest point and press **set** under input min, then its highest point and **set** under input max.
4. Optional: **Scale** shrinks the knob's range; **LFO rate** wobbles the knob around the signal's value, its speed set by a second signal.

Wiring is saved in `signal-lab/mapping.json` and survives restarts. A signal only makes its sound when it is
wired to a knob; the button on its chip mutes it. Toggles and dropdowns (like the shape picker) step once each time
their signal rises past the middle of its input range.

[Open Stage Control](https://openstagecontrol.ammd.net/) with
`osc2laser/osc-senders/open-stage-control/pavillion-template.json` gives you every knob by hand, sending to `127.0.0.1:2345`.
The Signal Lab reads its knob list and ranges from the same file.

Tests: `cd osc2laser/osc-receiver && .venv/bin/pytest -q` and `osc2laser/osc-receiver/.venv/bin/python signal-lab/signal_lab.py --selftest`.

## Shapes

The `/laserobject` dropdown picks what the laser draws:

| # | Shape |
|---|---|
| 0 | Blank (laser off) |
| 1–5 | Curves: Parabola, Cubic, Conic, Hyperelliptic, Cubic 2. Their coefficients are knobs under `/parameters/*`. |
| 6+ | SVG drawings from `osc2laser/osc-receiver/svg/`, sorted by file name |

To add an SVG: drop it in `svg/`, then add a `"N: Name": N` entry to the `laserobject` dropdown in `pavillion-template.json`.
Text must be converted to paths; single-stroke (Hershey) fonts trace cleanest.

Two perspective controls bend the picture to fit a tilted wall: `/effect/perspective/*` moves the drawn points,
`/parameters/homography_*` transforms the curve itself so its points stay evenly spaced.

## Connecting to the Bela

- **USB:** Bela `192.168.7.2`, Mac `192.168.7.1`. `ssh root@bela.local`.
- **Wi-Fi:** the Bela runs a hotspot called `bela-laser` (password in `/etc/hostapd/hostapd.conf` on the Bela).
  Join it from the Mac: Bela `192.168.8.2`, Mac `192.168.8.1`. `ssh root@192.168.8.2`.

The Signal Lab finds the Bela on either link by itself. The Bela sends signals only to a Mac whose Signal Lab is running.

## Tuning

Sensors need tuning on the real hardware. The knobs live at the top of the code:

- `laser-osc-controller/_main.scd`: pot range and smoothing, piezo gain and floor, pulse threshold.
- `signal-lab/sounds.scd`: `~sounds` (one tone per signal), `~volume`, `~hornVolume`.
- `laser-osc-controller/trill-oled.cpp`: Trill prescaler, noise threshold, `kCraftDefaults`.
- Trill Craft settings can be tuned live in the Signal Lab's "Trill Craft settings" panel. They reset when the Bela
  restarts: copy the `[craft] prescaler …` line from the Bela log into `kCraftDefaults`.
- `osc2laser/osc-receiver/config_laser1.txt`: laser DAC driver and OSC port.

## Troubleshooting

- **No chips in the Signal Lab:** check the Bela is running (`journalctl -fu bela_startup`) and the Mac is on its USB or Wi-Fi network.
- **A sensor makes no sound:** wire it to a knob and check its chip is not muted.
- **Laser shows nothing:** check the Helios is plugged in and `/laserobject` is not 0 (Blank). The osc2laser preview window shows what it sends.
- More detail on wiring, ports and internals: `AGENTS.md`.

## Credits

**osc2laser** by [oliverbyte](https://github.com/oliverbyte/osc2laser), sponsored by
[goodtimes](https://www.goodtimes.technology) and [sync.blue](https://www.sync.blue), is the laser engine of this project.
`osc2laser/` is a fork of upstream commit `76c637a` (tagged `osc2laser-upstream` here). We added the curve objects,
SVG drawings, perspective/homography controls, the Open Stage Control template and some output fixes; see them with
`git diff osc2laser-upstream -- osc2laser`. osc2laser is licensed under the
[GNU AGPL-3.0](osc2laser/LICENSE), and our changes to it are under the same license.

Also built on:

- [Bela](https://bela.io) and [Trill](https://bela.io/products/trill/) sensors, by Augmented Instruments
- [Helios laser DAC](https://bitlasers.com/helios-laser-dac/) and its [driver](https://github.com/Grix/helios_dac)
- [SuperCollider](https://supercollider.github.io), [Open Stage Control](https://openstagecontrol.ammd.net/),
  [python-osc](https://github.com/attwad/python-osc), [svgelements](https://github.com/meerk40t/svgelements), [pygame](https://www.pygame.org)
