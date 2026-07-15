"""Local mock Chronos-style service for protocol demos and integration smoke tests."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Header, HTTPException


def create_app(
    *,
    expected_token: str | None = None,
    model_family: str = "chronos",
    model_name: str = "chronos-tiny",
    artifact_digest: str = "sha256:" + "2" * 64,
) -> FastAPI:
    app = FastAPI(title="Mock Chronos Forecast Service")

    def authorize(authorization: str | None) -> None:
        if expected_token is None:
            return
        if authorization != f"Bearer {expected_token}":
            raise HTTPException(status_code=401, detail="unauthorized")

    @app.get("/v1/foundation/health")
    def health(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        authorize(authorization)
        return {
            "ready": True,
            "model_family": model_family,
            "model_name": model_name,
        }

    @app.post("/v1/foundation/forecast")
    def forecast(
        payload: dict[str, Any],
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        authorize(authorization)
        history_data = payload.get("history_data")
        forecast_data = payload.get("forecast_data")
        artifact = payload.get("artifact")
        quantiles = payload.get("quantiles") or []
        if not isinstance(history_data, list) or not history_data:
            raise HTTPException(status_code=400, detail="history_data is required")
        if not isinstance(forecast_data, list) or not forecast_data:
            raise HTTPException(status_code=400, detail="forecast_data is required")
        if artifact is not None and not isinstance(artifact, dict):
            raise HTTPException(status_code=400, detail="artifact is invalid")

        base_value = _last_numeric_value(history_data[-1])
        predictions = []
        for index, row in enumerate(forecast_data):
            timestamp = row.get("datetime")
            if not isinstance(timestamp, str):
                raise HTTPException(status_code=400, detail="forecast_data datetime is invalid")
            value = float(base_value + index + 1)
            predictions.append(
                {
                    "timestamp": timestamp,
                    "value": value,
                    "quantiles": [
                        {
                            "probability": float(probability),
                            "value": value,
                        }
                        for probability in quantiles
                    ],
                }
            )

        return {
            "artifact": None
            if artifact is None
            else {
                "kind": artifact.get("kind"),
                "family": artifact.get("family"),
                "version": artifact.get("version"),
                "artifact_digest": artifact_digest,
            },
            "predictions": predictions,
        }

    return app


def _last_numeric_value(row: dict[str, Any]) -> float:
    for key, value in reversed(tuple(row.items())):
        if key == "datetime":
            continue
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return float(value)
    raise HTTPException(status_code=400, detail="no numeric history value found")
