"""Shared request-validation helpers for forecast processors."""

from __future__ import annotations

import hmac
from datetime import datetime, timezone
from typing import Any, Callable

UTC = timezone.utc


class ProcessorRequestError(ValueError):
    """Stable processor-facing error that the HTTP layer may expose."""

    def __init__(
        self,
        *,
        code: str,
        category: str,
        message: str,
        status_code: int,
        retryable: bool = False,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.category = category
        self.public_message = message
        self.status_code = status_code
        self.retryable = retryable
        self.request_id = request_id


def verify_digest(request: Any, compute_input_digest: Callable[[Any], str]) -> None:
    if not hmac.compare_digest(compute_input_digest(request), request.input_digest):
        raise ProcessorRequestError(
            code="DIGEST_MISMATCH",
            category="invalid_request",
            message="input_digest does not match the canonical request data",
            status_code=400,
            request_id=request.request_id,
        )


def verify_deadline(
    request: Any,
    parse_utc_timestamp: Callable[[str], datetime],
    *,
    now_fn: Callable[[], datetime] | None = None,
) -> datetime:
    checked_at = now_fn() if now_fn is not None else datetime.now(UTC)
    if checked_at >= parse_utc_timestamp(request.deadline):
        raise ProcessorRequestError(
            code="DEADLINE_EXCEEDED",
            category="timeout",
            message="processing deadline has elapsed",
            status_code=504,
            retryable=True,
            request_id=request.request_id,
        )
    return checked_at


def frame_error(request: Any, message: str) -> None:
    raise ProcessorRequestError(
        code="FRAME_INVALID",
        category="invalid_data",
        message=message,
        status_code=422,
        request_id=request.request_id,
    )


def validate_artifact_match(
    request: Any,
    artifact: Any,
    artifact_model_type: type,
):
    if artifact is None:
        return None
    if request.artifact is not None and (
        artifact.kind != request.artifact.kind
        or artifact.family != request.artifact.family
        or (
            request.artifact.version is not None
            and artifact.version != request.artifact.version
        )
        or (
            request.artifact.artifact_digest is not None
            and artifact.artifact_digest != request.artifact.artifact_digest
        )
    ):
        raise ValueError("engine artifact does not match the selector")
    return artifact_model_type(
        kind=artifact.kind,
        family=artifact.family,
        version=artifact.version,
        artifact_digest=artifact.artifact_digest,
    )
