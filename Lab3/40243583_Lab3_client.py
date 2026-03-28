import argparse
import enum
import random
import socket
import struct
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


# Protocol definitions (from protocol.py)
class MessageType(enum.IntEnum):
    REQUEST = 1
    DATA = 2
    ACK = 3
    ERROR = 4


# Header: connection_id (4B), sequence_number (4B), message_type (1B), payload_length (2B)
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


# Client code
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


def main() -> None:
    parser = argparse.ArgumentParser(description="UDP stop-and-wait file client")
    parser.add_argument("server_ip", help="Server IP address")
    parser.add_argument("server_port", type=int, help="Server UDP port")
    parser.add_argument("filename", help="File name to request")
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

    if opts.segment_size <= 0:
        raise SystemExit("--segment-size must be > 0")

    if opts.timeout <= 0:
        raise SystemExit("--timeout must be > 0")

    run_client(opts)


if __name__ == "__main__":
    main()
