"""Runtime entrypoints that bridge existing EMS model serving into AetherIot."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from typing import Any


def ems_serving_http_runtime(payload: Mapping[str, Any]) -> dict[str, Any]:
    request = _mapping(payload.get("request"), "request")
    artifact = payload.get("artifact")
    runtime_options = _mapping(payload.get("runtime_options") or {}, "runtime_options")

    quantiles = request.get("quantiles") or ()
    expected_quantiles = _expected_quantiles(quantiles)

    base_url = _string(runtime_options.get("base_url"), "runtime_options.base_url")
    model_type = runtime_options.get("model_type")
    if model_type is None:
        model_type = _infer_model_type(artifact, request)
    model_type = _string(model_type, "runtime_options.model_type")

    history_rows = request.get("history_data")
    if not isinstance(history_rows, list) or not history_rows:
        raise ValueError("request.history_data must be a non-empty list")

    response = _post_json(
        url=f"{base_url.rstrip('/')}/api/predict/{model_type}",
        payload={"rows": history_rows},
        headers=_headers(runtime_options),
        timeout_seconds=_timeout_seconds(runtime_options.get("timeout_seconds", 30.0)),
    )
    predictions = response.get("predictions")
    if not isinstance(predictions, Sequence) or isinstance(predictions, (str, bytes)):
        raise ValueError("EMS serving response predictions are invalid")

    forecast_rows = request.get("forecast_data")
    if not isinstance(forecast_rows, list):
        raise ValueError("request.forecast_data must be a list")
    if len(predictions) != len(forecast_rows):
        raise ValueError("EMS serving response horizon does not match request")

    normalized_predictions: list[dict[str, Any]] = []
    for forecast_row, prediction in zip(forecast_rows, predictions):
        timestamp = _string(forecast_row.get("datetime"), "forecast_data.datetime")
        prediction_map = _mapping(prediction, "prediction")
        value = prediction_map.get("value")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("EMS serving response prediction value is invalid")
        normalized_predictions.append(
            {
                "timestamp": timestamp,
                "value": float(value),
                "quantiles": _extract_quantiles(prediction_map, expected_quantiles),
            }
        )

    response_artifact = None
    if artifact is not None:
        artifact_map = _mapping(artifact, "artifact")
        response_artifact = {
            "kind": _string(artifact_map.get("kind"), "artifact.kind"),
            "family": _string(artifact_map.get("family"), "artifact.family"),
            "version": _string(artifact_map.get("version"), "artifact.version"),
            "artifact_digest": _string(artifact_map.get("artifact_digest"), "artifact.artifact_digest"),
        }
    return {
        "artifact": response_artifact,
        "predictions": normalized_predictions,
    }


def _expected_quantiles(value: Any) -> tuple[float, ...]:
    if not value:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError("request.quantiles must be a numeric array")
    expected: list[float] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise ValueError("request.quantiles must be numeric")
        expected.append(float(item))
    return tuple(expected)


def _extract_quantiles(
    prediction: Mapping[str, Any],
    expected_quantiles: tuple[float, ...],
) -> list[dict[str, float]]:
    if not expected_quantiles:
        return []
    if "quantiles" in prediction:
        raw = prediction.get("quantiles")
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
            raise ValueError("EMS serving quantiles are invalid")
        parsed: list[dict[str, float]] = []
        for item in raw:
            item_map = _mapping(item, "prediction.quantile")
            probability = item_map.get("probability")
            quantile_value = item_map.get("value")
            if isinstance(probability, bool) or not isinstance(probability, (int, float)):
                raise ValueError("EMS serving quantile probability is invalid")
            if isinstance(quantile_value, bool) or not isinstance(quantile_value, (int, float)):
                raise ValueError("EMS serving quantile value is invalid")
            parsed.append({"probability": float(probability), "value": float(quantile_value)})
        if tuple(item["probability"] for item in parsed) != expected_quantiles:
            raise ValueError("EMS serving quantiles do not match request")
        return parsed

    legacy_field_map = {
        0.1: "p10",
        0.5: "p50",
        0.9: "p90",
    }
    parsed = []
    for probability in expected_quantiles:
        field_name = legacy_field_map.get(probability)
        if field_name is None:
            raise ValueError("EMS serving quantile mapping is not configured for request")
        quantile_value = prediction.get(field_name)
        if isinstance(quantile_value, bool) or not isinstance(quantile_value, (int, float)):
            raise ValueError("EMS serving quantile value is missing")
        parsed.append({"probability": probability, "value": float(quantile_value)})
    return parsed


def _post_json(
    *,
    url: str,
    payload: Mapping[str, Any],
    headers: Mapping[str, str],
    timeout_seconds: float,
) -> Mapping[str, Any]:
    request = urllib.request.Request(
        url=url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            **dict(headers),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            decoded = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise ValueError(f"EMS serving request failed: HTTP {exc.code} {detail}".strip()) from None
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ValueError(f"EMS serving request failed: {exc}") from None
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("EMS serving response is not valid JSON") from None
    if not isinstance(decoded, Mapping):
        raise ValueError("EMS serving response is invalid")
    return decoded


def _headers(runtime_options: Mapping[str, Any]) -> dict[str, str]:
    token = runtime_options.get("api_token")
    if token is None:
        return {}
    return {"Authorization": f"Bearer {_string(token, 'runtime_options.api_token')}"}


def _timeout_seconds(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or float(value) <= 0:
        raise ValueError("runtime_options.timeout_seconds must be positive")
    return float(value)


def _infer_model_type(artifact: Any, request: Mapping[str, Any]) -> str:
    candidates: list[str] = []
    if isinstance(artifact, Mapping):
        family = artifact.get("family")
        if isinstance(family, str):
            candidates.append(family)
    binding_id = request.get("binding_id")
    if isinstance(binding_id, str):
        candidates.append(binding_id)
    for candidate in candidates:
        lowered = candidate.lower()
        if "pv" in lowered or "solar" in lowered:
            return "pv"
        if "load" in lowered:
            return "load"
    raise ValueError("runtime_options.model_type is required when artifact family cannot infer pv/load")


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value
