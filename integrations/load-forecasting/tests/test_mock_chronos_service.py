from __future__ import annotations

import importlib.util
from pathlib import Path

from fastapi.testclient import TestClient


MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / "forecast-runtime-core"
    / "mock_chronos_service.py"
)


def load_module():
    spec = importlib.util.spec_from_file_location("mock_chronos_service", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_mock_chronos_service_health_and_forecast() -> None:
    module = load_module()
    client = TestClient(module.create_app(expected_token="secret-token"))

    health = client.get(
        "/v1/foundation/health",
        headers={"Authorization": "Bearer secret-token"},
    )
    assert health.status_code == 200
    assert health.json()["model_family"] == "chronos"

    response = client.post(
        "/v1/foundation/forecast",
        headers={"Authorization": "Bearer secret-token"},
        json={
            "request_id": "0190aee6-2139-7a87-8448-806f1b843201",
            "binding_id": "site-a",
            "as_of": "2099-07-11T12:00:00Z",
            "cadence_seconds": 900,
            "horizon_steps": 2,
            "artifact": {
                "kind": "model",
                "family": "site-load",
                "version": "v3",
            },
            "quantiles": [0.1, 0.9],
            "history_data": [
                {
                    "datetime": "2099-07-11T11:45:00Z",
                    "load": 835.0,
                }
            ],
            "forecast_data": [
                {"datetime": "2099-07-11T12:15:00Z"},
                {"datetime": "2099-07-11T12:30:00Z"},
            ],
            "model_family": "chronos",
            "model_name": "chronos-tiny",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["predictions"][0]["value"] == 836.0
    assert body["predictions"][1]["quantiles"][1]["probability"] == 0.9


def test_mock_chronos_service_rejects_missing_token() -> None:
    module = load_module()
    client = TestClient(module.create_app(expected_token="secret-token"))

    response = client.get("/v1/foundation/health")
    assert response.status_code == 401
