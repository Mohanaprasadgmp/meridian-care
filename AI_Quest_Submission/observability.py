# Original path: src/meridian/observability.py
"""Structured JSON logging with PII-safe fields, plus token cost estimation."""
import json
import logging
import sys

# USD per 1M tokens (input, output) - used for run cost reporting
PRICING = {
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
}

_RESERVED = set(vars(logging.makeLogRecord({})).keys()) | {"message", "asctime"}
_PII_KEYS = {"body", "customer_name", "tenant_message"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {"ts": self.formatTime(record), "level": record.levelname, "event": record.getMessage()}
        for k, v in record.__dict__.items():
            if k not in _RESERVED:
                payload[k] = "[redacted]" if k in _PII_KEYS else v
        return json.dumps(payload, default=str)


def _build_logger() -> logging.Logger:
    logger = logging.getLogger("meridian")
    if not logger.handlers:
        h = logging.StreamHandler(sys.stderr)
        h.setFormatter(JsonFormatter())
        logger.addHandler(h)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


log = _build_logger()


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    # longest-prefix match so dated snapshots (e.g. gpt-4.1-2025-04-14) still price correctly; unknown -> 0
    key = max((k for k in PRICING if model.startswith(k)), key=len, default=None)
    pin, pout = PRICING.get(key, (0.0, 0.0))
    return round(input_tokens / 1e6 * pin + output_tokens / 1e6 * pout, 4)
