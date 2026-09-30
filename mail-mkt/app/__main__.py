from __future__ import annotations

import argparse

from .factory import create_app


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="mail-mkt")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8001)
    p.add_argument("--debug", action="store_true")
    args = p.parse_args(argv)
    app = create_app()
    print(f"mail-mkt em http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=args.debug)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
