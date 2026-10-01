# Laser Stuff

Trill sensors on a Bela drive a Helios laser through the Signal Lab and osc2laser.

```
Trill / pots / joystick / piezos -> Bela (laser-osc-controller) -> Signal Lab (Mac) -> osc2laser -> Helios DAC
```

- `laser-osc-controller/` Bela project (`render.cpp`), sends sensor readings as `/signal/*` OSC.
- `signal-lab/` maps signals onto laser knobs, web UI on http://127.0.0.1:8000.
- `osc2laser/` fork of [oliverbyte/osc2laser](https://github.com/oliverbyte/osc2laser); our changes: `git diff osc2laser-upstream -- osc2laser`.

## Setup

The Python venv is not checked in. One venv, `osc2laser/osc-receiver/.venv`, runs both the receiver and the Signal Lab.
Create it with [uv](https://docs.astral.sh/uv/) (`brew install uv`):

```sh
cd osc2laser/osc-receiver
uv venv -p 3.11 .venv
uv pip install -p .venv -r requirements.txt
```

Without uv: `python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt`.

## Run

```sh
./start.sh        # receiver + Signal Lab, Ctrl-C stops both (-v for verbose)
```

Tests: `cd osc2laser/osc-receiver && .venv/bin/pytest -q`

Bela: VS Code task "Bela: build & run". See `AGENTS.md` for wiring, tuning and details.
