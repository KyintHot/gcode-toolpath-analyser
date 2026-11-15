#!/usr/bin/env python
"""Start the G-code analyser web app.

    python run.py                 # http://127.0.0.1:8000
    python run.py --port 9000
    python run.py --host 0.0.0.0  # expose on the local network
"""

from __future__ import annotations

import argparse
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _free_port(preferred: int, host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, preferred))
            return preferred
        except OSError:
            pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((host, 0))
        return probe.getsockname()[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the G-code analyser web app")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    if not (ROOT / "web" / "index.html").is_file():
        print("error: web/ assets are missing", file=sys.stderr)
        return 2

    try:
        import uvicorn
    except ImportError:
        print(
            "error: dependencies are missing.\n"
            "       pip install -r requirements.txt",
            file=sys.stderr,
        )
        return 2

    port = _free_port(args.port, args.host)
    url = f"http://{'localhost' if args.host in ('127.0.0.1', '0.0.0.0') else args.host}:{port}"
    print("G-code toolpath analyser")
    print(f"  serving {url}")
    print("  press Ctrl+C to stop")
    sys.path.insert(0, str(ROOT))
    uvicorn.run(
        "gcode_analyzer.api:app",
        host=args.host,
        port=port,
        log_level="info",
        access_log=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
