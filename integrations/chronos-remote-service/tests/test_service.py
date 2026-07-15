from __future__ import annotations

import json
from pathlib import Path
import urllib.error

import pytest
from fastapi.testclient import TestClient

from aether_chronos_remote_service import (
    PlaceholderChronosExecutor,
    ServiceConfig,
    build_backend_bindings,
    build_ems_artifact_registry,
    build_release_metadata,
    build_service_env,
    create_release_directory,
    create_app,
)
from aether_chronos_remote_service import app as app_module
from aether_chronos_remote_service import ems_serving_runtime
from aether_chronos_remote_service.executor import (
    ArtifactRegistry,
    PlaceholderChronosExecutor as ExecutorClass,
    PythonEntrypointChronosExecutor,
    create_executor_from_config,
)
from aether_chronos_remote_service.registry_builder import main as registry_builder_main
from aether_chronos_remote_service.deployment_preset_builder import (
    main as deployment_preset_builder_main,
)
from aether_chronos_remote_service.ems_publish_hook import main as ems_publish_hook_main


def test_service_health_and_forecast() -> None:
    client = TestClient(
        create_app(
            config=ServiceConfig(token="secret-token"),
            executor=PlaceholderChronosExecutor(),
        )
    )

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
                {"datetime": "2099-07-11T11:45:00Z", "load": 835.0}
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
    assert body["artifact"]["family"] == "site-load"


def test_service_rejects_wrong_token_and_model_identity() -> None:
    client = TestClient(create_app(config=ServiceConfig(token="secret-token")))

    unauthorized = client.get("/v1/foundation/health")
    assert unauthorized.status_code == 401

    bad_model = client.post(
        "/v1/foundation/forecast",
        headers={"Authorization": "Bearer secret-token"},
        json={
            "request_id": "0190aee6-2139-7a87-8448-806f1b843201",
            "binding_id": "site-a",
            "as_of": "2099-07-11T12:00:00Z",
            "cadence_seconds": 900,
            "horizon_steps": 1,
            "artifact": {
                "kind": "model",
                "family": "site-load",
                "version": "v3",
            },
            "quantiles": [],
            "history_data": [
                {"datetime": "2099-07-11T11:45:00Z", "load": 835.0}
            ],
            "forecast_data": [{"datetime": "2099-07-11T12:15:00Z"}],
            "model_family": "not-chronos",
            "model_name": "chronos-tiny",
        },
    )
    assert bad_model.status_code == 400


def test_service_rejects_wrong_horizon_and_executor_value_errors() -> None:
    client = TestClient(create_app(config=ServiceConfig(token=None)))

    bad_horizon = client.post(
        "/v1/foundation/forecast",
        json={
            "request_id": "0190aee6-2139-7a87-8448-806f1b843201",
            "binding_id": "site-a",
            "as_of": "2099-07-11T12:00:00Z",
            "cadence_seconds": 900,
            "horizon_steps": 2,
            "artifact": None,
            "quantiles": [],
            "history_data": [{"datetime": "2099-07-11T11:45:00Z", "load": 835.0}],
            "forecast_data": [{"datetime": "2099-07-11T12:15:00Z"}],
            "model_family": "chronos",
            "model_name": "chronos-tiny",
        },
    )
    assert bad_horizon.status_code == 400

    bad_value = client.post(
        "/v1/foundation/forecast",
        json={
            "request_id": "0190aee6-2139-7a87-8448-806f1b843201",
            "binding_id": "site-a",
            "as_of": "2099-07-11T12:00:00Z",
            "cadence_seconds": 900,
            "horizon_steps": 1,
            "artifact": None,
            "quantiles": [],
            "history_data": [{"datetime": "2099-07-11T11:45:00Z", "note": "bad"}],
            "forecast_data": [{"datetime": "2099-07-11T12:15:00Z"}],
            "model_family": "chronos",
            "model_name": "chronos-tiny",
        },
    )
    assert bad_value.status_code == 400


def test_executor_rejects_invalid_forecast_datetime() -> None:
    executor = ExecutorClass()
    request = {
        "request_id": "0190aee6-2139-7a87-8448-806f1b843201",
        "binding_id": "site-a",
        "as_of": "2099-07-11T12:00:00Z",
        "cadence_seconds": 900,
        "horizon_steps": 1,
        "artifact": None,
        "quantiles": [],
        "history_data": [{"datetime": "2099-07-11T11:45:00Z", "load": 835.0}],
        "forecast_data": [{"ts": "2099-07-11T12:15:00Z"}],
        "model_family": "chronos",
        "model_name": "chronos-tiny",
    }
    client = TestClient(create_app(config=ServiceConfig()))
    response = client.post("/v1/foundation/forecast", json=request)
    assert response.status_code == 400


def test_config_from_env_and_app_main(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AETHER_CHRONOS_SERVICE_HOST", "127.0.0.2")
    monkeypatch.setenv("AETHER_CHRONOS_SERVICE_PORT", "9010")
    monkeypatch.setenv("AETHER_CHRONOS_SERVICE_TOKEN", "secret-token")
    monkeypatch.setenv("AETHER_CHRONOS_SERVICE_MODEL_FAMILY", "chronos")
    monkeypatch.setenv("AETHER_CHRONOS_SERVICE_MODEL_NAME", "chronos-big")
    monkeypatch.setenv("AETHER_CHRONOS_SERVICE_EXECUTOR_BACKEND", "placeholder")

    captured = {}

    def fake_run(app, host, port):
        captured["app"] = app
        captured["host"] = host
        captured["port"] = port

    monkeypatch.setattr(app_module.uvicorn, "run", fake_run)
    monkeypatch.setattr(
        app_module,
        "config",
        ServiceConfig.from_env(),
    )
    monkeypatch.setattr(
        app_module,
        "app",
        create_app(config=app_module.config),
    )

    app_module.main()

    assert captured["host"] == "127.0.0.2"
    assert captured["port"] == 9010


def test_python_entrypoint_executor_uses_registered_artifact(tmp_path: Path) -> None:
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "models": [
                    {
                        "kind": "model",
                        "family": "site-load",
                        "version": "chronos-v1",
                        "artifact_digest": "sha256:" + "4" * 64,
                        "model_family": "chronos",
                        "model_name": "chronos-tiny",
                        "runtime_entrypoint": "aether_chronos_remote_service.builtin_runtimes:naive_forecast_runtime",
                        "runtime_options": {"device": "cpu"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    config = ServiceConfig(
        executor_backend="python-entrypoint",
        runtime_entrypoint="aether_chronos_remote_service.builtin_runtimes:naive_forecast_runtime",
        artifact_registry_path=str(registry_path),
    )
    client = TestClient(create_app(config=config))

    response = client.post(
        "/v1/foundation/forecast",
        json={
            "request_id": "0190aee6-2139-7a87-8448-806f1b843201",
            "binding_id": "site-a",
            "as_of": "2099-07-11T12:00:00Z",
            "cadence_seconds": 900,
            "horizon_steps": 2,
            "artifact": {
                "kind": "model",
                "family": "site-load",
                "version": "chronos-v1",
            },
            "quantiles": [0.1, 0.9],
            "history_data": [
                {"datetime": "2099-07-11T11:45:00Z", "load": 835.0}
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
    assert body["artifact"]["artifact_digest"] == "sha256:" + "4" * 64
    assert body["predictions"][1]["quantiles"][1]["probability"] == 0.9


def test_python_entrypoint_executor_rejects_unregistered_artifact(tmp_path: Path) -> None:
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "models": [
                    {
                        "kind": "model",
                        "family": "site-load",
                        "version": "chronos-v1",
                        "artifact_digest": "sha256:" + "4" * 64,
                        "model_family": "chronos",
                        "model_name": "chronos-tiny",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    executor = PythonEntrypointChronosExecutor(
        default_runtime_entrypoint="aether_chronos_remote_service.builtin_runtimes:naive_forecast_runtime",
        default_artifact_digest="sha256:" + "3" * 64,
        model_family="chronos",
        model_name="chronos-tiny",
        artifact_registry=ArtifactRegistry.from_path(registry_path),
    )
    client = TestClient(create_app(config=ServiceConfig(), executor=executor))

    response = client.post(
        "/v1/foundation/forecast",
        json={
            "request_id": "0190aee6-2139-7a87-8448-806f1b843201",
            "binding_id": "site-a",
            "as_of": "2099-07-11T12:00:00Z",
            "cadence_seconds": 900,
            "horizon_steps": 1,
            "artifact": {
                "kind": "model",
                "family": "site-load",
                "version": "missing-v2",
            },
            "quantiles": [],
            "history_data": [{"datetime": "2099-07-11T11:45:00Z", "load": 835.0}],
            "forecast_data": [{"datetime": "2099-07-11T12:15:00Z"}],
            "model_family": "chronos",
            "model_name": "chronos-tiny",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "requested artifact is not registered"


def test_create_executor_from_config_requires_runtime_entrypoint() -> None:
    with pytest.raises(ValueError, match="runtime_entrypoint"):
        create_executor_from_config(
            ServiceConfig(
                executor_backend="python-entrypoint",
                runtime_entrypoint=None,
            )
        )


def test_ems_serving_runtime_maps_existing_predictor_response(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post_json(*, url, payload, headers, timeout_seconds):
        assert url == "http://127.0.0.1:8010/api/predict/load"
        assert payload["rows"][0]["load"] == 835.0
        assert headers == {}
        assert timeout_seconds == 30.0
        return {
            "predictions": [
                {"timestamp": "ignore-1", "value": 901.0},
                {"timestamp": "ignore-2", "value": 902.0},
            ]
        }

    monkeypatch.setattr(ems_serving_runtime, "_post_json", fake_post_json)

    response = ems_serving_runtime.ems_serving_http_runtime(
        {
            "request": {
                "binding_id": "site-a",
                "quantiles": [],
                "history_data": [{"datetime": "2099-07-11T11:45:00Z", "load": 835.0}],
                "forecast_data": [
                    {"datetime": "2099-07-11T12:15:00Z"},
                    {"datetime": "2099-07-11T12:30:00Z"},
                ],
            },
            "artifact": {
                "kind": "model",
                "family": "site-load",
                "version": "ems-load-v1",
                "artifact_digest": "sha256:" + "5" * 64,
            },
            "runtime_options": {
                "base_url": "http://127.0.0.1:8010",
                "model_type": "load",
                "timeout_seconds": 30,
            },
        }
    )

    assert response["artifact"]["version"] == "ems-load-v1"
    assert response["predictions"][0]["timestamp"] == "2099-07-11T12:15:00Z"
    assert response["predictions"][1]["value"] == 902.0


def test_ems_serving_runtime_rejects_quantiles(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post_json(*, url, payload, headers, timeout_seconds):
        return {"predictions": [{"value": 101.0}]}

    monkeypatch.setattr(ems_serving_runtime, "_post_json", fake_post_json)

    with pytest.raises(ValueError, match="quantile value is missing"):
        ems_serving_runtime.ems_serving_http_runtime(
            {
                "request": {
                    "binding_id": "site-a",
                    "quantiles": [0.1, 0.9],
                    "history_data": [{"datetime": "2099-07-11T11:45:00Z", "load": 835.0}],
                    "forecast_data": [{"datetime": "2099-07-11T12:15:00Z"}],
                },
                "artifact": {
                    "kind": "model",
                    "family": "site-load",
                    "version": "ems-load-v1",
                    "artifact_digest": "sha256:" + "5" * 64,
                },
                "runtime_options": {
                    "base_url": "http://127.0.0.1:8010",
                    "model_type": "load",
                },
            }
        )


def test_ems_serving_runtime_maps_legacy_quantile_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post_json(*, url, payload, headers, timeout_seconds):
        return {
            "predictions": [
                {"value": 101.0, "p10": 90.0, "p50": 101.0, "p90": 111.0}
            ]
        }

    monkeypatch.setattr(ems_serving_runtime, "_post_json", fake_post_json)

    response = ems_serving_runtime.ems_serving_http_runtime(
        {
            "request": {
                "binding_id": "site-a",
                "quantiles": [0.1, 0.5, 0.9],
                "history_data": [{"datetime": "2099-07-11T11:45:00Z", "load": 835.0}],
                "forecast_data": [{"datetime": "2099-07-11T12:15:00Z"}],
            },
            "artifact": {
                "kind": "model",
                "family": "site-load",
                "version": "ems-load-v1",
                "artifact_digest": "sha256:" + "5" * 64,
            },
            "runtime_options": {
                "base_url": "http://127.0.0.1:8010",
                "model_type": "load",
            },
        }
    )

    assert response["predictions"][0]["quantiles"][0]["probability"] == 0.1
    assert response["predictions"][0]["quantiles"][2]["value"] == 111.0


def test_ems_serving_runtime_infers_model_type_and_allows_no_artifact(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post_json(*, url, payload, headers, timeout_seconds):
        assert url == "http://127.0.0.1:8010/api/predict/pv"
        assert headers == {"Authorization": "Bearer token-1"}
        assert timeout_seconds == 15.0
        return {"predictions": [{"value": 101.0}]}

    monkeypatch.setattr(ems_serving_runtime, "_post_json", fake_post_json)

    response = ems_serving_runtime.ems_serving_http_runtime(
        {
            "request": {
                "binding_id": "solar-site-a",
                "quantiles": [],
                "history_data": [{"datetime": "2099-07-11T11:45:00Z", "pv": 83.5}],
                "forecast_data": [{"datetime": "2099-07-11T12:15:00Z"}],
            },
            "runtime_options": {
                "base_url": "http://127.0.0.1:8010",
                "api_token": "token-1",
                "timeout_seconds": 15,
            },
        }
    )

    assert response["artifact"] is None
    assert response["predictions"][0]["timestamp"] == "2099-07-11T12:15:00Z"


def test_ems_serving_runtime_rejects_bad_prediction_shapes(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post_json(*, url, payload, headers, timeout_seconds):
        return {"predictions": [{"value": True}]}

    monkeypatch.setattr(ems_serving_runtime, "_post_json", fake_post_json)

    with pytest.raises(ValueError, match="prediction value"):
        ems_serving_runtime.ems_serving_http_runtime(
            {
                "request": {
                    "binding_id": "site-load-a",
                    "quantiles": [],
                    "history_data": [{"datetime": "2099-07-11T11:45:00Z", "load": 835.0}],
                    "forecast_data": [{"datetime": "2099-07-11T12:15:00Z"}],
                },
                "artifact": {
                    "kind": "model",
                    "family": "site-load",
                    "version": "ems-load-v1",
                    "artifact_digest": "sha256:" + "5" * 64,
                },
                "runtime_options": {
                    "base_url": "http://127.0.0.1:8010",
                    "model_type": "load",
                },
            }
        )


def test_ems_serving_runtime_validates_horizon_and_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError, match="timeout_seconds"):
        ems_serving_runtime.ems_serving_http_runtime(
            {
                "request": {
                    "binding_id": "site-load-a",
                    "quantiles": [],
                    "history_data": [{"datetime": "2099-07-11T11:45:00Z", "load": 835.0}],
                    "forecast_data": [{"datetime": "2099-07-11T12:15:00Z"}],
                },
                "runtime_options": {
                    "base_url": "http://127.0.0.1:8010",
                    "model_type": "load",
                    "timeout_seconds": 0,
                },
            }
        )

    def fake_post_json(*, url, payload, headers, timeout_seconds):
        return {"predictions": [{"value": 100.0}, {"value": 101.0}]}

    monkeypatch.setattr(ems_serving_runtime, "_post_json", fake_post_json)

    with pytest.raises(ValueError, match="horizon"):
        ems_serving_runtime.ems_serving_http_runtime(
            {
                "request": {
                    "binding_id": "site-load-a",
                    "quantiles": [],
                    "history_data": [{"datetime": "2099-07-11T11:45:00Z", "load": 835.0}],
                    "forecast_data": [{"datetime": "2099-07-11T12:15:00Z"}],
                },
                "runtime_options": {
                    "base_url": "http://127.0.0.1:8010",
                    "model_type": "load",
                },
            }
        )


def test_ems_serving_post_json_error_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        def __init__(self, body: bytes) -> None:
            self._body = body

        def read(self) -> bytes:
            return self._body

        def close(self) -> None:
            return None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen_ok(request, timeout):
        return FakeResponse(b'{"predictions":[]}')

    monkeypatch.setattr(ems_serving_runtime.urllib.request, "urlopen", fake_urlopen_ok)
    assert ems_serving_runtime._post_json(
        url="http://127.0.0.1:8010/api/predict/load",
        payload={"rows": []},
        headers={},
        timeout_seconds=5.0,
    )["predictions"] == []

    def fake_urlopen_http_error(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            500,
            "server error",
            hdrs=None,
            fp=FakeResponse(b"boom"),
        )

    monkeypatch.setattr(ems_serving_runtime.urllib.request, "urlopen", fake_urlopen_http_error)
    with pytest.raises(ValueError, match="HTTP 500"):
        ems_serving_runtime._post_json(
            url="http://127.0.0.1:8010/api/predict/load",
            payload={"rows": []},
            headers={},
            timeout_seconds=5.0,
        )

    def fake_urlopen_bad_json(request, timeout):
        return FakeResponse(b"{bad")

    monkeypatch.setattr(ems_serving_runtime.urllib.request, "urlopen", fake_urlopen_bad_json)
    with pytest.raises(ValueError, match="valid JSON"):
        ems_serving_runtime._post_json(
            url="http://127.0.0.1:8010/api/predict/load",
            payload={"rows": []},
            headers={},
            timeout_seconds=5.0,
        )


def test_build_ems_artifact_registry_uses_manifest_sha256() -> None:
    registry = build_ems_artifact_registry(
        {
            "load": {
                "version": "load_20260715T100000Z",
                "sha256": "a" * 64,
            },
            "pv_forecast": {
                "version": "pv_20260715T100000Z",
                "sha256": "b" * 64,
            },
        },
        base_url="http://127.0.0.1:8010",
        model_family="chronos",
        model_name="chronos-tiny",
    )

    assert registry["models"][0]["family"] == "site-load"
    assert registry["models"][0]["artifact_digest"] == "sha256:" + "a" * 64
    assert registry["models"][1]["family"] == "site-pv"
    assert registry["models"][1]["runtime_options"]["model_type"] == "pv"


def test_build_ems_artifact_registry_rejects_missing_manifest_entry() -> None:
    with pytest.raises(ValueError, match="pv entry"):
        build_ems_artifact_registry(
            {
                "load": {
                    "version": "load_20260715T100000Z",
                    "sha256": "a" * 64,
                }
            },
            base_url="http://127.0.0.1:8010",
            model_family="chronos",
            model_name="chronos-tiny",
        )


def test_registry_builder_main_generates_output(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    output_path = tmp_path / "artifact-registry.json"
    manifest_path.write_text(
        json.dumps(
            {
                "load": {
                    "version": "load_20260715T100000Z",
                    "sha256": "a" * 64,
                },
                "pv": {
                    "version": "pv_20260715T100000Z",
                    "sha256": "b" * 64,
                },
            }
        ),
        encoding="utf-8",
    )

    exit_code = registry_builder_main(
        [
            "--manifest",
            str(manifest_path),
            "--output",
            str(output_path),
            "--base-url",
            "http://127.0.0.1:8010",
        ]
    )

    assert exit_code == 0
    generated = json.loads(output_path.read_text(encoding="utf-8"))
    assert generated["models"][0]["runtime_entrypoint"].endswith(":ems_serving_http_runtime")


def test_build_backend_bindings_and_service_env() -> None:
    bindings = build_backend_bindings(
        forecast_service_base_url="http://127.0.0.1:9000",
        api_token="processor-token",
    )
    assert bindings[0]["backend_config"]["backend_id"] == "load-ems-bridge"
    assert bindings[1]["backend_config"]["backend_id"] == "pv-ems-bridge"

    env_text = build_service_env(
        service_host="127.0.0.1",
        service_port=9000,
        service_token="service-token",
        model_family="chronos",
        model_name="chronos-tiny",
        artifact_registry_path="C:/tmp/artifact-registry.generated.json",
        runtime_entrypoint="aether_chronos_remote_service.ems_serving_runtime:ems_serving_http_runtime",
    )
    assert "AETHER_CHRONOS_SERVICE_EXECUTOR_BACKEND=python-entrypoint" in env_text
    assert "AETHER_CHRONOS_SERVICE_ARTIFACT_REGISTRY=C:/tmp/artifact-registry.generated.json" in env_text

    release_metadata = build_release_metadata(
        {
            "load": {
                "version": "load_20260715T100000Z",
                "sha256": "a" * 64,
            },
            "pv": {
                "version": "pv_20260715T100000Z",
                "sha256": "b" * 64,
            },
        },
        forecast_service_base_url="http://127.0.0.1:9000",
        registry_path="C:/tmp/artifact-registry.generated.json",
        backend_bindings_path="C:/tmp/backend-bindings.generated.json",
        service_env_path="C:/tmp/forecast-service.generated.env",
        source_manifest_path="C:/tmp/manifest.json",
    )
    assert release_metadata["artifacts"]["load"]["version"] == "load_20260715T100000Z"
    assert release_metadata["generated_files"]["backend_bindings"].endswith("backend-bindings.generated.json")


def test_deployment_preset_builder_main_generates_all_outputs(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    output_dir = tmp_path / "preset"
    manifest_path.write_text(
        json.dumps(
            {
                "load": {
                    "version": "load_20260715T100000Z",
                    "sha256": "a" * 64,
                },
                "pv": {
                    "version": "pv_20260715T100000Z",
                    "sha256": "b" * 64,
                },
            }
        ),
        encoding="utf-8",
    )

    exit_code = deployment_preset_builder_main(
        [
            "--manifest",
            str(manifest_path),
            "--output-dir",
            str(output_dir),
            "--forecast-service-base-url",
            "http://127.0.0.1:9000",
            "--forecast-service-token",
            "processor-token",
            "--service-token",
            "service-token",
        ]
    )

    assert exit_code == 0
    generated_registry = json.loads((output_dir / "artifact-registry.generated.json").read_text(encoding="utf-8"))
    generated_bindings = json.loads((output_dir / "backend-bindings.generated.json").read_text(encoding="utf-8"))
    generated_env = (output_dir / "forecast-service.generated.env").read_text(encoding="utf-8")
    generated_release = json.loads((output_dir / "release-metadata.generated.json").read_text(encoding="utf-8"))

    assert generated_registry["models"][0]["artifact_digest"] == "sha256:" + "a" * 64
    assert generated_bindings[0]["backend_config"]["api_token"] == "processor-token"
    assert "AETHER_CHRONOS_SERVICE_TOKEN=service-token" in generated_env
    assert generated_release["artifacts"]["pv"]["version"] == "pv_20260715T100000Z"


def test_create_release_directory_and_publish_hook(tmp_path: Path) -> None:
    release_dir = create_release_directory(tmp_path)
    assert release_dir.exists()

    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "load": {"version": "load_20260715T100000Z", "sha256": "a" * 64},
                "pv": {"version": "pv_20260715T100000Z", "sha256": "b" * 64},
            }
        ),
        encoding="utf-8",
    )
    release_root = tmp_path / "releases"
    exit_code = ems_publish_hook_main(
        [
            "--manifest",
            str(manifest_path),
            "--release-root",
            str(release_root),
            "--forecast-service-base-url",
            "http://127.0.0.1:9000",
            "--forecast-service-token",
            "processor-token",
            "--service-token",
            "service-token",
            "--release-label",
            "release-a",
        ]
    )
    assert exit_code == 0
    assert (release_root / "release-a" / "release-metadata.generated.json").exists()
