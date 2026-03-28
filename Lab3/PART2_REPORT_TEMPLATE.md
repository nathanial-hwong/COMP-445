# Part 2 Report Template: UDP Protocol under Emulated Network Conditions

## 1. Experimental Setup
- Host OS: Linux (kernel version: __________)
- Test isolation: network namespaces + veth pair
- Namespaces used: `srvns` and `clins`
- Server IP / port: `10.44.0.1:9000`
- Client IP: `10.44.0.2`
- Segment size: `512` bytes (or state your value)
- Timeout: `0.8` s (or state your value)
- Input file: `apple.jpg` (size: __________ bytes)

Commands used:
```bash
cd Lab3
sudo bash netem_test.sh
```

## 2. Delay Scenario
### Netem settings
- `delay 120ms 25ms`
- Applied on both veth directions

### Results
- Status (success/failure): __________
- Transfer time (s): __________
- Throughput (bytes/s): __________
- Retransmissions (`type=RETRY` count): __________
- Hash match: yes / no

### Observations
- REQUEST/DATA/ACK behavior under delay:
- Any stalls or anomalies:
- Did transfer complete cleanly without duplicate file data:

## 3. Loss Scenario
### Netem settings
- `loss 8%`
- Applied on both veth directions

### Results
- Status (success/failure): __________
- Transfer time (s): __________
- Throughput (bytes/s): __________
- Retransmissions (`type=RETRY` count): __________
- Hash match: yes / no

### Observations
- REQUEST/DATA/ACK behavior under loss:
- Retransmission behavior:
- Any anomalies:

## 4. Discussion
- Compare delay vs loss impact on transfer time and throughput.
- Explain why stop-and-wait is sensitive to RTT and packet loss.
- Note any protocol limitations observed.

## 5. Artifacts
Attach or reference these files from `Lab3/netem_results`:
- `delay_summary.txt`
- `loss_summary.txt`
- `delay_trace.log`
- `loss_trace.log`
- `delay_server.log` / `delay_client.log`
- `loss_server.log` / `loss_client.log`
