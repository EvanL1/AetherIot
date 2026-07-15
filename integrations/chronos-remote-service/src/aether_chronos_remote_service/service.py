"""FastAPI app for the Chronos-style remote forecast skeleton."""

from __future__ import annotations

from fastapi import FastAPI, Header, HTTPException

from .config import ServiceConfig
from .executor import ChronosExecutor, create_executor_from_config
from .models import (
    ErrorResponseModel,
    ForecastRequestModel,
    ForecastResponseModel,
    HealthResponseModel,
)


def create_app(
    *,
    config: ServiceConfig,
    executor: ChronosExecutor | None = None,
) -> FastAPI:
    app = FastAPI(title="Aether Chronos Remote Service")
    active_executor = executor or create_executor_from_config(config)

    def authorize(authorization: str | None) -> None:
        if config.token is None:
            return
        if authorization != f"Bearer {config.token}":
            raise HTTPException(status_code=401, detail="unauthorized")

    @app.get("/v1/foundation/health", response_model=HealthResponseModel)
    def health(authorization: str | None = Header(default=None)) -> HealthResponseModel:
        authorize(authorization)
        ready = active_executor.is_ready() is True
        return HealthResponseModel(
            ready=ready,
            model_family=config.model_family,
            model_name=config.model_name,
        )

    @app.post(
        "/v1/foundation/forecast",
        response_model=ForecastResponseModel,
        responses={400: {"model": ErrorResponseModel}, 401: {"model": ErrorResponseModel}},
    )
    def forecast(
        request: ForecastRequestModel,
        authorization: str | None = Header(default=None),
    ) -> ForecastResponseModel:
        authorize(authorization)
        if request.model_family != config.model_family:
            raise HTTPException(status_code=400, detail="model_family does not match service")
        if request.model_name != config.model_name:
            raise HTTPException(status_code=400, detail="model_name does not match service")
        if len(request.forecast_data) != request.horizon_steps:
            raise HTTPException(status_code=400, detail="forecast_data length does not match horizon_steps")
        try:
            return active_executor.forecast(
                request=request,
                artifact_digest=config.artifact_digest,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return app
