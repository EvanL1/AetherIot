from __future__ import annotations

from typing import Any

import pytest

from forecast_runtime_core import (
    EngineUnavailable,
    ForecastContext,
    RemoteHttpBackendConfig,
    RemoteHttpForecastBackend,
)


class RecordingRemoteTransport:
    def __init__(
        self,
        *,
        health_result: dict[str, Any] | None = None,
        forecast_result: dict[str, Any] | None = None,
        raise_on_post: bool = False,
    ) -> None:
        self.health_result = health_result or {"ready": True}
        self.forecast_result = forecast_result or {}
        self.raise_on_post = raise_on_post
        self.get_calls: list[tuple[str, dict[str, str], float]] = []
        self.post_calls: list[tuple[str, dict[str, Any], dict[str, str], float]] = []

    def get_json(self, *, url: str, headers: dict[str, str], timeout_seconds: float):
        self.get_calls.append((url, headers, timeout_seconds))
        return self.health_result

    def post_json(
        self,
        *,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        timeout_seconds: float,
    ):
        self.post_calls.append((url, payload, headers, timeout_seconds))
        if self.raise_on_post:
            raise RuntimeError("hidden transport detail")
        return self.forecast_result


def context(*, quantiles: tuple[float, ...] = ()) -> ForecastContext:
    return ForecastContext(
        request_id="0190aee6-2139-7a87-8448-806f1b843201",
        binding_id="site-a",
        as_of="2099-07-11T12:00:00Z",
        cadence_seconds=900,
        horizon_steps=2,
        artifact_kind="model",
        artifact_family="site-load",
        artifact_version="v3",
        quantiles=quantiles,
    )


def test_remote_http_backend_reports_health_and_posts_governed_payload() -> None:
    transport = RecordingRemoteTransport(
        forecast_result={
            "artifact": {
                "kind": "model",
                "family": "site-load",
                "version": "v3",
                "artifact_digest": "sha256:" + "a" * 64,
            },
            "predictions": [
                {"timestamp": "2099-07-11T12:15:00Z", "value": 836.0},
                {"timestamp": "2099-07-11T12:30:00Z", "value": 837.0},
            ],
        }
    )
    backend = RemoteHttpForecastBackend(
        RemoteHttpBackendConfig(
            base_url="https://forecast.example",
            api_token="secret-token",
            verify_health_before_forecast=True,
        ),
        transport=transport,
    )

    assert backend.is_ready() is True
    result = backend.forecast(
        history_data=[{"datetime": "2099-07-11T11:45:00Z", "load": 835.0}],
        forecast_data=[
            {"datetime": "2099-07-11T12:15:00Z", "quarter_hour": 49},
            {"datetime": "2099-07-11T12:30:00Z", "quarter_hour": 50},
        ],
        context=context(),
    )

    assert [point.value for point in result.points] == [836.0, 837.0]
    assert result.artifact is not None
    assert result.artifact.artifact_digest == "sha256:" + "a" * 64
    assert transport.get_calls[0][0] == "https://forecast.example/v1/health"
    url, payload, headers, timeout_seconds = transport.post_calls[0]
    assert url == "https://forecast.example/v1/forecast"
    assert headers["Authorization"] == "Bearer secret-token"
    assert timeout_seconds == 10.0
    assert payload["binding_id"] == "site-a"
    assert payload["artifact"]["version"] == "v3"
    assert len(payload["forecast_data"]) == 2


def test_remote_http_backend_accepts_requested_quantiles() -> None:
    transport = RecordingRemoteTransport(
        forecast_result={
            "artifact": {
                "kind": "model",
                "family": "site-load",
                "version": "v3",
                "artifact_digest": "sha256:" + "b" * 64,
            },
            "predictions": [
                {
                    "timestamp": "2099-07-11T12:15:00Z",
                    "value": 836.0,
                    "quantiles": [
                        {"probability": 0.1, "value": 830.0},
                        {"probability": 0.9, "value": 840.0},
                    ],
                },
                {
                    "timestamp": "2099-07-11T12:30:00Z",
                    "value": 837.0,
                    "quantiles": [
                        {"probability": 0.1, "value": 831.0},
                        {"probability": 0.9, "value": 841.0},
                    ],
                },
            ],
        }
    )
    backend = RemoteHttpForecastBackend(
        RemoteHttpBackendConfig(base_url="https://forecast.example"),
        transport=transport,
    )

    result = backend.forecast(
        history_data=[],
        forecast_data=[{"datetime": "2099-07-11T12:15:00Z"}, {"datetime": "2099-07-11T12:30:00Z"}],
        context=context(quantiles=(0.1, 0.9)),
    )

    assert result.points[0].quantiles[0].probability == 0.1
    assert result.points[1].quantiles[1].value == 841.0


def test_remote_http_backend_fails_closed_on_bad_quantiles_or_artifact() -> None:
    bad_quantiles = RecordingRemoteTransport(
        forecast_result={
            "predictions": [
                {
                    "timestamp": "2099-07-11T12:15:00Z",
                    "value": 836.0,
                    "quantiles": [{"probability": 0.2, "value": 830.0}],
                }
            ]
        }
    )
    backend = RemoteHttpForecastBackend(
        RemoteHttpBackendConfig(base_url="https://forecast.example"),
        transport=bad_quantiles,
    )
    with pytest.raises(EngineUnavailable, match="quantiles do not match"):
        backend.forecast(
            history_data=[],
            forecast_data=[{"datetime": "2099-07-11T12:15:00Z"}],
            context=context(quantiles=(0.1,)),
        )

    bad_artifact = RecordingRemoteTransport(
        forecast_result={
            "artifact": {
                "kind": "model",
                "family": "site-load",
                "version": "v2",
                "artifact_digest": "sha256:" + "c" * 64,
            },
            "predictions": [{"timestamp": "2099-07-11T12:15:00Z", "value": 836.0}],
        }
    )
    backend = RemoteHttpForecastBackend(
        RemoteHttpBackendConfig(base_url="https://forecast.example"),
        transport=bad_artifact,
    )
    with pytest.raises(EngineUnavailable, match="artifact version does not match"):
        backend.forecast(
            history_data=[],
            forecast_data=[{"datetime": "2099-07-11T12:15:00Z"}],
            context=context(),
        )


def test_remote_http_backend_fails_closed_when_unready_or_transport_fails() -> None:
    unready = RemoteHttpForecastBackend(
        RemoteHttpBackendConfig(
            base_url="https://forecast.example",
            verify_health_before_forecast=True,
        ),
        transport=RecordingRemoteTransport(health_result={"ready": False}),
    )
    assert unready.is_ready() is False
    with pytest.raises(EngineUnavailable, match="not ready"):
        unready.forecast(
            history_data=[],
            forecast_data=[{"datetime": "2099-07-11T12:15:00Z"}],
            context=context(),
        )

    failing = RemoteHttpForecastBackend(
        RemoteHttpBackendConfig(base_url="https://forecast.example"),
        transport=RecordingRemoteTransport(raise_on_post=True),
    )
    with pytest.raises(EngineUnavailable, match="unavailable"):
        failing.forecast(
            history_data=[],
            forecast_data=[{"datetime": "2099-07-11T12:15:00Z"}],
            context=context(),
        )
