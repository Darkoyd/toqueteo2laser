// laser-osc-controller: SuperCollider owns the Bela audio core. It reads the pots, joystick, piezos, pulse sensor and buttons,
// gets the Trill signals from trill-oled (C++, I2C), sends every /signal/<name> (0..1) to the Signal Lab on the Mac
// and plays a sound for each signal whenever it moves (Bela audio out), if the Signal Lab says it may (see ~audible).
// Start it with run.sh (make run does), which also builds and starts trill-oled.

// Tuning knobs, sensors need tuning on the real hardware
~signalPort = 2346; // trill-oled sends /signal/flex and /signal/craft/<n> here
// The Signal Lab on the Mac says /hello here every second (over USB or the Bela Wi-Fi hotspot);
// signals go to whoever said it last, nowhere once it has been quiet this long
~helloTimeout = 3;
// /hello's args: the signals that may sound (wired to a knob in the Signal Lab and not muted there).
// Kept after the Signal Lab goes quiet; none until it first says hello.
~audible = [];
~minChange = 0.005; // ponytail: fixed dead band, tune if the laser jitters or feels steppy (kMinChange in trill-oled.cpp)
~pollHz = 80;       // input reads per second, same as trill-oled
// Pots and joystick on Analog In 0..3, wired between 3.3V and GND. Bela's ADC spans 0..4.096V,
// so 3.3V reads ~0.806; tune ~potMax to what a pot turned fully up prints.
~potMax = 0.806;
~potSmooth = 0.045; // seconds, one-pole time constant against ADC jitter, higher = smoother/slower
// Piezos on Audio In L/R: level envelope (instant attack, exponential release) 0..1
~piezoGain = 4;         // raise if hard hits don't reach ~1, lower if light taps max out
~piezoFloor = 0.02;     // envelope below this (after gain) = 0, so idle hiss isn't sent
~piezoRelease = 0.9998; // per audio sample, ~110 ms decay at 44.1 kHz; lower = snappier
~buttonPins = [0, 1, 2]; // Bela digital pins, 10k pull-up to 3.3V, pressed = GND
// PulseSensor (pulsesensor.com) on 3.3V, signal on Analog In 4. heart/beat jumps to 1 on every beat and falls back
// to 0; heart/bpm is the median of the last 5 beat intervals, ~bpmRange mapped to 0..1, 0 after 3 s without a beat.
// Every beat prints "[heart] <bpm> bpm, pulse <height>": set ~heartFloor between the height with a finger on
// and the heights of the false beats printed with no finger.
~heartFloor = 0.01;    // pulse height (Bela ADC units, 1 = 4.096V) below this = no finger, no beats
~heartRelease = 0.25;  // seconds heart/beat takes to fall from 1 to 0
~bpmRange = [40, 160]; // heart/bpm 0..1
// analog in 0..3, audio in L/R (piezos), pulse sensor, digital pins (buttons), sent as /signal/<name> in this order
~names = ["pot/0", "pot/1", "joy/x", "joy/y", "piezo/0", "piezo/1", "heart/beat", "joy/button", "button", "horn"];

// Sounds: one per signal, restarted by every move and fading out `release` seconds after the last one.
// The signal's value (0..1) bends the pitch up to an octave. [oscillator, overtone ratio, release, midi note]
~volume = 0.15; // per sound; a limiter keeps the sum from clipping
~sounds = Dictionary[
	"pot/0" ->      [\saw,   2,    0.5, 45],
	"pot/1" ->      [\saw,   2,    0.5, 52],
	"joy/x" ->      [\pulse, 1.5,  0.3, 57],
	"joy/y" ->      [\pulse, 1.5,  0.3, 60],
	"piezo/0" ->    [\sine,  2.76, 0.6, 64], // inharmonic overtone: bell
	"piezo/1" ->    [\sine,  2.76, 0.6, 67],
	"joy/button" -> [\tri,   3,    0.2, 76],
	"button" ->     [\tri,   3,    0.2, 81],
	"flex" ->       [\tri,   2,    0.4, 48],
	"heart/beat" -> [\sine,  2,    0.15, 40], // kick: the falling beat drops the pitch an octave
];
// The horn button (digital 2) plays this sample on every press instead of a tone, whether wired in the Signal Lab or not.
~hornFile = "sounds/airhorn.wav".resolveRelative; // wav/aiff only, scsynth can't read mp3
~hornVolume = 0.5;
~silent = Set["heart/bpm"]; // sent without a sound, it changes on every beat (heart/beat sounds those)
~soundFor = { |name|
	~sounds[name] ?? {
		// Craft pad n: n-th note of A minor pentatonic, sine for pads 0..14, triangle for 15..29.
		// Any other signal: the same from its name's hash, so it sounds the same every run.
		var n = if(name.beginsWith("craft/")) { name.drop(6).asInteger } { name.hash.abs };
		[[\sine, \tri][n div: 15 % 2], 1.5, 0.3, 57 + (n % 15).degreeToKey([0, 3, 5, 7, 10])]
	}
};

s = Server.default;
s.options.numAnalogInChannels = 8; // 8 channels at 22.05 kHz, as the C++ project ran (-C 8)
s.options.numAnalogOutChannels = 8;
s.options.numDigitalChannels = 16;
s.options.blockSize = 16;
s.options.numInputBusChannels = 2;
s.options.numOutputBusChannels = 2;
s.options.pgaGainLeft = 10; // audio input gain in dB, the piezo knobs above were tuned with it
s.options.pgaGainRight = 10;
s.options.headphoneLevel = -6; // dB

s.waitForBoot {
	[\sine, \tri, \pulse, \saw].do { |wave|
		SynthDef(wave, { |freq = 440, ratio = 2, val = 0, release = 0.4, amp = 0.1, t_move = 1|
			var f = Lag.kr(freq * (2 ** val), 0.03);
			var env = EnvGen.kr(Env.perc(0.005, release), t_move, doneAction: Done.freeSelf);
			var osc = (sine: { SinOsc.ar(f) }, tri: { LFTri.ar(f) }, pulse: { Pulse.ar(f, 0.3) }, saw: { Saw.ar(f) })[wave].value;
			var sig = LPF.ar(osc + SinOsc.ar(f * ratio, 0, 0.3), 3000);
			Out.ar(0, (sig * env * amp).dup);
		}).add;
	};
	SynthDef(\sample, { |buf, amp = 0.5|
		Out.ar(0, PlayBuf.ar(2, buf, BufRateScale.kr(buf), doneAction: Done.freeSelf) * amp);
	}).add;
	~horn = Buffer.read(s, ~hornFile);
	SynthDef(\limiter, { ReplaceOut.ar(0, Limiter.ar(In.ar(0, 2), 0.8)) }).add;
	SynthDef(\inputs, { |potMax, potSmooth, piezoGain, piezoFloor, piezoRelease, heartFloor, heartRelease, pollHz|
		var pots = 4.collect { |n| OnePole.ar(AnalogIn.ar(n), exp(-1 / (potSmooth * SampleRate.ir))) / potMax };
		var piezos = 2.collect { |n| (PeakFollower.ar(SoundIn.ar(n), piezoRelease) * piezoGain).min(1) };
		var buttons = ~buttonPins.collect { |pin| 1 - DigitalIn.kr(pin) };
		// Pulse sensor: band-passed to 0.5..8 Hz (drops the baseline and drift). A beat is the systolic upstroke,
		// a rise steeper than half the steepest recent one; the 0.3 s hold (max 200 bpm) skips the dicrotic bump.
		var pulse = A2K.kr(LPF.ar(HPF.ar(AnalogIn.ar(4), 0.5), 8));
		var height = Amplitude.kr(pulse, 0.01, 10);
		var rise = Slope.kr(pulse).max(0);
		var gate = Trig1.kr((rise > (Amplitude.kr(rise, 0.01, 10) * 0.5)) * (height > heartFloor), 0.3);
		var beat = EnvGen.kr(Env.linen(0, 0.05, heartRelease), gate); // holds 1 for 50 ms, so the poll sees it
		piezos = piezos.collect { |p| p * (p > piezoFloor) };
		SendReply.kr(Impulse.kr(pollHz), '/inputs', A2K.kr(pots.min(1) ++ piezos) ++ [beat] ++ buttons);
		SendReply.kr(gate, '/heartbeat', height);
	}).add;
	s.sync;

	~last = Dictionary.new;    // name -> last value sent
	~playing = Dictionary.new; // name -> [Synth, Main.elapsedTime when its envelope ends and it frees itself]
	~quiet = true; // no sounds while the pot smoothing settles after start
	SystemClock.sched(0.5, { ~quiet = false; nil });
	~signal = { |name, v|
		// try: in the seconds before the timeout a gone Mac can be unreachable and sendMsg throws
		if(~remote.notNil and: { Main.elapsedTime - ~helloAt < ~helloTimeout }) {
			try { ~remote.sendMsg("/signal/" ++ name, v) }
		};
		if(name == "horn") { if(~quiet.not and: { v == 1 }) { Synth(\sample, [buf: ~horn, amp: ~hornVolume]) } } {
			if(~quiet.not and: { ~audible.includesEqual(name) and: { ~silent.includes(name).not } }) { ~sound.(name, v) };
		};
	};
	~sound = { |name, v|
		var p = ~soundFor.(name), now = Main.elapsedTime, cur = ~playing[name];
		// Restart the ringing synth only while its end is 50 ms+ away, else start a new one. Not onFree: a lost
		// /n_end kept dead synths here, and every later move failed with "/n_set Node not found" and stayed silent.
		if(cur.notNil and: { now < (cur[1] - 0.05) }) { cur[0].set(\val, v, \t_move, 1) } {
			cur = [Synth(p[0], [freq: p[3].midicps, ratio: p[1], release: p[2], val: v, amp: ~volume]), 0];
			~playing[name] = cur;
		};
		cur[1] = now + 0.005 + p[2]; // Env.perc attack + release, counted from this move
	};

	OSCdef(\inputs, { |msg|
		msg[3..].do { |v, n|
			var name = ~names[n], last = ~last[name];
			// always send the return to 0, so a decaying piezo doesn't stick just above it
			if(last.isNil or: { (v - last).abs > ~minChange or: { v == 0 and: { last != 0 } } }) {
				~last[name] = v;
				~signal.(name, v);
				// piezos and heart/beat would flood the console
				if(name.beginsWith("piezo").not and: { name != "heart/beat" }) { "[in] % = %\n".postf(name, v.round(0.001)) };
			};
		};
	}, '/inputs');
	~beats = List.new; // last beat intervals, seconds
	OSCdef(\heartbeat, { |msg|
		var now = Main.elapsedTime, bpm;
		// a gap over 2 s (under 30 bpm) means the finger was off, it's not an interval
		if(~lastBeat.notNil and: { now - ~lastBeat < 2 }) {
			~beats.add(now - ~lastBeat);
			if(~beats.size > 5) { ~beats.removeAt(0) };
			bpm = 60 / ~beats.copy.sort[~beats.size div: 2]; // median: one missed or extra beat doesn't move it
			~signal.("heart/bpm", bpm.linlin(~bpmRange[0], ~bpmRange[1], 0, 1));
			"[heart] % bpm, pulse %\n".postf(bpm.round, msg[3].round(0.001));
		} { "[heart] beat, pulse %\n".postf(msg[3].round(0.001)) };
		~lastBeat = now;
	}, '/heartbeat');
	SystemClock.sched(1, {
		if(~lastBeat.notNil and: { Main.elapsedTime - ~lastBeat > 3 }) {
			~lastBeat = nil;
			~beats.clear;
			~signal.("heart/bpm", 0);
		};
		1 // check again in 1 s
	});
	if(thisProcess.openUDPPort(~signalPort).not) { "[sc] can't listen on udp :%, no Trill signals".format(~signalPort).error };
	thisProcess.addOSCRecvFunc({ |msg, time, addr, port|
		var path = msg[0].asString;
		if(port == ~signalPort) {
			if(path.beginsWith("/signal/")) { ~signal.(path.drop(8), msg[1]) };
			if(path == "/hello") {
				var names = msg[1..].collect(_.asString); // sorted by the Signal Lab
				if(~remote.isNil or: { ~remote.ip != addr.ip }) { "[sc] signals -> %:%".format(addr.ip, ~signalPort).postln };
				if(names != ~audible) { "[sc] sounds: %".format(if(names.isEmpty) { "none" } { names.join(" ") }).postln };
				~remote = NetAddr(addr.ip, ~signalPort);
				~helloAt = Main.elapsedTime;
				~audible = names;
			};
		};
	});

	Synth.tail(s, \limiter);
	Synth(\inputs, [potMax: ~potMax, potSmooth: ~potSmooth, piezoGain: ~piezoGain, piezoFloor: ~piezoFloor,
		piezoRelease: ~piezoRelease, heartFloor: ~heartFloor, heartRelease: ~heartRelease, pollHz: ~pollHz]);
	"[sc] ready, waiting for the Signal Lab's /hello on udp :%".format(~signalPort).postln;
};
