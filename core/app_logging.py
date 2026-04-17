import logging
import os
import re
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from logging.handlers import TimedRotatingFileHandler


_LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | sid=%(sid)s | rid=%(rid)s | %(message)s"
_DATE_SUFFIX_REGEX = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_sid_var: ContextVar[str] = ContextVar("log_sid", default="-")
_rid_var: ContextVar[str] = ContextVar("log_rid", default="-")


class _ContextDefaultsFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "sid"):
            record.sid = _sid_var.get()
        if not hasattr(record, "rid"):
            record.rid = _rid_var.get()
        return True


@contextmanager
def log_context(session_id: str | None = None, request_id: str | None = None):
    sid = session_id or "-"
    rid = request_id or "-"
    sid_token = _sid_var.set(sid)
    rid_token = _rid_var.set(rid)
    try:
        yield
    finally:
        _sid_var.reset(sid_token)
        _rid_var.reset(rid_token)


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
    handler.addFilter(_ContextDefaultsFilter())
    return handler


def _build_daily_file_handler_with_level(
    path: str,
    level: int,
) -> TimedRotatingFileHandler:
    handler = _build_daily_file_handler(path)
    handler.setLevel(level)
    return handler


def _build_console_handler() -> logging.StreamHandler:
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    handler.setLevel(logging.INFO)
    handler.addFilter(_ContextDefaultsFilter())
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

    # Keep console readable via handler level, but persist detailed trace in file logs.
    backend_logger.setLevel(logging.DEBUG)
    backend_logger.propagate = False
    frontend_logger.setLevel(logging.INFO)
    frontend_logger.propagate = False

    if not getattr(backend_logger, "_gnerate_log_configured", False):
        # Main backend log: concise operational view (INFO+).
        backend_info_handler = _build_daily_file_handler_with_level(
            os.path.join(log_dir, "backend.log"),
            logging.INFO,
        )
        # Debug backend log: full troubleshooting detail (DEBUG+).
        backend_debug_handler = _build_daily_file_handler_with_level(
            os.path.join(log_dir, "backend.debug.log"),
            logging.DEBUG,
        )
        backend_logger.addHandler(backend_info_handler)
        backend_logger.addHandler(backend_debug_handler)
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
