import contextvars
import logging
import os
import time
import uuid
from typing import Optional

from pythonjsonlogger import jsonlogger

# Per-request-id context variable. Unlike a plain attribute set on the shared
# module-level logger, a ContextVar is safe under concurrent asyncio tasks:
# each incoming request is handled in its own Task, and asyncio copies the
# current context when a Task is created, so concurrent requests never see
# each other's request_id.
request_id_ctx_var: "contextvars.ContextVar[str]" = contextvars.ContextVar(
    "request_id", default="-"
)


class RequestIdFilter(logging.Filter):
    """Injects request_id into log records.

    If a request_id was passed explicitly via `logger.info(..., extra={"request_id": ...})`
    it is left untouched. Otherwise it's filled in from the current request's
    context variable (set by RequestIdMiddleware), falling back to "-" outside
    of a request context.
    """

    def filter(self, record):
        if getattr(record, "request_id", None) is None:
            record.request_id = request_id_ctx_var.get()
        return True


def setup_logging():
    log_handler = logging.StreamHandler()
    formatter = jsonlogger.JsonFormatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s %(request_id)s",
        rename_fields={"asctime": "timestamp", "levelname": "level"}
    )
    log_handler.setFormatter(formatter)
    log_handler.addFilter(RequestIdFilter())
    
    logger = logging.getLogger("router")
    logger.handlers.clear()
    logger.addHandler(log_handler)
    logger.setLevel(logging.INFO)
    return logger


logger = setup_logging()
