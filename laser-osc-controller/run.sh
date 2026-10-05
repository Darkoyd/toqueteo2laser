#!/bin/bash
# Bela runs this for the project (make run, and at boot): builds trill-oled when its source changed, starts it
# (Trills + OLED on I2C), then SuperCollider (_main.scd: audio out, pots, joystick, piezos, buttons, sounds).
cd "$(dirname "$0")"
B=/root/Bela
mkdir -p build
if [ trill-oled.cpp -nt build/trill-oled ]; then # also true when the binary is missing
	echo "Building trill-oled..."
	# same flags as Bela's own builds; absolute path so the VS Code problem matcher finds errors
	g++ -O3 -ffast-math -std=c++14 -pthread -I$B -I$B/include "$PWD/trill-oled.cpp" \
		$B/libraries/Trill/Trill.cpp $B/libraries/Trill/CentroidDetection.cpp -o build/trill-oled || exit 1
fi
build/trill-oled &
helper=$!
trap 'kill $helper 2>/dev/null' EXIT
# as Bela's own SuperCollider run command: the fifo keeps sclang's stdin open
rm -f /tmp/sclangfifo && mkfifo /tmp/sclangfifo && touch /tmp/sclang.yaml
sclang -l /tmp/sclang.yaml _main.scd <> /tmp/sclangfifo
