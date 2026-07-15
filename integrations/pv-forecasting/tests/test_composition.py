from __future__ import annotations

import json

import pytest

from aether_pv_forecasting_processor import (
    PvForecastProcessor,
    create_backend_registry,
    create_processor_from_env,
    create_processor_from_bindings,
    load_backend_bindings_from_env,
)
from aether_pv_forecasting_processor.engine import (
    CommissionedArtifactBundle,
    compute_artifact_bundle_digest,
)
from aether_pv_forecasting_processor.processor import ProcessorPolicy


def test_pv_create_processor_from_env_uses_default_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "AETHER_PV_FORECASTING_BACKEND_BINDINGS",
        json.dumps(
            [
                {
                    "task_id": "energy.site-pv-forecast",
                    "task_revision": 1,
                    "binding_id": None,
                    "backend_kind": "remote-http",
                    "backend_config": {
                        "base_url": "https://pv-default.example",
                        "backend_id": "pv-default-remote",
                    },
                }
            ]
        ),
    )

    bindings = load_backend_bindings_from_env(required=True)
    processor = create_processor_from_env(
        binding_id="site-pv-a",
        policy=ProcessorPolicy(history_steps=2),
    )

    assert isinstance(processor, PvForecastProcessor)
    assert bindings.resolve(
        task_id="energy.site-pv-forecast",
        task_revision=1,
        binding_id="site-pv-a",
    ).backend_kind == "remote-http"
    assert processor._engine.descriptor().backend_id == "pv-default-remote"


class PvRecordingInferenceService:
    def run_inference(self, pre_out, raw_event):
        return {
            "statusCode": 200,
            "body": {
                "model_version": raw_event["model_version"],
                "artifact_digest": "sha256:" + "e" * 64,
                "predictions": [
                    {"ts": row["datetime"], "value": 500.0 + index}
                    for index, row in enumerate(pre_out["data"]["forecast"])
                ],
            },
        }


def test_pv_task_can_switch_between_remote_and_legacy_backends(tmp_path) -> None:
    model = tmp_path / "pv.onnx"
    model.write_bytes(b"legacy pv model")
    bundle = CommissionedArtifactBundle(
        files={"model": model},
        expected_digest=compute_artifact_bundle_digest({"model": model}),
    )
    registry = create_backend_registry(
        legacy_inference_service=PvRecordingInferenceService(),
        legacy_artifact_bundles={("model", "site-pv", "v3"): bundle},
        legacy_artifact_file_resolver=lambda _context: bundle.files,
        legacy_readiness_probe=lambda: True,
    )
    bindings = type(load_backend_bindings_from_env(required=False)).from_mappings(
        [
            {
                "task_id": "energy.site-pv-forecast",
                "task_revision": 1,
                "binding_id": "site-pv-remote",
                "backend_kind": "remote-http",
                "backend_config": {
                    "base_url": "https://pv-remote.example",
                    "backend_id": "pv-remote-choice",
                },
            },
            {
                "task_id": "energy.site-pv-forecast",
                "task_revision": 1,
                "binding_id": "site-pv-legacy",
                "backend_kind": "legacy-edge-platform",
                "backend_config": {},
            },
        ]
    )

    remote_processor = create_processor_from_bindings(
        binding_id="site-pv-remote",
        backend_bindings=bindings,
        registry=registry,
        policy=ProcessorPolicy(history_steps=2),
    )
    legacy_processor = create_processor_from_bindings(
        binding_id="site-pv-legacy",
        backend_bindings=bindings,
        registry=registry,
        policy=ProcessorPolicy(history_steps=2),
    )

    assert remote_processor._engine.descriptor().backend_id == "pv-remote-choice"
    assert legacy_processor._engine.descriptor().backend_id == "legacy-pv-edge-platform"
