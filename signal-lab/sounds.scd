// Signal sounds on the Mac (they used to play on the Bela, whose CPU couldn't spare it).
// The Signal Lab forwards every /signal/<name> (0..1) here and says /hello with the signals that may sound
// (wired to a knob and not muted). Each plays a sound whenever it moves, on the Mac's default audio out.
// start.sh runs it: /Applications/SuperCollider.app/Contents/MacOS/sclang -D signal-lab/sounds.scd

~port = 2348; // SOUND in signal_lab.py
~audible = []; // from /hello; none until the Signal Lab first says it

// One sound per signal, restarted by every move and fading out `release` seconds after the last one.
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
// The horn button plays this sample on every press instead of a tone, whether wired in the Signal Lab or not.
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
s.options.numInputBusChannels = 0; // no mic permission prompt
s.options.numOutputBusChannels = 2;

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
	s.sync;

	~playing = Dictionary.new; // name -> [Synth, Main.elapsedTime when its envelope ends and it frees itself]
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

	if(thisProcess.openUDPPort(~port).not) { "[sound] can't listen on udp :%".format(~port).error };
	thisProcess.addOSCRecvFunc({ |msg, time, addr, port|
		var path = msg[0].asString;
		if(port == ~port) {
			if(path.beginsWith("/signal/")) {
				var name = path.drop(8), v = msg[1];
				if(name == "horn") { if(v == 1) { Synth(\sample, [buf: ~horn, amp: ~hornVolume]) } } {
					if(~audible.includesEqual(name) and: { ~silent.includes(name).not }) { ~sound.(name, v) };
				};
			};
			if(path == "/hello") {
				var names = msg[1..].collect(_.asString); // sorted by the Signal Lab
				if(names != ~audible) { "[sound] sounds: %".format(if(names.isEmpty) { "none" } { names.join(" ") }).postln };
				~audible = names;
			};
		};
	});

	Synth.tail(s, \limiter);
	"[sound] ready on udp :%".format(~port).postln;
};
