import logging
import re
from collections.abc import Mapping, MutableMapping
from typing import Any, cast

import structlog

SENSITIVE_KEY_PATTERN = re.compile(r"(?:password|token|secret|cookie|authorization)", re.I)
SENSITIVE_URL_PARAMETER_PATTERN = re.compile(
    r"([?&](?:[^&=]*(?:password|token|secret|cookie|authorization)[^&=]*)=)[^&]*", re.I
)


def redact(value: Any, key: str | None = None) -> Any:
    if key and SENSITIVE_KEY_PATTERN.search(key):
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return {str(k): redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return SENSITIVE_URL_PARAMETER_PATTERN.sub(r"\1[REDACTED]", value)
    return value


def redact_processor(_: Any, __: str, event_dict: MutableMapping[str, Any]) -> Mapping[str, Any]:
    return cast(dict[str, Any], redact(dict(event_dict)))


class RedactingLogFilter(logging.Filter):
    """Protect framework access logs too; Uvicorn otherwise logs token queries."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.msg)
        if isinstance(record.args, Mapping):
            record.args = redact(record.args)
        elif isinstance(record.args, tuple):
            record.args = tuple(redact(value) for value in record.args)
        return True


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    redactor = RedactingLogFilter()
    for logger_name in ("", "uvicorn.access", "uvicorn.error"):
        logger = logging.getLogger(logger_name)
        logger.addFilter(redactor)
        for handler in logger.handlers:
            handler.addFilter(redactor)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            redact_processor,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
