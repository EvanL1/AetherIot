"""Generate deployment presets that wire Aether forecast tasks to EMS bridge."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .registry_builder import build_ems_artifact_registry, load_ems_manifest, write_artifact_registry


def build_backend_bindings(
    *,
    forecast_service_base_url: str,
    api_token: str,
    load_backend_id: str = "load-ems-bridge",
    pv_backend_id: str = "pv-ems-bridge",
) -> list[dict[str, Any]]:
    base_url = _non_empty_string(forecast_service_base_url, "forecast_service_base_url")
    token = _non_empty_string(api_token, "api_token")
    return [
        {
            "task_id": "energy.site-load-forecast",
            "task_revision": 1,
            "binding_id": None,
            "backend_kind": "remote-http",
            "backend_config": {
                "base_url": base_url,
                "forecast_path": "/v1/foundation/forecast",
                "health_path": "/v1/foundation/health",
                "backend_id": _non_empty_string(load_backend_id, "load_backend_id"),
                "api_token": token,
                "verify_health_before_forecast": True,
                "supports_quantiles": False,
                "requires_explicit_artifact": True,
            },
        },
        {
            "task_id": "energy.site-pv-forecast",
            "task_revision": 1,
            "binding_id": None,
            "backend_kind": "remote-http",
            "backend_config": {
                "base_url": base_url,
                "forecast_path": "/v1/foundation/forecast",
                "health_path": "/v1/foundation/health",
                "backend_id": _non_empty_string(pv_backend_id, "pv_backend_id"),
                "api_token": token,
                "verify_health_before_forecast": True,
                "supports_quantiles": False,
                "requires_explicit_artifact": True,
            },
        },
    ]


def build_service_env(
    *,
    service_host: str,
    service_port: int,
    service_token: str,
    model_family: str,
    model_name: str,
    artifact_registry_path: str,
    runtime_entrypoint: str = "aether_chronos_remote_service.builtin_runtimes:naive_forecast_runtime",
) -> str:
    host = _non_empty_string(service_host, "service_host")
    token = _non_empty_string(service_token, "service_token")
    family = _non_empty_string(model_family, "model_family")
    name = _non_empty_string(model_name, "model_name")
    registry_path = _non_empty_string(artifact_registry_path, "artifact_registry_path")
    entrypoint = _non_empty_string(runtime_entrypoint, "runtime_entrypoint")
    if service_port <= 0:
        raise ValueError("service_port must be positive")
    lines = [
        f"AETHER_CHRONOS_SERVICE_HOST={host}",
        f"AETHER_CHRONOS_SERVICE_PORT={service_port}",
        f"AETHER_CHRONOS_SERVICE_TOKEN={token}",
        f"AETHER_CHRONOS_SERVICE_MODEL_FAMILY={family}",
        f"AETHER_CHRONOS_SERVICE_MODEL_NAME={name}",
        "AETHER_CHRONOS_SERVICE_EXECUTOR_BACKEND=python-entrypoint",
        f"AETHER_CHRONOS_SERVICE_RUNTIME_ENTRYPOINT={entrypoint}",
        f"AETHER_CHRONOS_SERVICE_ARTIFACT_REGISTRY={registry_path}",
    ]
    return "\n".join(lines) + "\n"


def write_json(data: Any, path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def build_release_metadata(
    manifest: Mapping[str, Any],
    *,
    forecast_service_base_url: str,
    registry_path: str,
    backend_bindings_path: str,
    service_env_path: str,
    source_manifest_path: str,
) -> dict[str, Any]:
    load_entry = _resolve_manifest_entry(manifest, "load")
    pv_entry = _resolve_manifest_entry(manifest, "pv")
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_manifest_path": _non_empty_string(source_manifest_path, "source_manifest_path"),
        "forecast_service_base_url": _non_empty_string(forecast_service_base_url, "forecast_service_base_url"),
        "artifacts": {
            "load": {
                "version": load_entry["version"],
                "artifact_digest": f"sha256:{load_entry['sha256']}",
            },
            "pv": {
                "version": pv_entry["version"],
                "artifact_digest": f"sha256:{pv_entry['sha256']}",
            },
        },
        "generated_files": {
            "artifact_registry": _non_empty_string(registry_path, "registry_path"),
            "backend_bindings": _non_empty_string(backend_bindings_path, "backend_bindings_path"),
            "forecast_service_env": _non_empty_string(service_env_path, "service_env_path"),
        },
        "rollback_hint": {
            "strategy": "restore previous generated preset directory and restart forecast service/processors",
            "required_files": [
                "artifact-registry.generated.json",
                "backend-bindings.generated.json",
                "forecast-service.generated.env",
            ],
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate Aether deployment preset files from EMS manifest.json",
    )
    parser.add_argument("--manifest", required=True, help="Path to EMS manifest.json")
    parser.add_argument("--output-dir", required=True, help="Directory for generated preset files")
    parser.add_argument("--forecast-service-base-url", required=True, help="Aether forecast service base URL for processor backends")
    parser.add_argument("--forecast-service-token", required=True, help="Token used by load/pv processors when calling the Aether forecast service")
    parser.add_argument("--service-host", default="127.0.0.1", help="Host where the Aether forecast service listens")
    parser.add_argument("--service-port", type=int, default=9000, help="Port where the Aether forecast service listens")
    parser.add_argument("--service-token", required=True, help="Bearer token enforced by the Aether forecast service")
    parser.add_argument("--model-family", default="chronos", help="Aether forecast service model_family")
    parser.add_argument("--model-name", default="chronos-tiny", help="Aether forecast service model_name")
    parser.add_argument(
        "--runtime-entrypoint",
        default="aether_chronos_remote_service.ems_serving_runtime:ems_serving_http_runtime",
        help="Runtime entrypoint for the forecast service executor",
    )
    parser.add_argument("--ems-timeout-seconds", type=float, default=30.0, help="Timeout when forecast service calls EMS serving")
    parser.add_argument("--load-family", default="site-load", help="Artifact family for load models")
    parser.add_argument("--pv-family", default="site-pv", help="Artifact family for PV models")
    args = parser.parse_args(argv)

    manifest = load_ems_manifest(args.manifest)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    registry_path = output_dir / "artifact-registry.generated.json"
    bindings_path = output_dir / "backend-bindings.generated.json"
    service_env_path = output_dir / "forecast-service.generated.env"
    release_metadata_path = output_dir / "release-metadata.generated.json"

    registry = build_ems_artifact_registry(
        manifest,
        base_url=args.forecast_service_base_url,
        model_family=args.model_family,
        model_name=args.model_name,
        runtime_entrypoint=args.runtime_entrypoint,
        timeout_seconds=args.ems_timeout_seconds,
        family_map={"load": args.load_family, "pv": args.pv_family},
    )
    write_artifact_registry(registry, registry_path)

    bindings = build_backend_bindings(
        forecast_service_base_url=args.forecast_service_base_url,
        api_token=args.forecast_service_token,
    )
    write_json(bindings, bindings_path)

    service_env = build_service_env(
        service_host=args.service_host,
        service_port=args.service_port,
        service_token=args.service_token,
        model_family=args.model_family,
        model_name=args.model_name,
        artifact_registry_path=str(registry_path),
        runtime_entrypoint=args.runtime_entrypoint,
    )
    service_env_path.write_text(service_env, encoding="utf-8")

    release_metadata = build_release_metadata(
        manifest,
        forecast_service_base_url=args.forecast_service_base_url,
        registry_path=str(registry_path),
        backend_bindings_path=str(bindings_path),
        service_env_path=str(service_env_path),
        source_manifest_path=args.manifest,
    )
    write_json(release_metadata, release_metadata_path)
    return 0


def _non_empty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _resolve_manifest_entry(manifest: Mapping[str, Any], model_type: str) -> Mapping[str, Any]:
    aliases = (model_type, f"{model_type}_forecast")
    for alias in aliases:
        entry = manifest.get(alias)
        if isinstance(entry, Mapping):
            return entry
    raise ValueError(f"EMS manifest does not contain {model_type} entry")


if __name__ == "__main__":
    raise SystemExit(main())
