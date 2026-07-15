from __future__ import annotations

from pathlib import Path

import pytest

from aether_pv_forecasting_processor.engine import (
    CommissionedArtifactBundle,
    EdgePlatformInferenceServiceEngine,
    EngineUnavailable,
    ForecastContext,
    compute_artifact_bundle_digest,
)


class RecordingInferenceService:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def run_inference(self, pre_out, raw_event):
        self.calls.append((pre_out, raw_event))
        return self.result


def context() -> ForecastContext:
    return ForecastContext(
        request_id="0190aee6-2139-7a87-8448-806f1b843201",
        binding_id="site-a",
        as_of="2099-07-11T12:00:00Z",
        cadence_seconds=1800,
        horizon_steps=2,
        artifact_kind="model",
        artifact_family="site-pv",
        artifact_version="v3",
        quantiles=(),
    )


def commissioned_bundle(tmp_path: Path) -> CommissionedArtifactBundle:
    files = {
        "model": tmp_path / "pv.onnx",
        "scaler": tmp_path / "pv-scaler.json",
        "config": tmp_path / "pv-config.json",
    }
    files["model"].write_bytes(b"commissioned pv model")
    files["scaler"].write_bytes(b'{"scale": 2.0}')
    files["config"].write_bytes(b'{"features": ["pv", "GHI"]}')
    return CommissionedArtifactBundle(
        files=files,
        expected_digest=compute_artifact_bundle_digest(files),
    )


def resolver_for(bundle: CommissionedArtifactBundle):
    def resolve(_context):
        return bundle.files

    return resolve


def test_edge_platform_adapter_only_passes_request_data_in_memory(tmp_path: Path) -> None:
    service = RecordingInferenceService(
        {
            "statusCode": 200,
            "body": {
                "model_version": "v3",
                "predictions": [
                    {"ts": "2099-07-11T12:30:00Z", "value": 500.0},
                    {"ts": "2099-07-11T13:00:00Z", "value": 510.0},
                ],
            },
        }
    )
    bundle = commissioned_bundle(tmp_path)
    engine = EdgePlatformInferenceServiceEngine(
        service,
        artifact_bundles={("model", "site-pv", "v3"): bundle},
        artifact_file_resolver=resolver_for(bundle),
        readiness_probe=lambda: True,
    )

    result = engine.forecast(
        history_data=[{"datetime": "2099-07-11T11:30:00Z", "pv": 420.0}],
        forecast_data=[{"datetime": "2099-07-11T12:30:00Z"}, {"datetime": "2099-07-11T13:00:00Z"}],
        context=context(),
    )

    assert [point.value for point in result.points] == [500.0, 510.0]
    pre_out, raw_event = service.calls[0]
    assert pre_out["forecast_type"] == "pv_forecast"
    assert raw_event["forecast_type"] == "pv_forecast"


def test_edge_platform_adapter_rejects_uncommissioned_artifact_selection() -> None:
    service = RecordingInferenceService({"statusCode": 200, "body": {}})

    with pytest.raises(EngineUnavailable, match="not commissioned"):
        EdgePlatformInferenceServiceEngine(service).forecast(
            history_data=[],
            forecast_data=[],
            context=context(),
        )
