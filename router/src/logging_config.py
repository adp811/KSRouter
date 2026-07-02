import logging
import os
import time
import uuid
from typing import Optional

from pythonjsonlogger import jsonlogger


class RequestIdFilter(logging.Filter):
    """Injects request_id into log records."""
    
    def filter(self, record):
        if not hasattr(record, "request_id"):
            record.request_id = "-"
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
