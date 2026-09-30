"""Background worker: drains queued/sending campaigns via UniOne."""

from __future__ import annotations

import logging
import os
import time

from .config import Config
from .db import SessionLocal, init_db
from .sender import process_next_batch
from .unione import UnioneClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("mailmkt.worker")


def main() -> int:
    init_db()
    client = UnioneClient()
    pause = Config.UNIONE_BATCH_PAUSE_SEC
    idle_sleep = float(os.environ.get("WORKER_IDLE_SLEEP", "3"))
    log.info(
        "worker start base=%s from=%s batch=%s configured=%s",
        Config.UNIONE_BASE_URL,
        Config.UNIONE_FROM_EMAIL or "(missing)",
        Config.UNIONE_BATCH_SIZE,
        client.configured,
    )
    while True:
        try:
            with SessionLocal() as session:
                n = process_next_batch(session, client=client)
                session.commit()
            if n:
                log.info("sent batch size=%s", n)
                time.sleep(pause)
            else:
                time.sleep(idle_sleep)
        except KeyboardInterrupt:
            log.info("worker stop")
            return 0
        except Exception:
            log.exception("worker loop error")
            time.sleep(5)


if __name__ == "__main__":
    raise SystemExit(main())
