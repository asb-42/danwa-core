"""Tests for backend.core.logging — structlog configuration."""

from __future__ import annotations

import logging

import structlog

from backend.core.logging import bind_request_context, setup_logging


def test_setup_logging_debug_mode_does_not_raise() -> None:
    setup_logging(debug=True)
    # If we got here, configuration succeeded
    assert True


def test_setup_logging_production_mode_does_not_raise() -> None:
    setup_logging(debug=False)
    assert True


def test_setup_logging_can_be_called_twice() -> None:
    """Idempotent re-configuration must not raise."""
    setup_logging(debug=False)
    setup_logging(debug=True)
    assert True


def test_setup_logging_suppresses_noisy_loggers() -> None:
    setup_logging(debug=True)
    for name in (
        "httpx",
        "httpcore",
        "litellm",
        "python_multipart",
        "chromadb",
        "chromadb.utils.embedding_functions",
        "uvicorn.access",
    ):
        assert logging.getLogger(name).level == logging.WARNING


def test_bind_request_context_with_user() -> None:
    bind_request_context("req-abc", user_id="user-1")
    # If it didn't raise, the binding succeeded
    bound = structlog.contextvars.get_contextvars()
    assert bound.get("request_id") == "req-abc"
    assert bound.get("user_id") == "user-1"


def test_bind_request_context_without_user() -> None:
    bind_request_context("req-xyz")
    bound = structlog.contextvars.get_contextvars()
    assert bound.get("request_id") == "req-xyz"
    assert "user_id" not in bound


def test_bind_request_context_clears_previous() -> None:
    bind_request_context("req-1", user_id="u1")
    bind_request_context("req-2", user_id="u2")
    bound = structlog.contextvars.get_contextvars()
    assert bound["request_id"] == "req-2"
    assert bound["user_id"] == "u2"
