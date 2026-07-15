from __future__ import annotations

import pytest

from forecast_runtime_core import (
    ArtifactProvenance,
    ChronosRemoteExecutor,
    ChronosRemoteExecutorConfig,
    EngineForecast,
    EngineForecastPoint,
    EngineUnavailable,
    ForecastContext,
    FoundationModelBackendConfig,
    FoundationModelForecastBackend,
)


class ReadyExecutor:
    def is_ready(self) -> bool:
        return True

    def forecast(
        self,
        *,
        history_data,
        forecast_data,
        context,
        model_family,
        model_name,
    ) -> EngineForecast:
        return EngineForecast(
            points=tuple(
                EngineForecastPoint(timestamp=row["datetime"], value=100.0 + index)
                for index, row in enumerate(forecast_data)
            ),
            artifact=ArtifactProvenance(
                kind="model",
                family="site-load",
                version="v3",
                artifact_digest="sha256:" + "f" * 64,
            ),
        )


class FailingExecutor:
    def is_ready(self) -> bool:
        raise RuntimeError("hidden detail")

    def forecast(self, **kwargs) -> EngineForecast:
        raise RuntimeError("hidden detail")


class RecordingTransport:
    def __init__(self, *, health_result=None, forecast_result=None) -> None:
        self.health_result = health_result or {"ready": True, "model_family": "chronos", "model_name": "chronos-tiny"}
        self.forecast_result = forecast_result or {}
        self.get_calls = []
        self.post_calls = []

    def get_json(self, *, url, headers, timeout_seconds):
        self.get_calls.append((url, headers, timeout_seconds))
        return self.health_result

    def post_json(self, *, url, payload, headers, timeout_seconds):
        self.post_calls.append((url, payload, headers, timeout_seconds))
        return self.forecast_result


def context() -> ForecastContext:
    return ForecastContext(
        request_id="0190aee6-2139-7a87-8448-806f1b843201",
        binding_id="site-a",
        as_of="2099-07-11T12:00:00Z",
        cadence_seconds=900,
        horizon_steps=2,
        artifact_kind="model",
        artifact_family="site-load",
        artifact_version="v3",
        quantiles=(),
    )


def test_foundation_backend_reports_descriptor_and_forecast() -> None:
    backend = FoundationModelForecastBackend(
        FoundationModelBackendConfig(
            backend_id="chronos-sample",
            model_family="chronos",
            model_name="chronos-tiny",
        ),
        executor=ReadyExecutor(),
    )

    result = backend.forecast(
        history_data=[{"datetime": "2099-07-11T11:45:00Z", "load": 1.0}],
        forecast_data=[
            {"datetime": "2099-07-11T12:15:00Z"},
            {"datetime": "2099-07-11T12:30:00Z"},
        ],
        context=context(),
    )

    assert backend.is_ready() is True
    assert backend.descriptor().backend_kind == "foundation-model"
    assert result.points[0].value == 100.0


def test_foundation_backend_fails_closed_on_executor_errors() -> None:
    backend = FoundationModelForecastBackend(
        FoundationModelBackendConfig(
            backend_id="chronos-sample",
            model_family="chronos",
            model_name="chronos-tiny",
        ),
        executor=FailingExecutor(),
    )

    assert backend.is_ready() is False
    with pytest.raises(EngineUnavailable, match="execution failed"):
        backend.forecast(
            history_data=[{"datetime": "2099-07-11T11:45:00Z", "load": 1.0}],
            forecast_data=[{"datetime": "2099-07-11T12:15:00Z"}],
            context=ForecastContext(
                request_id="0190aee6-2139-7a87-8448-806f1b843201",
                binding_id="site-a",
                as_of="2099-07-11T12:00:00Z",
                cadence_seconds=900,
                horizon_steps=1,
                artifact_kind="model",
                artifact_family="site-load",
                artifact_version="v3",
                quantiles=(),
            ),
        )


def test_chronos_remote_executor_health_and_forecast() -> None:
    transport = RecordingTransport(
        forecast_result={
            "artifact": {
                "kind": "model",
                "family": "site-load",
                "version": "v3",
                "artifact_digest": "sha256:" + "1" * 64,
            },
            "predictions": [
                {"timestamp": "2099-07-11T12:15:00Z", "value": 111.0},
                {"timestamp": "2099-07-11T12:30:00Z", "value": 112.0},
            ],
        }
    )
    executor = ChronosRemoteExecutor(
        ChronosRemoteExecutorConfig(
            base_url="https://chronos.example",
            api_token="token-123",
        ),
        transport=transport,
    )

    result = executor.forecast(
        history_data=[{"datetime": "2099-07-11T11:45:00Z", "load": 1.0}],
        forecast_data=[
            {"datetime": "2099-07-11T12:15:00Z"},
            {"datetime": "2099-07-11T12:30:00Z"},
        ],
        context=context(),
        model_family="chronos",
        model_name="chronos-tiny",
    )

    assert executor.is_ready() is True
    assert result.points[1].value == 112.0
    assert transport.get_calls[0][0] == "https://chronos.example/v1/foundation/health"
    assert transport.post_calls[0][2]["Authorization"] == "Bearer token-123"
    assert transport.post_calls[0][1]["model_name"] == "chronos-tiny"


def test_chronos_remote_executor_fails_closed_on_bad_response() -> None:
    executor = ChronosRemoteExecutor(
        ChronosRemoteExecutorConfig(base_url="https://chronos.example"),
        transport=RecordingTransport(forecast_result={"predictions": ["bad"]}),
    )

    with pytest.raises(EngineUnavailable, match="prediction is invalid"):
        executor.forecast(
            history_data=[{"datetime": "2099-07-11T11:45:00Z", "load": 1.0}],
            forecast_data=[{"datetime": "2099-07-11T12:15:00Z"}],
            context=ForecastContext(
                request_id="0190aee6-2139-7a87-8448-806f1b843201",
                binding_id="site-a",
                as_of="2099-07-11T12:00:00Z",
                cadence_seconds=900,
                horizon_steps=1,
                artifact_kind="model",
                artifact_family="site-load",
                artifact_version="v3",
                quantiles=(),
            ),
            model_family="chronos",
            model_name="chronos-tiny",
        )
