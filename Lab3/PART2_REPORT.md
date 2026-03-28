# Part 2 Report: UDP Protocol under Emulated Network Conditions

## 1. Experimental Setup
- Host OS: Linux (via network namespace test script execution)
- Test isolation: Linux network namespaces with a veth pair
- Namespaces: srvns (server), clins (client)
- Interfaces: veth-srv (server side), veth-cli (client side)
- Server address: 10.44.0.1:9000
- Client address: 10.44.0.2
- Segment size: 512 bytes
- Timeout: 0.8 s
- Input file: apple.jpg (5644 bytes)
- Protocol mode: stop-and-wait over UDP
- Netem application model: same impairment applied in both directions on both veth interfaces

Run command:

```bash
cd Lab3
sudo bash run.sh
```

## 2. Delay Scenario
### Netem Settings
- Exact rule: delay 120ms 25ms
- Applied on: veth-srv and veth-cli root qdisc (bidirectional path impairment)

### Results
- Status: SUCCESS
- Transfer time: 5.991152 s
- Throughput: 942.06 bytes/s
- Retransmissions (type=RETRY): 4
- Hash check: source and output hashes match

### Retransmission Behavior and Anomalies
- Multiple timeout-driven retries were observed on the server side (DATA retransmissions).
- Client-side control retry and duplicate ACK behavior appeared in the trace, indicating delayed/out-of-order arrivals rather than data corruption.
- No fatal decode/drop/error pattern was observed; transfer completed correctly with integrity preserved.

### Impact Summary
- Delay + jitter significantly increased wall-clock transfer time and reduced throughput.
- Stop-and-wait sensitivity to RTT inflation is visible because each lost/delayed ACK blocks progression to the next segment.

## 3. Loss Scenario
### Netem Settings
- Exact rule: loss 8%
- Applied on: veth-srv and veth-cli root qdisc (bidirectional path impairment)

### Results
- Status: SUCCESS
- Transfer time: 3.420541 s
- Throughput: 1650.03 bytes/s
- Retransmissions (type=RETRY): 3
- Hash check: source and output hashes match

### Retransmission Behavior and Anomalies
- Timeout-based retries occurred (fewer than in the delay scenario).
- A client control retry was observed, consistent with occasional dropped control/ACK packets.
- No hash mismatch, decode failure, or unrecoverable error occurred.

### Impact Summary
- Even with configured random loss, this run completed faster than the delay case due to lower effective waiting overhead in this sample.
- Throughput remained higher than the delay+jitter scenario despite retransmissions.

## 4. Comparative Discussion
- Delay scenario showed higher transfer time and lower throughput than loss scenario in this measurement set.
- In stop-and-wait, each packet exchange is serialized, so added RTT/jitter directly reduces pipeline utilization.
- Random loss causes retransmissions, but if losses are sparse in a specific run, total completion time can still beat a consistently high-delay channel.
- The protocol handled both impairments robustly (all scenarios ended SUCCESS with matching hashes).

## 5. Artifacts Used
- netem_results/delay_summary.txt
- netem_results/loss_summary.txt
- netem_results/delay_trace.log
- netem_results/loss_trace.log
- netem_results/apple_out_delay.jpg
- netem_results/apple_out_loss.jpg
