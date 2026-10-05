// Trill Flex + Craft and the OLED, everything on I2C. A plain Linux program next to SuperCollider (_main.scd),
// which owns the Bela audio core: run.sh builds and starts both. Trill readings go to sclang as /signal/<name>
// (0..1); sclang plays their sounds and forwards them to the Signal Lab on the Mac.
// Follows Bela's Trill/flex-visual example: Flex runs in DIFF mode and touches
// are computed here with CentroidDetection.
#include <cmath>
#include <csignal>
#include <algorithm>
#include <thread>
#include <libraries/Trill/Trill.h>
#include <libraries/Trill/CentroidDetection.h>
#include <oscpkt.hh>
#include <I2c.h>
#include <string>
#include <arpa/inet.h>
#include <sys/prctl.h>
#include <sys/socket.h>
#include <unistd.h>

const int kSignalPort = 2346;  // sclang on this Bela (_main.scd), forwards to the Signal Lab on the same port
const int kDisplayPort = 2347; // Signal Lab sends /display "<text>" and /craft/* here
const unsigned int kPollUs = 12000; // ~80 Hz, same as Bela's Trill examples
// ponytail: fixed dead band, tune if the laser jitters or feels steppy
const float kMinChange = 0.005f;
// Flex tuning knobs, values recommended in the flex-visual example
const int kPrescaler = 4;           // higher = more sensitive / more resistive material
const float kNoiseThreshold = 0.03f;
// Craft tuning knobs, live-tunable like Bela's Trill/craft-visual example: send /craft/<name> <float> to udp :2347
// (the Signal Lab's Craft panel does), or /craft/calibrate for a new baseline. Each change prints the full set;
// copy it back here once the pads feel right. Each pad is scaled 0..1 between threshold and its own max, which
// starts at full and grows to the strongest reading seen, so pads of different sizes all reach 1.
// Watch "[craft] peak" (printed once a second) while touching pads to pick threshold and full.
enum { kCraftPrescaler, kCraftNoise, kCraftBits, kCraftSpeed, kCraftThreshold, kCraftFull, kNumCraftSet };
const char* kCraftSetNames[kNumCraftSet] = {"prescaler", "noise", "bits", "speed", "threshold", "full"};
const float kCraftDefaults[kNumCraftSet] = {
	1,     // prescaler 1..8: higher = longer charge, for bigger pads, resistive material, longer wires; lower = proximity
	0.f,   // noise 0..1: raw readings below this are zeroed by the Craft itself, typically < 0.1
	12,    // bits 9..16: scan resolution, more = finer but slower scan
	0,     // speed 0..3: 0 = fastest scan, 3 = slowest and least noisy
	0.05f, // threshold: DIFF reading above this = pad touched
	0.3f,  // full: starting max per pad, a firm touch at least this strong reads 1
};
const float kCraftMin[kNumCraftSet] = {1, 0, 9, 0, 0, 0.01f}, kCraftMax[kNumCraftSet] = {8, 1, 16, 3, 1, 1};
// Pots, joystick, piezos and buttons are read by SuperCollider, their tuning knobs are at the top of _main.scd.
const int kOledAddress = 0x3C; // SSD1306 128x64, same I2C bus as the Trills

Trill gFlex;
Trill gCraft; // same I2C bus as the Flex, default address 0x30
CentroidDetection gCd;
int gSock = -1; // UDP, sends to sclang and receives on kDisplayPort
sockaddr_in gSclang;
volatile sig_atomic_t gStop = 0;
// Set by the OSC thread, applied in readAndSend() (I2C stays in one thread). -1 = nothing pending.
volatile bool gCalibrate = false;
volatile float gCraftPending[kNumCraftSet];

void sendSignal(const char* address, float v)
{
	oscpkt::PacketWriter pw;
	oscpkt::Message msg(address);
	pw.addMessage(msg.pushFloat(v));
	sendto(gSock, pw.packetData(), pw.packetSize(), 0, (sockaddr*)&gSclang, sizeof gSclang);
}

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

void onDisplay(oscpkt::Message* msg)
{
	if(msg->match("/craft/calibrate").isOkNoMoreArgs()) {
		gCalibrate = true;
		return;
	}
	for(int i = 0; i < kNumCraftSet; i++) {
		float v;
		if(msg->match(std::string("/craft/") + kCraftSetNames[i]).popFloat(v).isOkNoMoreArgs()) {
			gCraftPending[i] = std::min(kCraftMax[i], std::max(kCraftMin[i], v));
			return;
		}
	}
	std::string text;
	if(msg->match("/display").popStr(text).isOkNoMoreArgs()) {
		printf("[display] %s\n", text.c_str());
		if(gOled.ok)
			gOled.show(text);
	}
}

void readAndSend()
{
	// no NaN sentinel: Bela builds with -ffast-math, which assumes NaN never happens
	float last = 0.f;
	bool sent = false;
	unsigned int lastTouches = 0;
	uint32_t lastPads = 0; // bit n = Craft pad n touched (30 pads)
	float lastPad[32] = {}; // last value sent per Craft pad
	float padMax[32];        // strongest reading per Craft pad, see full in kCraftDefaults
	float cfg[kNumCraftSet]; // current Craft settings, sent to the Craft from gCraftPending on the first pass
	std::copy(kCraftDefaults, kCraftDefaults + kNumCraftSet, cfg);
	std::fill(padMax, padMax + 32, cfg[kCraftFull]);
	while(!gStop) {
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
				sendSignal("/signal/flex", loc);
				printf("[osc] /signal/flex %.3f\n", loc);
				last = loc;
				sent = true;
			}
		}
		bool changed = false;
		for(int i = 0; i < kNumCraftSet; i++) {
			float v = gCraftPending[i];
			if(v < 0.f)
				continue;
			gCraftPending[i] = -1.f;
			cfg[i] = v;
			changed = true;
			if(i == kCraftPrescaler)
				gCraft.setPrescaler(v);
			else if(i == kCraftNoise)
				gCraft.setNoiseThreshold(v);
			else if(i == kCraftBits || i == kCraftSpeed)
				gCraft.setScanSettings(cfg[kCraftSpeed], cfg[kCraftBits]);
			// prescaler and scan settings shift the raw levels, so they need a new baseline too
			if(i != kCraftNoise && i != kCraftThreshold)
				gCalibrate = true;
		}
		if(changed)
			printf("[craft] prescaler %.0f noise %.3f bits %.0f speed %.0f threshold %.3f full %.3f\n",
				cfg[kCraftPrescaler], cfg[kCraftNoise], cfg[kCraftBits], cfg[kCraftSpeed], cfg[kCraftThreshold], cfg[kCraftFull]);
		if(gCalibrate) {
			gCalibrate = false;
			gCraft.updateBaseline(); // hands off the pads while this runs
			std::fill(padMax, padMax + 32, cfg[kCraftFull]);
			printf("[craft] calibrated: new baseline, pad max reset to %.3f\n", cfg[kCraftFull]);
		}
		gCraft.readI2C();
		uint32_t pads = 0;
		char addr[24];
		for(size_t n = 0; n < gCraft.rawData.size() && n < 32; n++) {
			// below threshold = 0, so idle pad noise isn't sent
			float raw = gCraft.rawData[n];
			padMax[n] = std::max(padMax[n], raw);
			float th = std::min(cfg[kCraftThreshold], padMax[n] * 0.99f); // keep the divisor > 0
			float v = raw > th ? std::min(1.f, (raw - th) / (padMax[n] - th)) : 0.f;
			if(v > 0.f)
				pads |= 1u << n;
			if(std::fabs(v - lastPad[n]) > kMinChange) {
				snprintf(addr, sizeof addr, "/signal/craft/%zu", n);
				sendSignal(addr, v);
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


void receive()
{
	char buf[1024];
	while(!gStop) {
		ssize_t n = recv(gSock, buf, sizeof buf, 0);
		if(n <= 0)
			continue;
		oscpkt::PacketReader pr(buf, n);
		while(oscpkt::Message* msg = pr.popMessage())
			onDisplay(msg);
	}
}

int main()
{
	setvbuf(stdout, nullptr, _IOLBF, 0); // run.sh pipes our log, keep it line by line
	prctl(PR_SET_PDEATHSIG, SIGTERM);    // stop with run.sh, so nothing keeps the I2C bus or port 2347
	signal(SIGINT, [](int) { gStop = 1; });
	signal(SIGTERM, [](int) { gStop = 1; });

	if(gFlex.setup(1, Trill::FLEX) != 0) {
		fprintf(stderr, "Unable to initialise Trill Flex\n");
		return 1;
	}
	gFlex.printDetails();
	gFlex.setMode(Trill::DIFF);
	gFlex.setPrescaler(kPrescaler);
	gFlex.setNoiseThreshold(kNoiseThreshold);
	gFlex.updateBaseline(); // don't touch the strip while the program starts
	gCd.setup(gFlex.getNumChannels(), 1, 3200);

	if(gCraft.setup(1, Trill::CRAFT) != 0) {
		fprintf(stderr, "Unable to initialise Trill Craft\n");
		return 1;
	}
	gCraft.printDetails();
	gCraft.setMode(Trill::DIFF);
	for(int i = 0; i < kNumCraftSet; i++)
		gCraftPending[i] = kCraftDefaults[i]; // applied by readAndSend() on its first pass
	gCraft.updateBaseline(); // don't touch the pads while the program starts

	// the display is optional: the rest works without it
	if(gOled.setup(1, kOledAddress))
		gOled.show("Signal Lab\nwaiting");
	else
		fprintf(stderr, "No OLED at %#x, display disabled\n", kOledAddress);

	gSock = socket(AF_INET, SOCK_DGRAM, 0);
	sockaddr_in local = {};
	local.sin_family = AF_INET;
	local.sin_addr.s_addr = htonl(INADDR_ANY);
	local.sin_port = htons(kDisplayPort);
	if(bind(gSock, (sockaddr*)&local, sizeof local) != 0) {
		perror("Unable to listen on udp :2347");
		return 1;
	}
	gSclang.sin_family = AF_INET;
	gSclang.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
	gSclang.sin_port = htons(kSignalPort);
	std::thread(receive).detach();

	readAndSend();
	return 0;
}
