import logging
import signal
from threading import Event
from types import FrameType

from app.config import Settings
from app.db import check_database

logger = logging.getLogger(__name__)


def run_worker(settings: Settings, stop_event: Event) -> None:
    logger.info("Worker started")
    while not stop_event.is_set():
        database_ok = check_database(settings.database_url)
        logger.info("Worker heartbeat: db=%s", "ok" if database_ok else "unavailable")
        stop_event.wait(settings.worker_heartbeat_seconds)
    logger.info("Worker stopped")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings()
    stop_event = Event()

    def handle_stop(signum: int, frame: FrameType | None) -> None:
        logger.info("Received signal %s, stopping worker", signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, handle_stop)
    signal.signal(signal.SIGINT, handle_stop)
    run_worker(settings, stop_event)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
