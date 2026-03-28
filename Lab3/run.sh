#!/usr/bin/env bash
set -euo pipefail

# Linux-only test driver for UDP stop-and-wait protocol using netns + tc netem.
# Run with sudo.

LAB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RESULTS_DIR="$LAB_DIR/netem_results"
INPUT_FILE="apple.jpg"
OUTPUT_DELAY="netem_results/apple_out_delay.jpg"
OUTPUT_LOSS="netem_results/apple_out_loss.jpg"

SERVER_NS="srvns"
CLIENT_NS="clins"
SERVER_VETH="veth-srv"
CLIENT_VETH="veth-cli"

SERVER_IP="10.44.0.1"
CLIENT_IP="10.44.0.2"
PORT="9000"
SEGMENT_SIZE="512"
TIMEOUT="0.8"

PYTHON_BIN="python3"

cleanup_ns() {
  ip netns del "$SERVER_NS" 2>/dev/null || true
  ip netns del "$CLIENT_NS" 2>/dev/null || true
}

setup_ns() {
  cleanup_ns

  ip netns add "$SERVER_NS"
  ip netns add "$CLIENT_NS"

  ip link add "$SERVER_VETH" type veth peer name "$CLIENT_VETH"

  ip link set "$SERVER_VETH" netns "$SERVER_NS"
  ip link set "$CLIENT_VETH" netns "$CLIENT_NS"

  ip -n "$SERVER_NS" addr add "$SERVER_IP/24" dev "$SERVER_VETH"
  ip -n "$CLIENT_NS" addr add "$CLIENT_IP/24" dev "$CLIENT_VETH"

  ip -n "$SERVER_NS" link set lo up
  ip -n "$CLIENT_NS" link set lo up

  ip -n "$SERVER_NS" link set "$SERVER_VETH" up
  ip -n "$CLIENT_NS" link set "$CLIENT_VETH" up
}

reset_outputs() {
  mkdir -p "$RESULTS_DIR"
  rm -f "$RESULTS_DIR"/*.log "$RESULTS_DIR"/*.tmp.log "$RESULTS_DIR"/*_summary.txt "$LAB_DIR/$OUTPUT_DELAY" "$LAB_DIR/$OUTPUT_LOSS"
}

run_one() {
  local scenario_name="$1"
  local output_file="$2"
  local netem_rule="$3"

  local server_log_tmp="$RESULTS_DIR/.${scenario_name}_server.tmp.log"
  local client_log_tmp="$RESULTS_DIR/.${scenario_name}_client.tmp.log"
  local combined_log="$RESULTS_DIR/${scenario_name}_trace.log"

  # Apply same impairment in both directions.
  ip netns exec "$SERVER_NS" tc qdisc replace dev "$SERVER_VETH" root netem $netem_rule
  ip netns exec "$CLIENT_NS" tc qdisc replace dev "$CLIENT_VETH" root netem $netem_rule

  ip netns exec "$SERVER_NS" bash -lc "cd '$LAB_DIR' && $PYTHON_BIN server.py --bind $SERVER_IP --port $PORT --segment-size $SEGMENT_SIZE --timeout $TIMEOUT --base-dir . --trace-file '$server_log_tmp'" &
  local server_pid=$!

  sleep 0.6

  local start_time end_time elapsed
  start_time="$(date +%s.%N)"

  set +e
  ip netns exec "$CLIENT_NS" bash -lc "cd '$LAB_DIR' && $PYTHON_BIN client.py $SERVER_IP $PORT '$INPUT_FILE' --segment-size $SEGMENT_SIZE --timeout $TIMEOUT --output '$output_file' --trace-file '$client_log_tmp'"
  local client_rc=$?
  set -e

  end_time="$(date +%s.%N)"
  elapsed="$(python3 - "$start_time" "$end_time" <<'PY'
import sys
start = float(sys.argv[1])
end = float(sys.argv[2])
print(f"{end - start:.6f}")
PY
)"

  kill "$server_pid" 2>/dev/null || true
  wait "$server_pid" 2>/dev/null || true

  if [[ "$client_rc" -ne 0 ]]; then
    echo "[$scenario_name] client failed with exit code $client_rc"
    return 1
  fi

  cat "$server_log_tmp" "$client_log_tmp" > "$combined_log"
  rm -f "$server_log_tmp" "$client_log_tmp"

  local src_hash out_hash file_bytes retries throughput
  src_hash="$(sha256sum "$LAB_DIR/$INPUT_FILE" | awk '{print $1}')"
  out_hash="$(sha256sum "$LAB_DIR/$output_file" | awk '{print $1}')"
  file_bytes="$(stat -c%s "$LAB_DIR/$INPUT_FILE")"
  retries="$(grep -c 'type=RETRY' "$combined_log" || true)"
  throughput="$(python3 - "$file_bytes" "$elapsed" <<'PY'
import sys
size = float(sys.argv[1])
elapsed = float(sys.argv[2])
if elapsed <= 0:
    print("0.00")
else:
    print(f"{(size/elapsed):.2f}")
PY
)"

  {
    echo "scenario=$scenario_name"
    echo "netem=$netem_rule"
    echo "segment_size=$SEGMENT_SIZE"
    echo "timeout_s=$TIMEOUT"
    echo "elapsed_s=$elapsed"
    echo "file_bytes=$file_bytes"
    echo "throughput_bytes_per_s=$throughput"
    echo "retries=$retries"
    echo "src_hash=$src_hash"
    echo "out_hash=$out_hash"
    if [[ "$src_hash" == "$out_hash" ]]; then
      echo "status=SUCCESS"
    else
      echo "status=HASH_MISMATCH"
    fi
  } > "$RESULTS_DIR/${scenario_name}_summary.txt"

  echo "[$scenario_name] done"
  cat "$RESULTS_DIR/${scenario_name}_summary.txt"
}

main() {
  if [[ "${EUID}" -ne 0 ]]; then
    echo "Please run with sudo."
    exit 1
  fi

  if [[ ! -f "$LAB_DIR/$INPUT_FILE" ]]; then
    echo "Missing input file: $LAB_DIR/$INPUT_FILE"
    exit 1
  fi

  reset_outputs
  setup_ns

  run_one "delay" "$OUTPUT_DELAY" "delay 120ms 25ms"
  run_one "loss" "$OUTPUT_LOSS" "loss 8%"

  cleanup_ns
  echo "All scenarios completed. Results in: $RESULTS_DIR"
}

main "$@"
