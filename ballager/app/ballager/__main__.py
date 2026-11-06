import logging
import os

from waitress import serve

from . import __version__, db, scheduler
from .web import app


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("BALLAGER_LOGLEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    logging.getLogger("waitress.queue").setLevel(logging.ERROR)
    log = logging.getLogger("ballager")
    log.info("BALLager %s startet – Datenbank: %s", __version__, db.DB_PATH)
    db.init()
    scheduler.start()
    port = int(os.environ.get("BALLAGER_PORT", "8099"))
    serve(app, host="0.0.0.0", port=port, threads=6, ident="BALLager")


if __name__ == "__main__":
    main()
