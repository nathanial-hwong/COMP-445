import argparse
import hashlib
import subprocess
import sys
import time
from pathlib import Path


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(8192)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one UDP file-transfer demo")
    parser.add_argument("--python-exe", default=sys.executable, help="Python executable for server/client")
    parser.add_argument("--host", default="127.0.0.1", help="Server bind/target IP")
    parser.add_argument("--port", type=int, default=9000, help="UDP port")
    parser.add_argument("--segment-size", type=int, default=512, help="DATA payload size")
    parser.add_argument("--timeout", type=float, default=1.0, help="Timeout seconds")
    parser.add_argument("--input-file", default="apple.jpg", help="File in Lab3 to transfer")
    parser.add_argument("--output-file", default=None, help="Output file name in Lab3")
    parser.add_argument("--trace-file", default="transfer_trace.log", help="Combined trace output file")
    args = parser.parse_args()

    base = Path(__file__).resolve().parent
    output_dir = base / "output"
    output_dir.mkdir(exist_ok=True)
    
    input_path = base / args.input_file
    if not input_path.is_file():
        raise SystemExit(f"Input file not found: {input_path}")

    if args.output_file:
        output_path = output_dir / args.output_file
    else:
        output_path = output_dir / f"{input_path.stem}_out{input_path.suffix}"

    trace_path = output_dir / args.trace_file
    server_tmp = base / "_tmp_server_trace.log"
    client_tmp = base / "_tmp_client_trace.log"

    for p in (output_path, trace_path, server_tmp, client_tmp):
        if p.exists():
            p.unlink()

    server_cmd = [
        args.python_exe,
        "server.py",
        "--bind",
        args.host,
        "--port",
        str(args.port),
        "--segment-size",
        str(args.segment_size),
        "--timeout",
        str(args.timeout),
        "--base-dir",
        ".",
        "--trace-file",
        server_tmp.name,
    ]

    client_cmd = [
        args.python_exe,
        "client.py",
        args.host,
        str(args.port),
        input_path.name,
        "--segment-size",
        str(args.segment_size),
        "--timeout",
        str(args.timeout),
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
        client_run = subprocess.run(client_cmd, cwd=base, check=False)
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
            f"==== run segment-size={args.segment_size} file={input_path.name} "
            f"host={args.host} port={args.port} ====\n"
        )
        if server_tmp.exists():
            merged.write(server_tmp.read_text(encoding="utf-8"))
        if client_tmp.exists():
            merged.write(client_tmp.read_text(encoding="utf-8"))

    if server_tmp.exists():
        server_tmp.unlink()
    if client_tmp.exists():
        client_tmp.unlink()

    print(f"PASS segment-size={args.segment_size} hash={out_hash}")
    print(f"Output: {output_path}")
    print(f"Trace:  {trace_path}")


if __name__ == "__main__":
    main()
