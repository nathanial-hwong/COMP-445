import argparse
import enum
import os
import socket
import struct
import time
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


def make_data(connection_id: int, sequence_number: int, chunk: bytes) -> Packet:
    return Packet(
        connection_id=connection_id,
        sequence_number=sequence_number,
        message_type=MessageType.DATA,
        payload=chunk,
    )


def make_error(connection_id: int, message: str) -> Packet:
    return Packet(
        connection_id=connection_id,
        sequence_number=0,
        message_type=MessageType.ERROR,
        payload=message.encode("utf-8"),
    )


# Server code
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
        f"[{stamp}] role=SERVER dir={direction} type={message_type} "
        f"src={src} dst={dst} cid={connection_id} seq={sequence_number} bytes={data_qty} note={note}"
    )
    if log_file is not None:
        log_file.write(text + "\n")
        log_file.flush()


def sanitize_filename(base_dir: Path, requested_name: str) -> Path | None:
    candidate = (base_dir / requested_name).resolve()
    try:
        candidate.relative_to(base_dir.resolve())
    except ValueError:
        return None
    return candidate


def wait_for_ack(
    sock: socket.socket,
    expected_addr: tuple[str, int],
    connection_id: int,
    expected_seq: int,
    timeout: float,
    log_file,
) -> bool:
    sock.settimeout(timeout)
    try:
        raw, addr = sock.recvfrom(64 * 1024)
    except socket.timeout:
        return False
    finally:
        sock.settimeout(None)

    if addr != expected_addr:
        return False

    try:
        packet = Packet.decode(raw)
    except ValueError:
        log_msg(
            log_file,
            "IN",
            "DROP",
            f"{addr[0]}:{addr[1]}",
            f"{sock.getsockname()[0]}:{sock.getsockname()[1]}",
            -1,
            -1,
            len(raw),
            "decode-failed-while-waiting-ack",
        )
        return False

    log_msg(
        log_file,
        "IN",
        packet.message_type.name,
        f"{addr[0]}:{addr[1]}",
        f"{sock.getsockname()[0]}:{sock.getsockname()[1]}",
        packet.connection_id,
        packet.sequence_number,
        len(packet.payload),
    )

    if packet.connection_id != connection_id:
        log_msg(
            log_file,
            "IN",
            "DROP",
            f"{addr[0]}:{addr[1]}",
            f"{sock.getsockname()[0]}:{sock.getsockname()[1]}",
            packet.connection_id,
            packet.sequence_number,
            len(packet.payload),
            f"wrong-connection wanted={connection_id}",
        )
        return False

    return packet.message_type == MessageType.ACK and packet.sequence_number == expected_seq


def send_file_stop_and_wait(
    sock: socket.socket,
    client_addr: tuple[str, int],
    connection_id: int,
    file_path: Path,
    segment_size: int,
    timeout: float,
    log_file,
) -> bytes:
    seq = 0
    final_packet = b""

    with file_path.open("rb") as source:
        while True:
            chunk = source.read(segment_size)

            # For exact multiples of segment_size, send an empty final packet.
            is_final = len(chunk) < segment_size

            packet = make_data(connection_id, seq, chunk)
            encoded = packet.encode()

            while True:
                sock.sendto(encoded, client_addr)
                log_msg(
                    log_file,
                    "OUT",
                    "DATA",
                    f"{sock.getsockname()[0]}:{sock.getsockname()[1]}",
                    f"{client_addr[0]}:{client_addr[1]}",
                    connection_id,
                    seq,
                    len(chunk),
                )
                if wait_for_ack(sock, client_addr, connection_id, seq, timeout, log_file):
                    break
                log_msg(
                    log_file,
                    "OUT",
                    "RETRY",
                    f"{sock.getsockname()[0]}:{sock.getsockname()[1]}",
                    f"{client_addr[0]}:{client_addr[1]}",
                    connection_id,
                    seq,
                    len(chunk),
                    "timeout-or-bad-ack",
                )

            if is_final:
                final_packet = encoded
                break

            seq = 1 - seq

    return final_packet


def run_server(opts: argparse.Namespace) -> None:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind((opts.bind, opts.port))

    done: dict[tuple[str, int, int, str], tuple[float, bytes]] = {}
    root = Path(opts.base_dir).resolve()

    if opts.trace_file:
        log_handle = open(opts.trace_file, "w", encoding="utf-8")
    else:
        log_handle = None

    log_msg(log_handle, "SYS", "INFO", "-", "-", -1, -1, 0, f"listening={opts.bind}:{opts.port}")
    log_msg(log_handle, "SYS", "INFO", "-", "-", -1, -1, 0, f"base-dir={root}")

    try:
        while True:
            data, addr = s.recvfrom(64 * 1024)

            t = time.monotonic()
            old_keys = [k for k, (until, _) in done.items() if until <= t]
            for k in old_keys:
                del done[k]

            try:
                pkt = Packet.decode(data)
            except ValueError:
                log_msg(
                    log_handle,
                    "IN",
                    "DROP",
                    f"{addr[0]}:{addr[1]}",
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
                f"{addr[0]}:{addr[1]}",
                f"{s.getsockname()[0]}:{s.getsockname()[1]}",
                pkt.connection_id,
                pkt.sequence_number,
                len(pkt.payload),
            )

            if pkt.message_type == MessageType.REQUEST:
                try:
                    want_file = pkt.payload.decode("utf-8")
                except UnicodeDecodeError:
                    err = make_error(pkt.connection_id, "invalid filename encoding")
                    s.sendto(err.encode(), addr)
                    log_msg(
                        log_handle,
                        "OUT",
                        "ERROR",
                        f"{s.getsockname()[0]}:{s.getsockname()[1]}",
                        f"{addr[0]}:{addr[1]}",
                        pkt.connection_id,
                        0,
                        len(err.payload),
                        "invalid-filename-encoding",
                    )
                    continue

                key = (addr[0], addr[1], pkt.connection_id, want_file)
                if key in done:
                    _, last_data = done[key]
                    s.sendto(last_data, addr)
                    last_pkt = Packet.decode(last_data)
                    log_msg(
                        log_handle,
                        "OUT",
                        "DATA",
                        f"{s.getsockname()[0]}:{s.getsockname()[1]}",
                        f"{addr[0]}:{addr[1]}",
                        last_pkt.connection_id,
                        last_pkt.sequence_number,
                        len(last_pkt.payload),
                        "duplicate-request-final-resend",
                    )
                    continue

                target = sanitize_filename(root, want_file)
                if target is None or not target.is_file():
                    err = make_error(pkt.connection_id, "file not found")
                    s.sendto(err.encode(), addr)
                    log_msg(
                        log_handle,
                        "OUT",
                        "ERROR",
                        f"{s.getsockname()[0]}:{s.getsockname()[1]}",
                        f"{addr[0]}:{addr[1]}",
                        pkt.connection_id,
                        0,
                        len(err.payload),
                        "file-not-found",
                    )
                    continue

                log_msg(
                    log_handle,
                    "SYS",
                    "INFO",
                    "-",
                    "-",
                    pkt.connection_id,
                    -1,
                    0,
                    f"transfer-start file={want_file} addr={addr}",
                )
                last_data = send_file_stop_and_wait(
                    s,
                    addr,
                    pkt.connection_id,
                    target,
                    opts.segment_size,
                    opts.timeout,
                    log_handle,
                )
                done[key] = (time.monotonic() + opts.grace_period, last_data)
                log_msg(
                    log_handle,
                    "SYS",
                    "INFO",
                    "-",
                    "-",
                    pkt.connection_id,
                    -1,
                    0,
                    f"transfer-complete file={want_file} addr={addr}",
                )

            elif pkt.message_type == MessageType.ACK:
                for key, (_, last_data) in done.items():
                    ip, port, conn, _ = key
                    if (
                        ip == addr[0]
                        and port == addr[1]
                        and conn == pkt.connection_id
                        and Packet.decode(last_data).sequence_number == pkt.sequence_number
                    ):
                        s.sendto(last_data, addr)
                        last_pkt = Packet.decode(last_data)
                        log_msg(
                            log_handle,
                            "OUT",
                            "DATA",
                            f"{s.getsockname()[0]}:{s.getsockname()[1]}",
                            f"{addr[0]}:{addr[1]}",
                            last_pkt.connection_id,
                            last_pkt.sequence_number,
                            len(last_pkt.payload),
                            "duplicate-ack-final-resend",
                        )
                        break
    finally:
        if log_handle is not None:
            log_handle.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="UDP stop-and-wait file server")
    parser.add_argument("--bind", default="0.0.0.0", help="IP address to bind")
    parser.add_argument("--port", type=int, default=9000, help="UDP port to listen on")
    parser.add_argument(
        "--segment-size",
        type=int,
        default=512,
        help="Maximum data payload bytes per DATA packet",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=1.0,
        help="Seconds to wait for ACK before retransmitting",
    )
    parser.add_argument(
        "--grace-period",
        type=float,
        default=5.0,
        help="Seconds to keep finished transfer state for duplicates",
    )
    parser.add_argument(
        "--base-dir",
        default=".",
        help="Directory from which files are served",
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

    if not os.path.isdir(opts.base_dir):
        raise SystemExit("--base-dir must exist and be a directory")

    run_server(opts)


if __name__ == "__main__":
    main()
