#!/bin/bash
# Starts the osc2laser receiver and the Signal Lab; every log line is prefixed with its app. Ctrl-C stops both.
# -v / --verbose: Signal Lab logs every signal in/out and the receiver logs its OSC handling; without it both are quiet.
# The receiver runs on a temp copy of its config (-c, default config_laser1.txt) with the [logging] switches set.
cd "$(dirname "$0")"
export PYTHONUNBUFFERED=1 # otherwise python holds logs back when piped
cfg=$(mktemp -t osc2laser-config)
trap 'trap - INT TERM EXIT; rm -f "$cfg"; kill 0' INT TERM EXIT

lab_args=() receiver_args=() verbose= base=osc2laser/osc-receiver/config_laser1.txt
while [ $# -gt 0 ]; do
	case $1 in
		-v|--verbose) verbose=1; lab_args+=(--verbose) ;;
		-c|--config) case $2 in /*) base=$2 ;; *) base=osc2laser/osc-receiver/$2 ;; esac; shift ;; # relative to osc-receiver/, as before
		*) receiver_args+=("$1") ;;
	esac
	shift
done

# everything in [logging] off; -v turns the osc_server_* switches back on (per-frame point logs stay off)
sed '/^\[logging\]/,/^\[/ s/= *yes/= no/' "$base" > "$cfg" || exit 1
[ -n "$verbose" ] && sed -i '' '/^\[logging\]/,/^\[/ s/^\(osc_server_[a-z_]*\) *= *no/\1 = yes/' "$cfg"
receiver_args+=(-c "$cfg")

osc2laser/osc-receiver/start.sh "${receiver_args[@]}" 2>&1 | sed -l 's/^/[osc2laser]  /' &
osc2laser/osc-receiver/.venv/bin/python signal-lab/signal_lab.py ${lab_args[@]+"${lab_args[@]}"} 2>&1 | sed -l 's/^/[signal-lab] /' &
# ponytail: if one app dies the other keeps running, check the prefixes; Ctrl-C and restart
wait
