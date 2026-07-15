"""FastAPI transport for the request-driven processor."""

from __future__ import annotations

import hmac
import os
import re

from forecast_runtime_core import (
    DEFAULT_MAX_REQUEST_BYTES,
    MEDIA_TYPE,
    DataProcessingJSONResponse,
    ProcessorBusy,
    ProcessorRunner as SharedProcessorRunner,
    RuntimeTransportConfig,
    create_app as create_runtime_app,
    create_router as create_runtime_router,
    install_routes as install_runtime_routes,
)

from .models import DataProcessingRequest, ProcessingError, ProcessingResult
from .processor import ProcessorRequestError, PvForecastProcessor

_BEARER_TOKEN = re.compile(r"^[A-Za-z0-9._~+/=-]{1,8192}$")

_CONFIG = RuntimeTransportConfig(
    token_env="AETHER_PV_FORECASTING_BEARER_TOKEN",
    require_auth_env="AETHER_PV_FORECASTING_REQUIRE_AUTH",
    max_concurrency_env="AETHER_PV_FORECASTING_MAX_CONCURRENCY",
    thread_name_prefix="aether-pv-forecast",
    state_attr="aether_pv_forecasting_runner",
    duplicate_install_message="Aether PV forecasting routes are already installed",
    app_title="Aether PV-Forecasting Processor",
)


class BearerAuthPolicy:
    """Local compatibility wrapper over the shared runtime auth contract."""

    def __init__(self, token: str | None = None, required: bool = False) -> None:
        self.token = token
        self.required = required
        if token is not None and (len(token) < 32 or not _BEARER_TOKEN.fullmatch(token)):
            raise ValueError("configured bearer token is invalid")
        if required and token is None:
            raise ValueError("a bearer token is required but not configured")

    @classmethod
    def from_env(cls) -> BearerAuthPolicy:
        token = os.getenv(_CONFIG.token_env) or None
        raw = os.getenv(_CONFIG.require_auth_env)
        if raw is None:
            required = False
        else:
            normalized = raw.strip().lower()
            if normalized == "true":
                required = True
            elif normalized == "false":
                required = False
            else:
                raise ValueError(f"{_CONFIG.require_auth_env} must be either true or false")
        return cls(token=token, required=required)

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


class ProcessorRunner(SharedProcessorRunner):
    def __init__(self, max_concurrency: int = 1) -> None:
        super().__init__(max_concurrency=max_concurrency, thread_name_prefix=_CONFIG.thread_name_prefix)


def create_router(
    processor: PvForecastProcessor,
    *,
    runner: ProcessorRunner | None = None,
):
    return create_runtime_router(
        processor=processor,
        request_model=DataProcessingRequest,
        response_model=ProcessingResult,
        error_model=ProcessingError,
        processor_request_error_type=ProcessorRequestError,
        config=_CONFIG,
        runner=runner,
    )


def install_routes(
    app,
    *,
    processor: PvForecastProcessor,
    max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES,
    max_processor_concurrency: int | None = None,
    auth_policy: BearerAuthPolicy | None = None,
    include_health_alias: bool = False,
) -> None:
    install_runtime_routes(
        app,
        processor=processor,
        request_model=DataProcessingRequest,
        response_model=ProcessingResult,
        error_model=ProcessingError,
        processor_request_error_type=ProcessorRequestError,
        config=_CONFIG,
        max_request_bytes=max_request_bytes,
        max_processor_concurrency=max_processor_concurrency,
        auth_policy=auth_policy,
        include_health_alias=include_health_alias,
    )


def create_app(
    *,
    processor: PvForecastProcessor,
    max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES,
    max_processor_concurrency: int | None = None,
    auth_policy: BearerAuthPolicy | None = None,
):
    return create_runtime_app(
        processor=processor,
        request_model=DataProcessingRequest,
        response_model=ProcessingResult,
        error_model=ProcessingError,
        processor_request_error_type=ProcessorRequestError,
        config=_CONFIG,
        max_request_bytes=max_request_bytes,
        max_processor_concurrency=max_processor_concurrency,
        auth_policy=auth_policy,
    )
