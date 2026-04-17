import logging
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

def setup_logging() -> None:
    LOG_DIR = BASE_DIR / "gemini_logs"
    LOG_DIR.mkdir(exist_ok=True)
    logger = logging.getLogger("gemini_service")
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    file_handler = logging.FileHandler(LOG_DIR / "gemini_service.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    if not logger.hasHandlers():
        logger.addHandler(file_handler)
    else:
        logger.handlers.clear()
        logger.addHandler(file_handler)
