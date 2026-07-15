from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from aether_pv_forecasting_processor.api import BearerAuthPolicy, create_app
from aether_pv_forecasting_processor.engine import (
    ArtifactProvenance,
    EngineForecast,
    EngineForecastPoint,
)
from aether_pv_forecasting_processor.processor import ProcessorPolicy, PvForecastProcessor

from conftest import digest_for


class FakeEngine:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def forecast(self, *, history_data, forecast_data, context) -> EngineForecast:
        self.calls.append(
            {
                "history_data": history_data,
                "forecast_data": forecast_data,
                "context": context,
            }
        )
        return EngineForecast(
            points=tuple(
                EngineForecastPoint(timestamp=row["datetime"], value=500.0 + index)
                for index, row in enumerate(forecast_data, start=1)
            ),
            artifact=ArtifactProvenance(
                kind="model",
                family="site-pv",
                version="v3",
                artifact_digest="sha256:" + "a" * 64,
            ),
        )


class FailingEngine:
    def forecast(self, *, history_data, forecast_data, context) -> EngineForecast:
        raise RuntimeError("inference failed")


def client_for(engine, *, allow_fallback: bool = False, auth: BearerAuthPolicy | None = None):
    processor = PvForecastProcessor(
        engine=engine,
        policy=ProcessorPolicy(
            history_steps=2,
            max_horizon_steps=2,
            allow_persistence_fallback=allow_fallback,
        ),
    )
    return TestClient(create_app(processor=processor, auth_policy=auth), raise_server_exceptions=False)


def test_processes_complete_pv_frame_end_to_end(valid_payload: dict[str, Any]) -> None:
    engine = FakeEngine()

    response = client_for(engine).post("/v1/process", json=valid_payload)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "produced"
    assert body["processor"]["id"] == "pv-forecasting-edge"
    assert body["output"]["target"] == "pv"
    assert body["output"]["sign_convention"] == "positive_generation"
    assert [point["value"] for point in body["output"]["points"]] == [501.0, 502.0]
    assert engine.calls[0]["history_data"][0]["pv"] == 420.0
    assert engine.calls[0]["forecast_data"][0]["pv"] == ""


def test_rejects_invalid_pv_feature_constraints(valid_payload: dict[str, Any]) -> None:
    valid_payload["frame"]["future_covariates"]["features"]["Relative Humidity"]["values"][0] = 101.0
    valid_payload["input_digest"] = digest_for(valid_payload)

    response = client_for(FakeEngine()).post("/v1/process", json=valid_payload)

    assert response.status_code == 422
    assert response.json()["code"] == "FRAME_INVALID"


def test_requires_issue_time_for_future_covariates(valid_payload: dict[str, Any]) -> None:
    for item in valid_payload["frame"]["provenance"]:
        if item["segment"] == "future_covariates":
            item.pop("issued_at", None)
            break
    valid_payload["input_digest"] = digest_for(valid_payload)

    response = client_for(FakeEngine()).post("/v1/process", json=valid_payload)

    assert response.status_code == 422
    assert response.json()["code"] == "FRAME_INVALID"


def test_can_fallback_to_last_pv_value(valid_payload: dict[str, Any]) -> None:
    response = client_for(FailingEngine(), allow_fallback=True).post("/v1/process", json=valid_payload)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "fallback"
    assert body["fallback"]["source_feature"] == "pv"
    assert body["output"]["points"][0]["value"] == 430.0


def test_process_route_can_require_bearer_token(valid_payload: dict[str, Any]) -> None:
    auth = BearerAuthPolicy(token="commissioned-secret-0123456789abcdef", required=True)
    client = client_for(FakeEngine(), auth=auth)

    missing = client.post("/v1/process", json=valid_payload)
    accepted = client.post(
        "/v1/process",
        json=valid_payload,
        headers={"authorization": "Bearer commissioned-secret-0123456789abcdef"},
    )

    assert missing.status_code == 401
    assert accepted.status_code == 200
    assert client.get("/health").status_code == 200
