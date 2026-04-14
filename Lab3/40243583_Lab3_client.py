import argparse
import enum
import hashlib
import random
import socket
import struct
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


class MessageType(enum.IntEnum):
    REQUEST = 1
    DATA = 2
    ACK = 3
    ERROR = 4


HEADER_STRUCT = struct.Struct("!IIBH")
HEADER_SIZE = HEADER_STRUCT.size


@dataclass(frozen=True)
class Packet:
    connection_id: int
    sequence_number: int
    message_type: MessageType
    payload: bytes = b""

    def encode(self) -> bytes:
        payload_length = len(self.payload)
        header = HEADER_STRUCT.pack(
            self.connection_id,
            self.sequence_number,
            int(self.message_type),
            payload_length,
        )
        return header + self.payload

    @staticmethod
    def decode(raw: bytes) -> "Packet":
        if len(raw) < HEADER_SIZE:
            raise ValueError("packet too short")

        connection_id, sequence_number, message_type, payload_length = HEADER_STRUCT.unpack(
            raw[:HEADER_SIZE]
        )

        payload = raw[HEADER_SIZE:]
        if len(payload) != payload_length:
            raise ValueError("payload length mismatch")

        try:
            parsed_type = MessageType(message_type)
        except ValueError as exc:
            raise ValueError("unknown message type") from exc

        return Packet(
            connection_id=connection_id,
            sequence_number=sequence_number,
            message_type=parsed_type,
            payload=payload,
        )


def make_request(connection_id: int, filename: str) -> Packet:
    return Packet(
        connection_id=connection_id,
        sequence_number=0,
        message_type=MessageType.REQUEST,
        payload=filename.encode("utf-8"),
    )


def make_ack(connection_id: int, sequence_number: int) -> Packet:
    return Packet(
        connection_id=connection_id,
        sequence_number=sequence_number,
        message_type=MessageType.ACK,
        payload=b"",
    )


def log_msg(
    log_file,
    direction: str,
    message_type: str,
    src: str,
    dst: str,
    connection_id: int,
    sequence_number: int,
    data_qty: int,
    note: str = "",
) -> None:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    text = (
        f"[{stamp}] role=CLIENT dir={direction} type={message_type} "
        f"src={src} dst={dst} cid={connection_id} seq={sequence_number} bytes={data_qty} note={note}"
    )
    if log_file is not None:
        log_file.write(text + "\n")
        log_file.flush()


def run_client(opts: argparse.Namespace) -> None:
    srv = (opts.server_ip, opts.server_port)
    cid = random.getrandbits(32)

    req_pkt = make_request(cid, opts.filename)
    last_msg = req_pkt.encode()

    want_seq = 0
    if opts.output:
        save_to = Path(opts.output)
    else:
        save_to = Path("downloaded_" + Path(opts.filename).name)

    if opts.trace_file:
        log_handle = open(opts.trace_file, "w", encoding="utf-8")
    else:
        log_handle = None

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s, save_to.open("wb") as f:
            s.settimeout(opts.timeout)
            s.sendto(last_msg, srv)
            log_msg(
                log_handle,
                "OUT",
                "REQUEST",
                f"{s.getsockname()[0]}:{s.getsockname()[1]}",
                f"{srv[0]}:{srv[1]}",
                cid,
                0,
                len(req_pkt.payload),
            )

            while True:
                try:
                    data, from_addr = s.recvfrom(opts.segment_size + HEADER_SIZE + 64)
                except socket.timeout:
                    s.sendto(last_msg, srv)
                    log_msg(
                        log_handle,
                        "OUT",
                        "RETRY",
                        f"{s.getsockname()[0]}:{s.getsockname()[1]}",
                        f"{srv[0]}:{srv[1]}",
                        cid,
                        want_seq,
                        len(last_msg),
                        "timeout-resend-last-control",
                    )
                    continue

                if from_addr != srv:
                    log_msg(
                        log_handle,
                        "IN",
                        "DROP",
                        f"{from_addr[0]}:{from_addr[1]}",
                        f"{s.getsockname()[0]}:{s.getsockname()[1]}",
                        -1,
                        -1,
                        len(data),
                        "unexpected-source",
                    )
                    continue

                try:
                    pkt = Packet.decode(data)
                except ValueError:
                    log_msg(
                        log_handle,
                        "IN",
                        "DROP",
                        f"{from_addr[0]}:{from_addr[1]}",
                        f"{s.getsockname()[0]}:{s.getsockname()[1]}",
                        -1,
                        -1,
                        len(data),
                        "decode-failed",
                    )
                    continue

                log_msg(
                    log_handle,
                    "IN",
                    pkt.message_type.name,
                    f"{from_addr[0]}:{from_addr[1]}",
                    f"{s.getsockname()[0]}:{s.getsockname()[1]}",
                    pkt.connection_id,
                    pkt.sequence_number,
                    len(pkt.payload),
                )

                if pkt.connection_id != cid:
                    log_msg(
                        log_handle,
                        "IN",
                        "DROP",
                        f"{from_addr[0]}:{from_addr[1]}",
                        f"{s.getsockname()[0]}:{s.getsockname()[1]}",
                        pkt.connection_id,
                        pkt.sequence_number,
                        len(pkt.payload),
                        f"wrong-connection wanted={cid}",
                    )
                    continue

                if pkt.message_type == MessageType.ERROR:
                    msg = pkt.payload.decode("utf-8", errors="replace")
                    raise RuntimeError(f"server error: {msg}")

                if pkt.message_type != MessageType.DATA:
                    continue

                if pkt.sequence_number == want_seq:
                    f.write(pkt.payload)

                    ack_bytes = make_ack(cid, want_seq).encode()
                    s.sendto(ack_bytes, srv)
                    last_msg = ack_bytes
                    log_msg(
                        log_handle,
                        "OUT",
                        "ACK",
                        f"{s.getsockname()[0]}:{s.getsockname()[1]}",
                        f"{srv[0]}:{srv[1]}",
                        cid,
                        want_seq,
                        0,
                    )

                    if len(pkt.payload) < opts.segment_size:
                        break

                    want_seq = 1 - want_seq
                else:
                    old_ack = 1 - want_seq
                    ack_bytes = make_ack(cid, old_ack).encode()
                    s.sendto(ack_bytes, srv)
                    last_msg = ack_bytes
                    log_msg(
                        log_handle,
                        "OUT",
                        "ACK",
                        f"{s.getsockname()[0]}:{s.getsockname()[1]}",
                        f"{srv[0]}:{srv[1]}",
                        cid,
                        old_ack,
                        0,
                        "duplicate-data-ack",
                    )
    finally:
        if log_handle is not None:
            log_handle.close()

    print(f"File received successfully: {save_to}")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(8192)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def run_demo() -> None:
    base = Path(__file__).resolve().parent
    output_dir = base / "output"
    output_dir.mkdir(exist_ok=True)

    host = "127.0.0.1"
    port = 9000
    segment_size = 512
    timeout = 1.0
    input_file = "apple.jpg"

    input_path = base / input_file
    if not input_path.is_file():
        raise SystemExit(f"Input file not found: {input_path}")

    output_path = output_dir / f"{input_path.stem}_out{input_path.suffix}"
    trace_path = output_dir / "transfer_trace.log"
    summary_path = output_dir / "transfer_summary.txt"
    server_tmp = base / "_tmp_server_trace.log"
    client_tmp = base / "_tmp_client_trace.log"

    for p in (output_path, trace_path, summary_path, server_tmp, client_tmp):
        if p.exists():
            p.unlink()

    server_cmd = [
        sys.executable,
        "40243583_Lab3_server.py",
        "--bind",
        host,
        "--port",
        str(port),
        "--segment-size",
        str(segment_size),
        "--timeout",
        str(timeout),
        "--base-dir",
        ".",
        "--trace-file",
        server_tmp.name,
    ]

    client_cmd = [
        sys.executable,
        "40243583_Lab3_client.py",
        host,
        str(port),
        input_file,
        "--segment-size",
        str(segment_size),
        "--timeout",
        str(timeout),
        "--output",
        str(output_path.relative_to(base)),
        "--trace-file",
        client_tmp.name,
    ]

    server_proc = subprocess.Popen(
        server_cmd,
        cwd=base,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        time.sleep(0.6)
        start = time.monotonic()
        client_run = subprocess.run(client_cmd, cwd=base, check=False)
        end = time.monotonic()
        if client_run.returncode != 0:
            raise SystemExit(f"Client failed with exit code {client_run.returncode}")
    finally:
        server_proc.terminate()
        try:
            server_proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            server_proc.kill()
            server_proc.wait(timeout=2)

    src_hash = sha256_file(input_path)
    out_hash = sha256_file(output_path)
    if src_hash != out_hash:
        raise SystemExit("Hash mismatch: output file does not match input")

    with trace_path.open("w", encoding="utf-8") as merged:
        merged.write(
            f"==== run segment-size={segment_size} file={input_path.name} "
            f"host={host} port={port} ====\n"
        )
        if server_tmp.exists():
            merged.write(server_tmp.read_text(encoding="utf-8"))
        if client_tmp.exists():
            merged.write(client_tmp.read_text(encoding="utf-8"))

    if server_tmp.exists():
        server_tmp.unlink()
    if client_tmp.exists():
        client_tmp.unlink()

    elapsed = max(end - start, 0.0)
    file_bytes = input_path.stat().st_size
    throughput = (file_bytes / elapsed) if elapsed > 0 else 0.0
    retries = 0
    if trace_path.exists():
        retries = trace_path.read_text(encoding="utf-8").count("type=RETRY")

    summary_path.write_text(
        "\n".join(
            [
                "scenario=demo",
                f"host={host}",
                f"port={port}",
                f"segment_size={segment_size}",
                f"timeout_s={timeout}",
                f"input_file={input_path.name}",
                f"output_file={output_path.name}",
                f"trace_file={trace_path.name}",
                f"elapsed_s={elapsed:.6f}",
                f"file_bytes={file_bytes}",
                f"throughput_bytes_per_s={throughput:.2f}",
                f"retries={retries}",
                f"src_hash={src_hash}",
                f"out_hash={out_hash}",
                f"status={'SUCCESS' if src_hash == out_hash else 'HASH_MISMATCH'}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    print(f"PASS segment-size={segment_size} hash={out_hash}")
    print(f"Output: {output_path}")
    print(f"Trace:  {trace_path}")
    print(f"Summary: {summary_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="UDP stop-and-wait file client (or demo mode with no args)")
    parser.add_argument("server_ip", nargs="?", default=None, help="Server IP address (omit for demo mode)")
    parser.add_argument("server_port", nargs="?", type=int, default=None, help="Server UDP port (omit for demo mode)")
    parser.add_argument("filename", nargs="?", default=None, help="File name to request (omit for demo mode)")
    parser.add_argument(
        "--segment-size",
        type=int,
        default=512,
        help="Maximum expected DATA payload size",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output path (default: downloaded_<filename>)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=1.0,
        help="Seconds to wait before retransmitting control packet",
    )
    parser.add_argument(
        "--trace-file",
        default=None,
        help="Optional path to write packet trace logs (overwrites on each run)",
    )
    opts = parser.parse_args()

    # Check if in demo mode
    if opts.server_ip is None:
        run_demo()
        return

    # Client mode: all positional args required
    if opts.server_port is None or opts.filename is None:
        parser.error("When using client mode, server_ip, server_port, and filename are all required")

    if opts.segment_size <= 0:
        raise SystemExit("--segment-size must be > 0")

    if opts.timeout <= 0:
        raise SystemExit("--timeout must be > 0")

    run_client(opts)


if __name__ == "__main__":
    main()
