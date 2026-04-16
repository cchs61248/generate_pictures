import logging
import os
import re
import sys
from logging.handlers import TimedRotatingFileHandler


_LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
_DATE_SUFFIX_REGEX = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _build_daily_file_handler(path: str) -> TimedRotatingFileHandler:
    handler = TimedRotatingFileHandler(
        filename=path,
        when="midnight",
        interval=1,
        backupCount=29,  # active file + 29 backups = at most 30 days
        encoding="utf-8",
    )
    handler.suffix = "%Y-%m-%d"
    handler.extMatch = _DATE_SUFFIX_REGEX
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    return handler


def _build_console_handler() -> logging.StreamHandler:
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    handler.setLevel(logging.INFO)
    return handler


def setup_app_logging(project_root: str) -> None:
    log_dir = os.path.join(project_root, "log")
    os.makedirs(log_dir, exist_ok=True)

    backend_logger = logging.getLogger("app.backend")
    frontend_logger = logging.getLogger("app.frontend")

    if getattr(backend_logger, "_gnerate_log_configured", False) and getattr(
        frontend_logger, "_gnerate_log_configured", False
    ):
        return

    backend_logger.setLevel(logging.INFO)
    backend_logger.propagate = False
    frontend_logger.setLevel(logging.INFO)
    frontend_logger.propagate = False

    if not getattr(backend_logger, "_gnerate_log_configured", False):
        backend_handler = _build_daily_file_handler(os.path.join(log_dir, "backend.log"))
        backend_logger.addHandler(backend_handler)
        backend_logger.addHandler(_build_console_handler())
        backend_logger._gnerate_log_configured = True

    if not getattr(frontend_logger, "_gnerate_log_configured", False):
        frontend_handler = _build_daily_file_handler(
            os.path.join(log_dir, "frontend.log")
        )
        frontend_logger.addHandler(frontend_handler)
        frontend_logger.addHandler(_build_console_handler())
        frontend_logger._gnerate_log_configured = True


def get_backend_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"app.backend.{name}")


def get_frontend_logger(name: str = "client") -> logging.Logger:
    return logging.getLogger(f"app.frontend.{name}")
