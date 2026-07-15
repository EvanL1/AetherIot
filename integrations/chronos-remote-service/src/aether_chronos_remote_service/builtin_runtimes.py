"""Built-in runtime callables for executor wiring and local demos."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def naive_forecast_runtime(payload: Mapping[str, Any]) -> dict[str, Any]:
    request = payload["request"]
    artifact = payload.get("artifact")
    history_data = request["history_data"]
    forecast_data = request["forecast_data"]
    quantiles = request.get("quantiles", ())

    base_value = _last_numeric_value(history_data[-1])
    predictions: list[dict[str, Any]] = []
    for index, row in enumerate(forecast_data):
        timestamp = _extract_timestamp(row)
        point_value = base_value + index + 1.0
        predictions.append(
            {
                "timestamp": timestamp,
                "value": point_value,
                "quantiles": [
                    {"probability": probability, "value": point_value}
                    for probability in quantiles
                ],
            }
        )

    if artifact is None:
        response_artifact = None
    else:
        response_artifact = {
            "kind": artifact["kind"],
            "family": artifact["family"],
            "version": artifact["version"],
            "artifact_digest": artifact["artifact_digest"],
        }
    return {
        "artifact": response_artifact,
        "predictions": predictions,
    }


def _last_numeric_value(row: Mapping[str, Any]) -> float:
    for key, value in reversed(tuple(row.items())):
        if key == "datetime":
            continue
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return float(value)
    raise ValueError("no numeric history value found")


def _extract_timestamp(row: Mapping[str, Any]) -> str:
    timestamp = row.get("datetime")
    if not isinstance(timestamp, str):
        raise ValueError("forecast_data datetime is invalid")
    return timestamp
