from __future__ import annotations

import hashlib
from copy import deepcopy
from typing import Any

import pytest
import rfc8785

from aether_pv_forecasting_processor.models import (
    DataProcessingRequest,
    compute_input_digest,
)


def digest_for(payload: dict[str, Any]) -> str:
    try:
        return compute_input_digest(DataProcessingRequest.model_validate(payload))
    except ValueError:
        digest_input = {
            "task": payload["task"],
            "binding": payload["binding"],
            "processor_contract": payload["processor_contract"],
            "artifact": payload.get("artifact"),
            "frame": payload["frame"],
            "options": payload["options"],
        }
        return f"sha256:{hashlib.sha256(rfc8785.dumps(digest_input)).hexdigest()}"


def _series(unit: str, values: list[float]) -> dict[str, Any]:
    return {
        "value_type": "number",
        "unit": unit,
        "values": values,
        "quality": ["good"] * len(values),
    }


@pytest.fixture
def valid_payload() -> dict[str, Any]:
    history_timestamps = [
        "2099-07-11T11:30:00Z",
        "2099-07-11T12:00:00Z",
    ]
    future_timestamps = [
        "2099-07-11T12:30:00Z",
        "2099-07-11T13:00:00Z",
    ]
    history_features = {
        "pv": _series("kW", [420.0, 430.0]),
        "DHI": _series("W/m2", [100.0, 120.0]),
        "DNI": _series("W/m2", [200.0, 220.0]),
        "GHI": _series("W/m2", [300.0, 320.0]),
        "Clearsky DHI": _series("W/m2", [110.0, 130.0]),
        "Clearsky DNI": _series("W/m2", [210.0, 230.0]),
        "Clearsky GHI": _series("W/m2", [310.0, 330.0]),
        "Cloud Type": _series("1", [3.0, 4.0]),
        "Dew Point": _series("Cel", [18.0, 18.5]),
        "Solar Zenith Angle": _series("deg", [45.0, 30.0]),
        "Fill Flag": _series("1", [0.0, 0.0]),
        "Surface Albedo": _series("1", [0.2, 0.2]),
        "Wind Speed": _series("m/s", [3.0, 3.5]),
        "Precipitable": _series("cm", [1.1, 1.2]),
        "Wind Direction": _series("deg", [180.0, 190.0]),
        "Relative Humidity": _series("%", [60.0, 58.0]),
        "Temperature": _series("Cel", [26.0, 27.0]),
        "Pressure": _series("hPa", [1008.0, 1009.0]),
        "Global Horizontal UV Irradiance 280-440": _series("W/m2", [5.0, 6.0]),
        "Global Horizontal UV Irradiance 295-385": _series("W/m2", [4.0, 5.0]),
    }
    future_features = {
        name: deepcopy(series)
        for name, series in history_features.items()
        if name != "pv"
    }
    future_features["DHI"]["values"] = [140.0, 150.0]
    future_features["DNI"]["values"] = [240.0, 250.0]
    future_features["GHI"]["values"] = [340.0, 350.0]
    future_features["Temperature"]["values"] = [28.0, 29.0]

    payload: dict[str, Any] = {
        "schema": "aether.data-processing.request.v1",
        "request_id": "0190aee6-2139-7a87-8448-806f1b843201",
        "submitted_at": "2099-07-11T12:00:01Z",
        "deadline": "2099-07-11T12:00:06Z",
        "task": {"id": "energy.site-pv-forecast", "revision": 1, "kind": "forecast"},
        "binding": {"id": "site-a", "revision": 7},
        "processor_contract": "aether.data-processing.forecast.v1",
        "artifact": {"kind": "model", "family": "site-pv", "version": "v3"},
        "frame": {
            "schema": "aether.processing-frame.v1",
            "as_of": "2099-07-11T12:00:00Z",
            "cadence_seconds": 1800,
            "history": {"timestamps": history_timestamps, "features": history_features},
            "future_covariates": {
                "timestamps": future_timestamps,
                "features": future_features,
            },
            "static_features": {},
            "quality": {
                "input_watermark": "2099-07-11T12:00:00Z",
                "missing_ratio": 0.0,
                "max_gap_seconds": 1800,
                "live_tail_included": False,
                "substituted_samples": 0,
            },
            "provenance": [
                {
                    "segment": "history",
                    "feature": feature,
                    "source_kind": "history",
                    "source_ref": f"history.{feature.lower().replace(' ', '_')}",
                    "watermark": "2099-07-11T12:00:00Z",
                }
                for feature in history_features
            ]
            + [
                {
                    "segment": "future_covariates",
                    "feature": feature,
                    "source_kind": "covariate",
                    "source_ref": f"weather.nwp.{feature.lower().replace(' ', '_')}",
                    "watermark": "2099-07-11T11:50:00Z",
                    "issued_at": "2099-07-11T11:40:00Z",
                }
                for feature in future_features
            ],
        },
        "options": {"kind": "forecast", "horizon_steps": 2},
        "input_digest": "",
    }
    payload["input_digest"] = digest_for(payload)
    return deepcopy(payload)
