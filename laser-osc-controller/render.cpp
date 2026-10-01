// Trill Flex + Craft, pots, joystick, piezos, buttons -> Signal Lab (signal-lab/ on the Mac), which maps each
// /signal/<name> (0..1) to an osc2laser knob. Wire a new sensor = send one more /signal/<name>.
// Follows Bela's Trill/flex-visual example: Flex runs in DIFF mode and touches
// are computed on the Bela with CentroidDetection; I2C is read in an auxiliary
// task (never in render()), and OSC is sent with sendNonRt() from that task.
#include <Bela.h>
#include <cmath>
#include <algorithm>
#include <libraries/Trill/Trill.h>
#include <libraries/Trill/CentroidDetection.h>
#include <libraries/OscSender/OscSender.h>
#include <libraries/OscReceiver/OscReceiver.h>
#include <I2c.h>
#include <string>

// Mac running the Signal Lab, as seen from the Bela over USB
const char* kRemoteIp = "192.168.7.1";
const int kRemotePort = 2346;
const int kDisplayPort = 2347; // Signal Lab sends /display "<text>" here
const unsigned int kPollUs = 12000; // ~80 Hz, same as Bela's Trill examples
// ponytail: fixed dead band, tune if the laser jitters or feels steppy
const float kMinChange = 0.005f;
// Flex tuning knobs, values recommended in the flex-visual example
const int kPrescaler = 4;           // higher = more sensitive / more resistive material
const float kNoiseThreshold = 0.03f;
// Craft tuning knobs. Each pad is scaled 0..1 between kPadThreshold and its own max, which starts at
// kPadFullTouch and grows to the strongest reading seen, so pads of different sizes all reach 1.
// Watch "[craft] peak" (printed once a second) while touching pads to pick these.
const int kCraftPrescaler = 1;          // Trill lib default for Craft; higher = more sensitive, for bigger pads or longer wires
const float kCraftNoiseThreshold = 0.f; // raw readings below this are zeroed by the Craft itself
const float kPadThreshold = 0.05f;      // DIFF reading above this = pad touched
const float kPadFullTouch = 0.3f;       // starting max: a firm touch at least this strong reads 1
// Everything read in render() ends up in gIn[] and is sent as /signal/<kNames[i]>, in this order:
// analog in 0..3, audio in L/R (piezos), digital 0..1 (buttons).
const char* kNames[] = {"pot/0", "pot/1", "joy/x", "joy/y", "piezo/0", "piezo/1", "joy/button", "button"};
const int kNumAnalog = 4, kNumPiezo = 2, kNumButtons = 2;
const int kNumIn = kNumAnalog + kNumPiezo + kNumButtons;
const int kButtonPins[kNumButtons] = {0, 1}; // Bela digital pins, 10k pull-up to 3.3V, pressed = GND
// Analog parts are wired between 3.3V and GND. Bela's ADC spans 0..4.096V,
// so 3.3V reads ~0.806; tune kPotMax to what a pot turned fully up prints.
const float kPotMax = 0.806f;
const float kPotSmooth = 0.001f; // one-pole coefficient per analog frame, lower = smoother/slower
// Piezos on audio in L/R: level envelope (instant attack, exponential release) 0..1
const float kPiezoGain = 4.f;       // raise if hard hits don't reach ~1, lower if light taps max out
const float kPiezoFloor = 0.02f;    // envelope below this (after gain) = 0, so idle hiss isn't sent
const float kPiezoRelease = 0.9998f; // per audio sample, ~110 ms decay at 44.1 kHz; lower = snappier
const int kOledAddress = 0x3C; // SSD1306 128x64, same I2C bus as the Trills

Trill gFlex;
Trill gCraft; // same I2C bus as the Flex, default address 0x30
CentroidDetection gCd;
OscSender gOsc;
OscReceiver gOscIn;
volatile float gIn[kNumIn]; // 0..1, written by render(), sent by readAndSend()
volatile bool gCalibrate = false; // set by /craft/calibrate, handled in readAndSend() (I2C stays in the aux task)

// Classic 5x7 font, ASCII 32..126, one byte per column, bit 0 = top
const uint8_t kFont[][5] = {
	{0x00,0x00,0x00,0x00,0x00},{0x00,0x00,0x5F,0x00,0x00},{0x00,0x07,0x00,0x07,0x00},{0x14,0x7F,0x14,0x7F,0x14},
	{0x24,0x2A,0x7F,0x2A,0x12},{0x23,0x13,0x08,0x64,0x62},{0x36,0x49,0x55,0x22,0x50},{0x00,0x05,0x03,0x00,0x00},
	{0x00,0x1C,0x22,0x41,0x00},{0x00,0x41,0x22,0x1C,0x00},{0x08,0x2A,0x1C,0x2A,0x08},{0x08,0x08,0x3E,0x08,0x08},
	{0x00,0x50,0x30,0x00,0x00},{0x08,0x08,0x08,0x08,0x08},{0x00,0x60,0x60,0x00,0x00},{0x20,0x10,0x08,0x04,0x02},
	{0x3E,0x51,0x49,0x45,0x3E},{0x00,0x42,0x7F,0x40,0x00},{0x42,0x61,0x51,0x49,0x46},{0x21,0x41,0x45,0x4B,0x31},
	{0x18,0x14,0x12,0x7F,0x10},{0x27,0x45,0x45,0x45,0x39},{0x3C,0x4A,0x49,0x49,0x30},{0x01,0x71,0x09,0x05,0x03},
	{0x36,0x49,0x49,0x49,0x36},{0x06,0x49,0x49,0x29,0x1E},{0x00,0x36,0x36,0x00,0x00},{0x00,0x56,0x36,0x00,0x00},
	{0x08,0x14,0x22,0x41,0x00},{0x14,0x14,0x14,0x14,0x14},{0x00,0x41,0x22,0x14,0x08},{0x02,0x01,0x51,0x09,0x06},
	{0x32,0x49,0x79,0x41,0x3E},{0x7E,0x11,0x11,0x11,0x7E},{0x7F,0x49,0x49,0x49,0x36},{0x3E,0x41,0x41,0x41,0x22},
	{0x7F,0x41,0x41,0x22,0x1C},{0x7F,0x49,0x49,0x49,0x41},{0x7F,0x09,0x09,0x09,0x01},{0x3E,0x41,0x49,0x49,0x7A},
	{0x7F,0x08,0x08,0x08,0x7F},{0x00,0x41,0x7F,0x41,0x00},{0x20,0x40,0x41,0x3F,0x01},{0x7F,0x08,0x14,0x22,0x41},
	{0x7F,0x40,0x40,0x40,0x40},{0x7F,0x02,0x0C,0x02,0x7F},{0x7F,0x04,0x08,0x10,0x7F},{0x3E,0x41,0x41,0x41,0x3E},
	{0x7F,0x09,0x09,0x09,0x06},{0x3E,0x41,0x51,0x21,0x5E},{0x7F,0x09,0x19,0x29,0x46},{0x46,0x49,0x49,0x49,0x31},
	{0x01,0x01,0x7F,0x01,0x01},{0x3F,0x40,0x40,0x40,0x3F},{0x1F,0x20,0x40,0x20,0x1F},{0x3F,0x40,0x38,0x40,0x3F},
	{0x63,0x14,0x08,0x14,0x63},{0x07,0x08,0x70,0x08,0x07},{0x61,0x51,0x49,0x45,0x43},{0x00,0x7F,0x41,0x41,0x00},
	{0x02,0x04,0x08,0x10,0x20},{0x00,0x41,0x41,0x7F,0x00},{0x04,0x02,0x01,0x02,0x04},{0x40,0x40,0x40,0x40,0x40},
	{0x00,0x01,0x02,0x04,0x00},{0x20,0x54,0x54,0x54,0x78},{0x7F,0x48,0x44,0x44,0x38},{0x38,0x44,0x44,0x44,0x20},
	{0x38,0x44,0x44,0x48,0x7F},{0x38,0x54,0x54,0x54,0x18},{0x08,0x7E,0x09,0x01,0x02},{0x0C,0x52,0x52,0x52,0x3E},
	{0x7F,0x08,0x04,0x04,0x78},{0x00,0x44,0x7D,0x40,0x00},{0x20,0x40,0x44,0x3D,0x00},{0x7F,0x10,0x28,0x44,0x00},
	{0x00,0x41,0x7F,0x40,0x00},{0x7C,0x04,0x18,0x04,0x78},{0x7C,0x08,0x04,0x04,0x78},{0x38,0x44,0x44,0x44,0x38},
	{0x7C,0x14,0x14,0x14,0x08},{0x08,0x14,0x14,0x18,0x7C},{0x7C,0x08,0x04,0x04,0x08},{0x48,0x54,0x54,0x54,0x20},
	{0x04,0x3F,0x44,0x40,0x20},{0x3C,0x40,0x40,0x20,0x7C},{0x1C,0x20,0x40,0x20,0x1C},{0x3C,0x40,0x30,0x40,0x3C},
	{0x44,0x28,0x10,0x28,0x44},{0x0C,0x50,0x50,0x50,0x3C},{0x44,0x64,0x54,0x4C,0x44},{0x00,0x08,0x36,0x41,0x00},
	{0x00,0x00,0x7F,0x00,0x00},{0x00,0x41,0x36,0x08,0x00},{0x08,0x04,0x08,0x10,0x08},
};

// Minimal SSD1306 text display: each '\n' starts a line; lines of <= 10 chars are drawn double size.
// ponytail: SSD1306 only, SH1106 needs a 2-column offset and page-by-page writes
struct Oled : I2c {
	bool ok = false;
	void cmd(std::initializer_list<uint8_t> c) {
		uint8_t buf[32] = {0x00}; // 0x00 = command stream
		size_t n = 1;
		for(uint8_t b : c) buf[n++] = b;
		writeBytes(buf, n);
	}
	bool setup(int bus, int address) {
		if(initI2C_RW(bus, address, -1))
			return false;
		cmd({0xAE, 0xD5, 0x80, 0xA8, 0x3F, 0xD3, 0x00, 0x40, 0x8D, 0x14, 0x20, 0x00, 0xA1, 0xC8,
		     0xDA, 0x12, 0x81, 0xCF, 0xD9, 0xF1, 0xDB, 0x40, 0xA4, 0xA6, 0xAF});
		return ok = true;
	}
	void show(const std::string& text) {
		uint8_t fb[8][128] = {}; // 8 pages of 8 pixel rows
		int page = 0;
		size_t start = 0;
		while(start <= text.size() && page < 8) {
			size_t end = std::min(text.find('\n', start), text.size());
			std::string line = text.substr(start, end - start);
			int s = line.size() <= 10 && page < 7 ? 2 : 1; // pixel scale
			for(size_t i = 0; i < line.size(); i++) {
				char ch = line[i] < 32 || line[i] > 126 ? '?' : line[i];
				for(int col = 0; col < 5; col++) {
					uint8_t bits = kFont[ch - 32][col];
					for(int row = 0; row < 7; row++) {
						if(!(bits >> row & 1))
							continue;
						for(int dx = 0; dx < s; dx++)
							for(int dy = 0; dy < s; dy++) {
								int x = (i * 6 + col) * s + dx, y = row * s + dy;
								if(x < 128)
									fb[page + y / 8][x] |= 1 << (y % 8);
							}
					}
				}
			}
			page += s;
			start = end + 1;
		}
		cmd({0x21, 0, 127, 0x22, 0, 7}); // write the whole screen
		for(auto& p : fb) {
			uint8_t buf[129] = {0x40}; // 0x40 = data stream
			std::copy(p, p + 128, buf + 1);
			writeBytes(buf, sizeof buf);
		}
	}
} gOled;

void onDisplay(oscpkt::Message* msg, const char*, void*)
{
	if(msg->match("/craft/calibrate").isOkNoMoreArgs()) {
		gCalibrate = true;
		return;
	}
	std::string text;
	if(msg->match("/display").popStr(text).isOkNoMoreArgs()) {
		printf("[display] %s\n", text.c_str());
		if(gOled.ok)
			gOled.show(text);
	}
}

void readAndSend(void*)
{
	// no NaN sentinel: Bela builds with -ffast-math, which assumes NaN never happens
	float last = 0.f;
	bool sent = false;
	unsigned int lastTouches = 0;
	uint32_t lastPads = 0; // bit n = Craft pad n touched (30 pads)
	float lastPad[32] = {}; // last value sent per Craft pad
	float padMax[32];        // strongest reading per Craft pad, see kPadFullTouch
	std::fill(padMax, padMax + 32, kPadFullTouch);
	while(!Bela_stopRequested()) {
		gFlex.readI2C();
		gCd.process(gFlex.rawData.data());
		unsigned int touches = gCd.getNumTouches();
		if(touches != lastTouches) {
			printf("[trill] touches: %u\n", touches);
			lastTouches = touches;
		}
		if(touches) {
			float loc = gCd.touchLocation(0); // 0..1 along the strip
			if(!sent || std::fabs(loc - last) > kMinChange) {
				gOsc.newMessage("/signal/flex").add(loc).sendNonRt();
				printf("[osc] -> %s:%d /signal/flex %.3f\n", kRemoteIp, kRemotePort, loc);
				last = loc;
				sent = true;
			}
		}
		if(gCalibrate) {
			gCalibrate = false;
			gCraft.updateBaseline(); // hands off the pads while this runs
			std::fill(padMax, padMax + 32, kPadFullTouch);
			printf("[craft] calibrated: new baseline, pad max reset to %.3f\n", kPadFullTouch);
		}
		gCraft.readI2C();
		uint32_t pads = 0;
		char addr[24];
		for(size_t n = 0; n < gCraft.rawData.size() && n < 32; n++) {
			// below threshold = 0, so idle pad noise isn't sent
			float raw = gCraft.rawData[n];
			padMax[n] = std::max(padMax[n], raw);
			float v = raw > kPadThreshold ? (raw - kPadThreshold) / (padMax[n] - kPadThreshold) : 0.f;
			if(v > 0.f)
				pads |= 1u << n;
			if(std::fabs(v - lastPad[n]) > kMinChange) {
				snprintf(addr, sizeof addr, "/signal/craft/%zu", n);
				gOsc.newMessage(addr).add(v).sendNonRt();
				lastPad[n] = v;
			}
		}
		if(pads != lastPads) {
			printf("[craft] pads:");
			for(size_t n = 0; n < gCraft.rawData.size(); n++)
				if(pads & (1u << n))
					printf(" %zu", n);
			printf("\n");
			lastPads = pads;
		}
		static float lastIn[kNumIn] = {-1.f, -1.f, -1.f, -1.f, -1.f, -1.f, -1.f, -1.f}; // -1 = send on first pass
		for(int n = 0; n < kNumIn; n++) {
			float v = gIn[n];
			// always send the return to 0, so a decaying piezo doesn't stick just above it
			if(std::fabs(v - lastIn[n]) > kMinChange || (v == 0.f && lastIn[n] != 0.f)) {
				snprintf(addr, sizeof addr, "/signal/%s", kNames[n]);
				gOsc.newMessage(addr).add(v).sendNonRt();
				if(n < kNumAnalog || n >= kNumAnalog + kNumPiezo) // piezos would flood the console
					printf("[in] %s = %.3f\n", kNames[n], v);
				lastIn[n] = v;
			}
		}
		static unsigned int tick = 0;
		if(++tick * kPollUs >= 1000000) { // once a second: strongest channel
			tick = 0;
			auto& raw = gFlex.rawData;
			size_t peak = std::max_element(raw.begin(), raw.end()) - raw.begin();
			printf("[trill] peak raw ch %zu = %.4f\n", peak, raw[peak]);
			auto& craw = gCraft.rawData;
			size_t cpeak = std::max_element(craw.begin(), craw.end()) - craw.begin();
			printf("[craft] peak raw pad %zu = %.4f (max %.4f)\n", cpeak, craw[cpeak], padMax[cpeak]);
		}
		usleep(kPollUs);
	}
}

bool setup(BelaContext* context, void* userData)
{
	if(gFlex.setup(1, Trill::FLEX) != 0) {
		fprintf(stderr, "Unable to initialise Trill Flex\n");
		return false;
	}
	gFlex.printDetails();
	gFlex.setMode(Trill::DIFF);
	gFlex.setPrescaler(kPrescaler);
	gFlex.setNoiseThreshold(kNoiseThreshold);
	gFlex.updateBaseline(); // don't touch the strip while the program starts
	gCd.setup(gFlex.getNumChannels(), 1, 3200);

	if(gCraft.setup(1, Trill::CRAFT) != 0) {
		fprintf(stderr, "Unable to initialise Trill Craft\n");
		return false;
	}
	gCraft.printDetails();
	gCraft.setMode(Trill::DIFF);
	gCraft.setPrescaler(kCraftPrescaler);
	gCraft.setNoiseThreshold(kCraftNoiseThreshold);
	gCraft.updateBaseline(); // don't touch the pads while the program starts

	if(context->analogInChannels < kNumAnalog || context->audioInChannels < kNumPiezo) {
		fprintf(stderr, "Need %d analog and %d audio inputs, project has %u and %u\n",
			kNumAnalog, kNumPiezo, context->analogInChannels, context->audioInChannels);
		return false;
	}
	for(int pin : kButtonPins)
		pinMode(context, 0, pin, INPUT);

	// the display is optional: the rest works without it
	if(gOled.setup(1, kOledAddress))
		gOled.show("Signal Lab\nwaiting");
	else
		fprintf(stderr, "No OLED at %#x, display disabled\n", kOledAddress);
	gOscIn.setup(kDisplayPort, onDisplay);

	gOsc.setup(kRemotePort, kRemoteIp);
	Bela_runAuxiliaryTask(readAndSend);
	return true;
}

void render(BelaContext* context, void* userData)
{
	// analog is sampled here (audio thread); smoothing kills ADC jitter before the dead band
	static float smooth[kNumAnalog];
	for(unsigned int f = 0; f < context->analogFrames; f++)
		for(int n = 0; n < kNumAnalog; n++)
			smooth[n] += kPotSmooth * (analogRead(context, f, n) - smooth[n]);
	for(int n = 0; n < kNumAnalog; n++)
		gIn[n] = std::min(1.f, smooth[n] / kPotMax);

	static float env[kNumPiezo];
	for(int n = 0; n < kNumPiezo; n++) {
		for(unsigned int f = 0; f < context->audioFrames; f++) {
			float a = std::fabs(audioRead(context, f, n));
			env[n] = a > env[n] ? a : env[n] * kPiezoRelease;
		}
		float pz = std::min(1.f, env[n] * kPiezoGain);
		gIn[kNumAnalog + n] = pz > kPiezoFloor ? pz : 0.f;
	}

	// ponytail: no debounce beyond the ~12 ms send poll, add a hold time if presses double-fire
	for(int n = 0; n < kNumButtons; n++)
		gIn[kNumAnalog + kNumPiezo + n] = digitalRead(context, context->digitalFrames - 1, kButtonPins[n]) ? 0.f : 1.f;
}

void cleanup(BelaContext* context, void* userData) {}
