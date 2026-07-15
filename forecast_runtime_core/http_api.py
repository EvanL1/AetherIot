"""Reusable FastAPI transport shell for forecast processors."""

from __future__ import annotations

import asyncio
import hmac
import os
import re
import threading
from collections.abc import Awaitable, Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, TypeVar

from fastapi import APIRouter, FastAPI
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.requests import Request
from starlette.types import Message, Receive, Scope, Send

DEFAULT_MAX_REQUEST_BYTES = 1_048_576
MEDIA_TYPE = "application/vnd.aether.data-processing+json;version=1"
_BEARER_TOKEN = re.compile(r"^[A-Za-z0-9._~+/=-]{1,8192}$")

_T = TypeVar("_T")


class DataProcessingJSONResponse(JSONResponse):
    """Versioned JSON response shared with the Rust HTTP adapter."""

    media_type = MEDIA_TYPE


@dataclass(frozen=True, slots=True)
class RuntimeTransportConfig:
    token_env: str
    require_auth_env: str
    max_concurrency_env: str
    thread_name_prefix: str
    state_attr: str
    duplicate_install_message: str
    app_title: str
    app_version: str = "0.1.0"
    processor_contract: str = "aether.data-processing.forecast.v1"


def _strict_environment_bool(name: str, *, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise ValueError(f"{name} must be either true or false")


@dataclass(frozen=True, slots=True)
class BearerAuthPolicy:
    """Optional bearer authentication with an explicit production fail-closed switch."""

    token: str | None = None
    required: bool = False

    def __post_init__(self) -> None:
        token = self.token
        if token is not None and (len(token) < 32 or not _BEARER_TOKEN.fullmatch(token)):
            raise ValueError("configured bearer token is invalid")
        if self.required and token is None:
            raise ValueError("a bearer token is required but not configured")

    @classmethod
    def from_env(cls, config: RuntimeTransportConfig) -> BearerAuthPolicy:
        token = os.getenv(config.token_env) or None
        return cls(
            token=token,
            required=_strict_environment_bool(config.require_auth_env, default=False),
        )

    @property
    def enabled(self) -> bool:
        return self.token is not None

    def authorizes(self, authorization: bytes | None) -> bool:
        if self.token is None:
            return True
        if authorization is None:
            return False
        try:
            header = authorization.decode("ascii")
        except UnicodeDecodeError:
            return False
        scheme, separator, credential = header.partition(" ")
        if not separator or scheme.lower() != "bearer" or not credential or " " in credential:
            return False
        return hmac.compare_digest(credential, self.token)


def _error_response(
    *,
    error_model: type,
    status_code: int,
    code: str,
    category: str,
    message: str,
    retryable: bool,
    request_id: str | None = None,
    headers: dict[str, str] | None = None,
) -> DataProcessingJSONResponse:
    error = error_model(
        request_id=request_id,
        code=code,
        category=category,
        message=message,
        retryable=retryable,
    )
    return DataProcessingJSONResponse(
        status_code=status_code,
        content=error.model_dump(exclude_none=True),
        headers=headers,
    )


class BearerAuthMiddleware:
    """Authenticate the processing endpoint before reading its request body."""

    def __init__(
        self,
        app: Callable[..., Awaitable[None]],
        *,
        policy: BearerAuthPolicy,
        error_model: type,
    ) -> None:
        self.app = app
        self.policy = policy
        self.error_model = error_model

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("path") == "/v1/process" and self.policy.enabled:
            headers = {key.lower(): value for key, value in scope.get("headers", [])}
            if not self.policy.authorizes(headers.get(b"authorization")):
                response = _error_response(
                    error_model=self.error_model,
                    status_code=401,
                    code="AUTHENTICATION_REQUIRED",
                    category="authorization",
                    message="valid bearer authentication is required",
                    retryable=False,
                    headers={"WWW-Authenticate": "Bearer"},
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


class ProcessorBusy(RuntimeError):
    """All commissioned processor execution slots remain occupied."""


class ProcessorRunner:
    """Bound synchronous model work without releasing slots on HTTP cancellation."""

    def __init__(self, max_concurrency: int = 1, *, thread_name_prefix: str) -> None:
        if max_concurrency <= 0 or max_concurrency > 256:
            raise ValueError("max_concurrency must be inside [1, 256]")
        self._slots = threading.BoundedSemaphore(max_concurrency)
        self._executor = ThreadPoolExecutor(
            max_workers=max_concurrency,
            thread_name_prefix=thread_name_prefix,
        )
        self._state_lock = threading.Lock()
        self._closed = False

    async def run(self, function: Callable[..., _T], *args: Any) -> _T:
        if not self._slots.acquire(blocking=False):
            raise ProcessorBusy
        with self._state_lock:
            if self._closed:
                self._slots.release()
                raise RuntimeError("processor runner is closed")
            try:
                future = self._executor.submit(function, *args)
            except Exception:
                self._slots.release()
                raise
        future.add_done_callback(self._release_slot)
        wrapped = asyncio.wrap_future(future)
        try:
            return await asyncio.shield(wrapped)
        except asyncio.CancelledError:
            wrapped.add_done_callback(self._consume_background_result)
            raise

    def close(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
        self._executor.shutdown(wait=False, cancel_futures=False)

    def _release_slot(self, _future: Future[Any]) -> None:
        self._slots.release()

    @staticmethod
    def _consume_background_result(future: asyncio.Future[Any]) -> None:
        if not future.cancelled():
            future.exception()


def _configured_max_concurrency(config: RuntimeTransportConfig) -> int:
    raw = os.getenv(config.max_concurrency_env, "1")
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{config.max_concurrency_env} must be an integer") from exc
    if value <= 0 or value > 256:
        raise ValueError(f"{config.max_concurrency_env} must be inside [1, 256]")
    return value


class RequestSizeLimitMiddleware:
    """Bound `/v1/process` before JSON parsing, with or without Content-Length."""

    def __init__(self, app: Callable[..., Awaitable[None]], *, max_bytes: int, error_model: type) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self.app = app
        self.max_bytes = max_bytes
        self.error_model = error_model

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") != "/v1/process":
            await self.app(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        raw_length = headers.get(b"content-length")
        if raw_length is not None:
            try:
                content_length = int(raw_length)
            except ValueError:
                await _error_response(
                    error_model=self.error_model,
                    status_code=400,
                    code="CONTENT_LENGTH_INVALID",
                    category="invalid_request",
                    message="content-length is invalid",
                    retryable=False,
                )(scope, receive, send)
                return
            if content_length < 0 or content_length > self.max_bytes:
                await self._too_large(scope, receive, send)
                return

        body = bytearray()
        while True:
            inbound = await receive()
            if inbound["type"] == "http.disconnect":
                return
            if inbound["type"] != "http.request":
                continue
            body.extend(inbound.get("body", b""))
            if len(body) > self.max_bytes:
                await self._too_large(scope, receive, send)
                return
            if not inbound.get("more_body", False):
                break

        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if replayed:
                return {"type": "http.disconnect"}
            replayed = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.app(scope, replay, send)

    async def _too_large(self, scope: Scope, receive: Receive, send: Send) -> None:
        response = _error_response(
            error_model=self.error_model,
            status_code=413,
            code="FRAME_TOO_LARGE",
            category="resource_limit",
            message="request body exceeds configured limit",
            retryable=False,
        )
        await response(scope, receive, send)


def _health_payload(processor: Any, config: RuntimeTransportConfig) -> dict[str, str]:
    return {
        "status": "ok" if processor.is_ready() else "unavailable",
        "processor": processor.policy.processor_id,
        "version": processor.policy.processor_version,
        "contract": config.processor_contract,
    }


def create_router(
    *,
    processor: Any,
    request_model: type,
    response_model: type,
    error_model: type,
    processor_request_error_type: type,
    config: RuntimeTransportConfig,
    runner: ProcessorRunner | None = None,
) -> APIRouter:
    router = APIRouter()
    processor_runner = runner or ProcessorRunner(
        thread_name_prefix=config.thread_name_prefix,
    )

    @router.post(
        "/v1/process",
        response_model=response_model,
        response_model_exclude_none=True,
        response_class=DataProcessingJSONResponse,
    )
    async def process(request: Request) -> DataProcessingJSONResponse:
        content_type = request.headers.get("content-type", "").lower()
        if (
            content_type != MEDIA_TYPE
            and content_type.split(";", 1)[0].strip() != "application/json"
        ):
            return _error_response(
                error_model=error_model,
                status_code=415,
                code="MEDIA_TYPE_UNSUPPORTED",
                category="invalid_request",
                message="request content type is not supported",
                retryable=False,
            )
        try:
            typed_request = request_model.model_validate_json(await request.body())
            result = await processor_runner.run(processor.process, typed_request)
            return DataProcessingJSONResponse(
                content=result.model_dump(mode="json", by_alias=True, exclude_none=True)
            )
        except ProcessorBusy:
            return _error_response(
                error_model=error_model,
                status_code=429,
                code="PROCESSOR_BUSY",
                category="capacity",
                message="processor concurrency limit is occupied",
                retryable=True,
                request_id=(typed_request.request_id if "typed_request" in locals() else None),
                headers={"Retry-After": "1"},
            )
        except processor_request_error_type as exc:
            return _error_response(
                error_model=error_model,
                status_code=exc.status_code,
                code=exc.code,
                category=exc.category,
                message=exc.public_message,
                retryable=exc.retryable,
                request_id=exc.request_id,
            )
        except ValidationError:
            return _error_response(
                error_model=error_model,
                status_code=422,
                code="FRAME_INVALID",
                category="invalid_data",
                message="request does not satisfy the Data Processing v1 contract",
                retryable=False,
            )
        except Exception:
            return _error_response(
                error_model=error_model,
                status_code=500,
                code="PROCESSOR_INTERNAL",
                category="internal",
                message="processor encountered an internal error",
                retryable=False,
            )

    @router.get("/v1/health")
    async def versioned_health() -> dict[str, str]:
        return _health_payload(processor, config)

    return router


def install_routes(
    app: FastAPI,
    *,
    processor: Any,
    request_model: type,
    response_model: type,
    error_model: type,
    processor_request_error_type: type,
    config: RuntimeTransportConfig,
    max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES,
    max_processor_concurrency: int | None = None,
    auth_policy: BearerAuthPolicy | None = None,
    include_health_alias: bool = False,
) -> None:
    if hasattr(app.state, config.state_attr):
        raise ValueError(config.duplicate_install_message)
    runner = ProcessorRunner(
        max_concurrency=(
            _configured_max_concurrency(config)
            if max_processor_concurrency is None
            else max_processor_concurrency
        ),
        thread_name_prefix=config.thread_name_prefix,
    )
    policy = auth_policy or BearerAuthPolicy.from_env(config)
    setattr(app.state, config.state_attr, runner)
    app.add_middleware(RequestSizeLimitMiddleware, max_bytes=max_request_bytes, error_model=error_model)
    app.add_middleware(BearerAuthMiddleware, policy=policy, error_model=error_model)
    app.include_router(
        create_router(
            processor=processor,
            request_model=request_model,
            response_model=response_model,
            error_model=error_model,
            processor_request_error_type=processor_request_error_type,
            config=config,
            runner=runner,
        )
    )
    app.router.add_event_handler("shutdown", runner.close)
    if include_health_alias:
        app.add_api_route("/health", lambda: _health_payload(processor, config), methods=["GET"])


def create_app(
    *,
    processor: Any,
    request_model: type,
    response_model: type,
    error_model: type,
    processor_request_error_type: type,
    config: RuntimeTransportConfig,
    max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES,
    max_processor_concurrency: int | None = None,
    auth_policy: BearerAuthPolicy | None = None,
) -> FastAPI:
    app = FastAPI(title=config.app_title, version=config.app_version)
    install_routes(
        app,
        processor=processor,
        request_model=request_model,
        response_model=response_model,
        error_model=error_model,
        processor_request_error_type=processor_request_error_type,
        config=config,
        max_request_bytes=max_request_bytes,
        max_processor_concurrency=max_processor_concurrency,
        auth_policy=auth_policy,
        include_health_alias=True,
    )
    return app
