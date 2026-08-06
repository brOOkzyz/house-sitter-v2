#!/usr/bin/env python3
"""Start the localhost-only RaPToR-Lite House-Sitter visual demo."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))

from raptor_lite.demo_ui import DemoController, make_server


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the local RaPToR-Lite House-Sitter visual demo.")
    parser.add_argument("--host", default="127.0.0.1"); parser.add_argument("--port", default=8765, type=int)
    args = parser.parse_args()
    server = make_server(DemoController(ROOT / "configs/raptor_lite/create3_sim_capabilities.yaml", ROOT / "artifacts/raptor_lite"), args.host, args.port)
    print(f"RaPToR-Lite demo available at http://{args.host}:{server.server_address[1]}")
    print("simulation-only — physical robot validation not performed")
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
