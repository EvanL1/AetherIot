"""Build Aether artifact registry files from existing EMS model manifests."""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DEFAULT_FAMILY_MAP = {
    "load": "site-load",
    "pv": "site-pv",
}


def load_ems_manifest(path: str | Path) -> Mapping[str, Any]:
    decoded = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(decoded, Mapping):
        raise ValueError("EMS manifest root must be an object")
    return decoded


def build_ems_artifact_registry(
    manifest: Mapping[str, Any],
    *,
    base_url: str,
    model_family: str,
    model_name: str,
    runtime_entrypoint: str = "aether_chronos_remote_service.ems_serving_runtime:ems_serving_http_runtime",
    timeout_seconds: float = 30.0,
    family_map: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    normalized_base_url = _non_empty_string(base_url, "base_url")
    normalized_model_family = _non_empty_string(model_family, "model_family")
    normalized_model_name = _non_empty_string(model_name, "model_name")
    normalized_runtime_entrypoint = _non_empty_string(runtime_entrypoint, "runtime_entrypoint")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")

    active_family_map = dict(_DEFAULT_FAMILY_MAP)
    if family_map is not None:
        active_family_map.update(family_map)

    models: list[dict[str, Any]] = []
    for model_type in ("load", "pv"):
        entry = _resolve_manifest_entry(manifest, model_type)
        version = _non_empty_string(entry.get("version"), f"{model_type} version")
        digest = _manifest_digest(entry, model_type)
        models.append(
            {
                "kind": "model",
                "family": _non_empty_string(active_family_map.get(model_type), f"{model_type} family"),
                "version": version,
                "artifact_digest": digest,
                "model_family": normalized_model_family,
                "model_name": normalized_model_name,
                "runtime_entrypoint": normalized_runtime_entrypoint,
                "runtime_options": {
                    "base_url": normalized_base_url,
                    "model_type": model_type,
                    "timeout_seconds": float(timeout_seconds),
                },
            }
        )
    return {"models": models}


def write_artifact_registry(registry: Mapping[str, Any], path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(registry, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate Aether artifact registry JSON from EMS manifest.json",
    )
    parser.add_argument("--manifest", required=True, help="Path to EMS manifest.json")
    parser.add_argument("--output", required=True, help="Output path for Aether registry JSON")
    parser.add_argument("--base-url", required=True, help="Aether forecast service base URL")
    parser.add_argument("--model-family", default="chronos", help="Aether service model_family")
    parser.add_argument("--model-name", default="chronos-tiny", help="Aether service model_name")
    parser.add_argument(
        "--runtime-entrypoint",
        default="aether_chronos_remote_service.ems_serving_runtime:ems_serving_http_runtime",
        help="Python runtime entrypoint for executing EMS serving bridge",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=30.0,
        help="Per-request EMS serving timeout",
    )
    parser.add_argument("--load-family", default="site-load", help="Artifact family for load models")
    parser.add_argument("--pv-family", default="site-pv", help="Artifact family for PV models")
    args = parser.parse_args(argv)

    manifest = load_ems_manifest(args.manifest)
    registry = build_ems_artifact_registry(
        manifest,
        base_url=args.base_url,
        model_family=args.model_family,
        model_name=args.model_name,
        runtime_entrypoint=args.runtime_entrypoint,
        timeout_seconds=args.timeout_seconds,
        family_map={"load": args.load_family, "pv": args.pv_family},
    )
    write_artifact_registry(registry, args.output)
    return 0


def _resolve_manifest_entry(manifest: Mapping[str, Any], model_type: str) -> Mapping[str, Any]:
    aliases = (model_type, f"{model_type}_forecast")
    for alias in aliases:
        entry = manifest.get(alias)
        if isinstance(entry, Mapping):
            return entry
    raise ValueError(f"EMS manifest does not contain {model_type} entry")


def _manifest_digest(entry: Mapping[str, Any], model_type: str) -> str:
    raw_digest = entry.get("sha256")
    digest = _non_empty_string(raw_digest, f"{model_type} sha256")
    if _SHA256.fullmatch(digest) is None:
        raise ValueError(f"{model_type} sha256 must be 64 lowercase hex characters")
    return f"sha256:{digest}"


def _non_empty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
