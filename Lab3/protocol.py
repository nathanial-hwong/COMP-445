import enum
import struct
from dataclasses import dataclass


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


def make_data(connection_id: int, sequence_number: int, chunk: bytes) -> Packet:
    return Packet(
        connection_id=connection_id,
        sequence_number=sequence_number,
        message_type=MessageType.DATA,
        payload=chunk,
    )


def make_ack(connection_id: int, sequence_number: int) -> Packet:
    return Packet(
        connection_id=connection_id,
        sequence_number=sequence_number,
        message_type=MessageType.ACK,
        payload=b"",
    )


def make_error(connection_id: int, message: str) -> Packet:
    return Packet(
        connection_id=connection_id,
        sequence_number=0,
        message_type=MessageType.ERROR,
        payload=message.encode("utf-8"),
    )
