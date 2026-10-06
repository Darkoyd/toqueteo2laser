# The Signal Lab: routes Bela /signal/<name> (0..1) to osc2laser knobs, rewired live from a web page.
# Bela -> udp :2346 -> here -> udp 127.0.0.1:2345 (osc2laser). UI on http://127.0.0.1:8000
# Every signal also goes to sounds.scd (sclang on udp 127.0.0.1:2348), which plays the audible ones.
import json
import math
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import ThreadingOSCUDPServer
from pythonosc.udp_client import SimpleUDPClient

HERE = Path(__file__).parent
TEMPLATE = HERE.parent / "osc2laser/osc-senders/open-stage-control/pavillion-template.json"
MAPPING = HERE / "mapping.json"
MUTED = HERE / "muted.json"  # signals whose sound is muted
SIGNAL_PORT, LASER, HTTP_PORT = 2346, ("127.0.0.1", 2345), 8000
SOUND = ("127.0.0.1", 2348)  # sounds.scd
BELA_DISPLAY = ("192.168.7.2", 2347)  # Bela's OLED (shape name on every step) and live Trill Craft settings;
# the ip follows wherever the signals come from (USB 192.168.7.2 or the Bela hotspot 192.168.8.2)
# the Bela's sclang sends signals to who says /hello; its args are the signals that may sound (see audible()),
# which only sounds.scd uses
HELLO = [("192.168.7.2", SIGNAL_PORT), ("192.168.8.2", SIGNAL_PORT), SOUND]
# /craft/<name> settings, trill-oled.cpp clamps each to its kCraftMin..kCraftMax; calibrate takes no value
CRAFT = {"prescaler", "noise", "bits", "speed", "threshold", "full", "calibrate"}
SKIP = {"/laserobject": {0}}  # dropdown values a press never steps to (0 = Blank)
VERBOSE = "-v" in sys.argv or "--verbose" in sys.argv
LFO_TICK = 1 / 50  # ponytail: fixed 50 Hz send rate for LFO knobs, raise if fast LFOs look steppy


def group_and_name(addr):
    parts = addr.strip("/").split("/")
    if len(parts) == 3:
        return parts[1], parts[2]  # /effect/perspective/pitch
    if parts[0] == "parameters" and "_" in parts[1]:
        return tuple(parts[1].split("_", 1))  # /parameters/cubic_a00
    return parts[0], parts[-1]  # /effect/scale_factor


def load_knobs(path):
    knobs = {}

    def walk(w):
        if isinstance(w, list):
            for c in w:
                walk(c)
        elif isinstance(w, dict):
            r, t, addr, vals = w.get("range"), w.get("type"), w.get("address", "auto"), w.get("values")
            k = None
            if t in ("knob", "fader") and isinstance(r, dict):
                k = {"kind": "knob", "min": float(r["min"]), "max": float(r["max"])}
            elif t == "toggle":  # a press flips it
                k = {"kind": "toggle", "min": 0.0, "max": 1.0}
            elif t == "dropdown" and isinstance(vals, dict):  # a press steps to the next value
                opts = sorted(([lbl, v] for lbl, v in vals.items() if v not in SKIP.get(addr, ())), key=lambda o: o[1])
                k = {"kind": "next", "min": float(opts[0][1]), "max": float(opts[-1][1]), "options": opts}
            if k and isinstance(addr, str) and addr.startswith("/"):
                group, name = group_and_name(addr)
                # display name from the template: the knob's caption (html), else a fixed label (not "%value")
                lbl = w.get("label")
                title = w.get("html") if isinstance(w.get("html"), str) else lbl if lbl and "%" not in lbl else ""
                knobs.setdefault(addr, {"addr": addr, "group": group, "name": name, "title": title, **k})
            for v in w.values():
                walk(v)

    walk(json.loads(Path(path).read_text()))
    # drop /parameters/<object>_* knobs of objects the /laserobject dropdown can't show ("5: Cubic 2" -> cubic2)
    shown = {lbl.split(": ", 1)[-1].replace(" ", "").lower() for lbl, _ in knobs["/laserobject"]["options"]}
    return {a: k for a, k in knobs.items() if not a.startswith("/parameters/") or k["group"] in shown | {"homography"}}


def scale(v, in_min, in_max, lo, hi, offset=0.0):
    # offset shifts the 0..1 position (the LFO swing) after the input clamp
    t = 0.0 if in_max == in_min else min(1.0, max(0.0, (v - in_min) / (in_max - in_min)))
    return lo + min(1.0, max(0.0, t + offset)) * (hi - lo)


def out_range(knob, factor):
    # shrink the knob's range by factor (0..1) toward 0 if the range crosses it, else toward its min
    lo, hi = knob["min"], knob["max"]
    a = 0.0 if lo <= 0 <= hi else lo
    return a + (lo - a) * factor, a + (hi - a) * factor


lock = threading.Lock()
signals = {}  # name -> last raw value
outputs = {}  # knob addr -> last value sent
pressed = {}  # toggle/next addr -> is its signal held down
mappings = json.loads(MAPPING.read_text()) if MAPPING.exists() else {}
muted = set(json.loads(MUTED.read_text())) if MUTED.exists() else set()
hello_now = threading.Event()  # set when audible() changes, so sounds.scd hears it before the next 1 s hello
knobs = {}
laser = SimpleUDPClient(*LASER)
bela = SimpleUDPClient(*BELA_DISPLAY)
sound = SimpleUDPClient(*SOUND)


WAVES = {  # phase 0..2pi -> -1..1, all rising through 0 at phase 0
    "sine": math.sin,
    "triangle": lambda ph: 2 / math.pi * math.asin(math.sin(ph)),
    "square": lambda ph: 1.0 if ph < math.pi else -1.0,
}


def send_knob(addr, m, offset=0.0):  # caller holds lock
    out = scale(signals[m["signal"]], m["in_min"], m["in_max"], *out_range(knobs[addr], m.get("scale", 1.0)), offset)
    outputs[addr] = out
    laser.send_message(addr, out)
    return out


def press(addr, m, v):  # caller holds lock
    """Toggle/next targets fire once when the signal rises past the middle of in_min..in_max.
    Returns the new value, or None when nothing fired."""
    down = scale(v, m["in_min"], m["in_max"], 0, 1) > 0.5
    was, pressed[addr] = pressed.get(addr, False), down
    if not down or was:
        return None
    k = knobs[addr]
    if k["kind"] == "toggle":
        out = 0.0 if outputs.get(addr, 0.0) > 0.5 else 1.0
    else:
        vals = [o[1] for o in k["options"]]
        cur = outputs.get(addr)
        out = vals[(vals.index(cur) + 1) % len(vals) if cur in vals else 0]
    outputs[addr] = out
    return out


def fire(addr, m, v):  # caller holds lock
    out = press(addr, m, v)
    if out is None:
        return None
    laser.send_message(addr, out)
    if knobs[addr]["kind"] == "next":
        label = next(lbl for lbl, val in knobs[addr]["options"] if val == out)
        try:
            bela.send_message("/display", label.replace(": ", "\n"))  # "1: Parabola" -> number, name
        except OSError:  # Bela unplugged: no route to it, the laser still switched
            pass
    return out


def on_signal_from(client, address, *args):
    global bela
    if client[0] != bela._address:  # Bela switched link (USB <-> Wi-Fi): reply on the one it uses
        bela = SimpleUDPClient(client[0], BELA_DISPLAY[1])
    on_signal(address, *args)


def on_signal(address, *args):
    if not args or not isinstance(args[0], (int, float)):
        return
    name, v = address[len("/signal/"):], float(args[0])
    sound.send_message(address, v)
    with lock:
        if VERBOSE and name not in signals:
            print(f"new signal {name}")
        signals[name] = v
        sent = []
        for addr, m in mappings.items():
            if m["signal"] != name or addr not in knobs:
                continue
            if knobs[addr]["kind"] != "knob":
                out = fire(addr, m, v)
                if out is not None:
                    sent.append(f"{addr} {out:.4g}")
            elif not m.get("lfo"):  # LFO knobs are sent by lfo_loop
                sent.append(f"{addr} {send_knob(addr, m):.4g}")
        if VERBOSE:
            # ponytail: LFO sends (50/s per knob) aren't logged, they'd drown everything else
            print(f"in {name} {v:.4f}" + (" -> " + ", ".join(sent) if sent else ""))


def audible():  # caller holds lock
    """Signals sounds.scd may play a sound for: wired to a knob (as its signal or LFO rate) and not muted."""
    wired = {s for addr, m in mappings.items() if addr in knobs for s in (m["signal"], m.get("lfo")) if s}
    return sorted(wired - muted)


def hello_loop():
    # every second, on both links: whichever reaches the Bela tells its sclang where to send the signals;
    # tells sounds.scd which of them may sound
    clients = [SimpleUDPClient(*a) for a in HELLO]
    while True:
        with lock:
            names = audible()
        for c in clients:
            try:
                c.send_message("/hello", names)
            except OSError:  # that link is down
                pass
        hello_now.wait(1)
        hello_now.clear()


def lfo_loop():
    # main signal = centre, depth = swing as a fraction of the range, lfo signal 0..1 -> 0..hz_max
    phases, last = {}, time.monotonic()
    while True:
        time.sleep(LFO_TICK)
        now = time.monotonic()
        dt, last = now - last, now
        with lock:
            for addr, m in mappings.items():
                if not m.get("lfo") or knobs.get(addr, {}).get("kind") != "knob" or m["signal"] not in signals:
                    continue
                hz = min(1.0, max(0.0, signals.get(m["lfo"], 0.0))) * m.get("hz_max", 5.0)
                phases[addr] = (phases.get(addr, 0.0) + 2 * math.pi * hz * dt) % (2 * math.pi)
                wave = WAVES.get(m.get("wave"), math.sin)
                send_knob(addr, m, m.get("depth", 0.25) * wave(phases[addr]))


class Http(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/":
            return self.reply(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
        if self.path == "/state":
            with lock:
                return self.reply(200, {"signals": signals, "knobs": list(knobs.values()), "mappings": mappings,
                                        "outputs": outputs, "muted": sorted(muted), "audible": audible()})
        self.reply(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/craft":
            return self.post_craft()
        if self.path == "/mute":
            return self.post_mute()
        if self.path == "/reset":
            with lock:
                mappings.clear()
                outputs.clear()
                MAPPING.write_text("{}\n")
            hello_now.set()
            return self.reply(200, {"ok": True})
        if self.path != "/map":
            return self.reply(404, {"error": "not found"})
        try:
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            knob, sig = req["knob"], req.get("signal")
            lo, hi = float(req.get("in_min", 0)), float(req.get("in_max", 1))
            factor = float(req.get("scale", 1))
            lfo, depth, hz_max = req.get("lfo"), float(req.get("depth", 0.25)), float(req.get("hz_max", 5))
            if knob not in knobs or not (math.isfinite(lo) and math.isfinite(hi)):
                raise ValueError("bad knob or range")
            if not 0 <= factor <= 1:
                raise ValueError("scale must be 0..1")
            wave = req.get("wave", "sine")
            if wave not in WAVES:
                raise ValueError("wave must be one of " + ", ".join(WAVES))
            if not 0 <= depth <= 1 or not 0 <= hz_max <= 100:
                raise ValueError("depth must be 0..1, max Hz 0..100")
            if not all(x is None or isinstance(x, str) for x in (sig, lfo)):
                raise ValueError("bad signal")
        except (ValueError, KeyError, TypeError) as e:
            return self.reply(400, {"error": str(e)})
        with lock:
            if sig:
                mappings[knob] = {"signal": sig, "in_min": lo, "in_max": hi, "scale": factor,
                                  "lfo": lfo or None, "depth": depth, "hz_max": hz_max, "wave": wave}
            else:
                mappings.pop(knob, None)
                outputs.pop(knob, None)
            if VERBOSE:
                print(f"map {knob} <- {mappings[knob] if sig else 'none'}")
            MAPPING.write_text(json.dumps(mappings, indent=2, sort_keys=True) + "\n")
        hello_now.set()
        self.reply(200, {"ok": True})

    def post_mute(self):
        try:
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            sig, mute = req["signal"], req["muted"]
            if not isinstance(sig, str) or not isinstance(mute, bool):
                raise ValueError("signal must be a name, muted true/false")
        except (ValueError, KeyError, TypeError) as e:
            return self.reply(400, {"error": str(e)})
        with lock:
            (muted.add if mute else muted.discard)(sig)
            MUTED.write_text(json.dumps(sorted(muted)) + "\n")
            if VERBOSE:
                print(f"{'mute' if mute else 'unmute'} {sig}")
            body = {"muted": sorted(muted), "audible": audible()}
        hello_now.set()
        self.reply(200, body)


    def post_craft(self):
        try:
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            name = req["setting"]
            if name not in CRAFT:
                raise ValueError("setting must be one of " + ", ".join(CRAFT))
            value = [] if name == "calibrate" else float(req["value"])
        except (ValueError, KeyError, TypeError) as e:
            return self.reply(400, {"error": str(e)})
        try:
            bela.send_message(f"/craft/{name}", value)  # float: trill-oled.cpp only pops floats
        except OSError:
            return self.reply(502, {"error": "Bela unreachable"})
        if VERBOSE:
            print(f"craft {name} {value}")
        self.reply(200, {"ok": True})


def selftest():
    assert scale(0.5, 0, 1, -math.pi, math.pi) == 0
    assert scale(0.25, 0, 1, -math.pi, math.pi) == -math.pi / 2
    assert scale(-1, 0, 1, 0, 255) == 0 and scale(2, 0, 1, 0, 255) == 255
    assert scale(0.5, 0.1, 0.9, 0, 10) == 5  # stretch
    assert scale(0.7, 0.5, 0.5, 3, 1000) == 3  # zero-width input
    assert abs(scale(0.2, 0.9, 0.1, 0, 10) - 8.75) < 1e-9  # min > max inverts
    assert scale(0.5, 0, 1, 0, 10, 0.25) == 7.5 and scale(0.9, 0, 1, 0, 10, 0.25) == 10  # LFO swing, clamped
    assert scale(2, 0, 1, 0, 10, -0.25) == 7.5  # input clamped before the swing
    tri, sq = WAVES["triangle"], WAVES["square"]
    assert abs(tri(math.pi / 2) - 1) < 1e-9 and abs(tri(math.pi / 4) - 0.5) < 1e-9 and abs(tri(3 * math.pi / 2) + 1) < 1e-9
    assert sq(0.1) == 1 and sq(math.pi + 0.1) == -1
    assert out_range({"min": -math.pi, "max": math.pi}, 0.5) == (-math.pi / 2, math.pi / 2)  # toward 0
    assert out_range({"min": 0, "max": 255}, 0.5) == (0, 127.5)
    assert out_range({"min": 5000, "max": 15000}, 0.5) == (5000, 10000)  # toward min
    assert out_range({"min": -5, "max": 5}, 1) == (-5, 5)
    assert group_and_name("/effect/perspective/pitch") == ("perspective", "pitch")
    assert group_and_name("/effect/color_change/r") == ("color_change", "r")
    assert group_and_name("/parameters/homography_tx") == ("homography", "tx")
    assert group_and_name("/effect/scale_factor") == ("effect", "scale_factor")
    assert group_and_name("/globals/scan_rate") == ("globals", "scan_rate")
    k = load_knobs(TEMPLATE)
    assert k["/effect/perspective/pitch"]["min"] < 0 and "/effect/xy_pos" not in k
    assert k["/parameters/homography_show_square"]["kind"] == "toggle"
    assert "/parameters/cubic2_a00" in k and "/parameters/wave_amplitude" not in k  # no Wave in the dropdown
    shapes = [o[1] for o in k["/laserobject"]["options"]]
    assert shapes[:4] == [1, 2, 3, 4], shapes  # Blank skipped, 6+ are svg/ files
    knobs.update(k)
    m = {"in_min": 0.0, "in_max": 1.0}
    sq, lo = "/parameters/homography_show_square", "/laserobject"
    assert press(sq, m, 1) == 1.0 and press(sq, m, 1) is None  # held: fires once
    assert press(sq, m, 0) is None and press(sq, m, 1) == 0.0  # release, press again: flips back
    assert press(lo, m, 1) == 1 and press(lo, m, 0) is None and press(lo, m, 1) == 2
    outputs[lo] = shapes[-1]
    pressed[lo] = False
    assert press(lo, m, 1) == 1  # wraps past the end, skipping Blank
    assert press(lo, {"in_min": 1.0, "in_max": 0.0}, 0) is None  # inverted range: 0 is "down" but was held
    mappings.clear()
    muted.clear()
    mappings.update({"/effect/perspective/pitch": {"signal": "pot/0", "lfo": "joy/x"}, lo: {"signal": "button"},
                     "/gone": {"signal": "pot/1"}})
    muted.add("joy/x")
    assert audible() == ["button", "pot/0"], audible()  # LFO rate counts; muted and unknown knobs don't
    print("selftest ok,", len(k), "knobs")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
        sys.exit()
    knobs.update(load_knobs(TEMPLATE))
    disp = Dispatcher()
    disp.map("/signal/*", on_signal_from, needs_reply_address=True)
    osc = ThreadingOSCUDPServer(("0.0.0.0", SIGNAL_PORT), disp)
    threading.Thread(target=osc.serve_forever, daemon=True).start()
    threading.Thread(target=lfo_loop, daemon=True).start()
    threading.Thread(target=hello_loop, daemon=True).start()
    print(f"{len(knobs)} knobs, signals on udp :{SIGNAL_PORT} -> {LASER[0]}:{LASER[1]}")
    print(f"UI http://127.0.0.1:{HTTP_PORT}")
    try:
        ThreadingHTTPServer(("127.0.0.1", HTTP_PORT), Http).serve_forever()
    except KeyboardInterrupt:
        pass
