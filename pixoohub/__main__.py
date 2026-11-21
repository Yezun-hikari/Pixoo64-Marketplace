"""python -m pixoohub [--host 0.0.0.0] [--port 8000]"""

import argparse
import logging
import os
import sys

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(prog="pixoohub")
    parser.add_argument("--host", default=os.environ.get("PIXOOHUB_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PIXOOHUB_PORT", "8000")))
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    from .web import create_app

    uvicorn.run(create_app(), host=args.host, port=args.port, access_log=False, log_level="warning")


if __name__ == "__main__":
    main()
