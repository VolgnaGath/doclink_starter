import logging
import signal
from threading import Event
from types import FrameType

from app.config import Settings
from app.services.jobs import work_once

logger = logging.getLogger(__name__)


def run_worker(settings: Settings, stop_event: Event) -> None:
    logger.info("Worker started")
    while not stop_event.is_set():
        try:
            if work_once(settings):
                continue
        except Exception:
            logger.exception("Worker iteration failed")
        stop_event.wait(min(settings.worker_heartbeat_seconds, 2))
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
