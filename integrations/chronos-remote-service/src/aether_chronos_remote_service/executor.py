"""Executor contract and pluggable runtime implementations."""

from __future__ import annotations

import importlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .models import (
    ArtifactResponseModel,
    ForecastPointModel,
    ForecastRequestModel,
    ForecastResponseModel,
    QuantileModel,
)

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


class ChronosExecutor(Protocol):
    def is_ready(self) -> bool: ...

    def forecast(
        self,
        *,
        request: ForecastRequestModel,
        artifact_digest: str,
    ) -> ForecastResponseModel: ...


@dataclass(frozen=True, slots=True)
class RuntimeArtifactBinding:
    kind: str
    family: str
    version: str
    artifact_digest: str
    model_family: str
    model_name: str
    runtime_entrypoint: str | None = None
    runtime_options: dict[str, Any] = field(default_factory=dict)


class RuntimeCallable(Protocol):
    def __call__(self, payload: dict[str, Any]) -> Any: ...


class ArtifactRegistry:
    def __init__(self, bindings: Sequence[RuntimeArtifactBinding]) -> None:
        self._bindings = {
            (binding.kind, binding.family, binding.version): binding for binding in bindings
        }

    @classmethod
    def from_path(cls, path: str | Path) -> "ArtifactRegistry":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("artifact registry root must be an object")
        models = raw.get("models")
        if not isinstance(models, list) or not models:
            raise ValueError("artifact registry must declare a non-empty models list")
        bindings: list[RuntimeArtifactBinding] = []
        seen: set[tuple[str, str, str]] = set()
        for item in models:
            if not isinstance(item, dict):
                raise ValueError("artifact registry model entry is invalid")
            binding = RuntimeArtifactBinding(
                kind=_non_empty_string(item.get("kind"), "artifact kind"),
                family=_non_empty_string(item.get("family"), "artifact family"),
                version=_non_empty_string(item.get("version"), "artifact version"),
                artifact_digest=_artifact_digest(item.get("artifact_digest")),
                model_family=_non_empty_string(item.get("model_family"), "model_family"),
                model_name=_non_empty_string(item.get("model_name"), "model_name"),
                runtime_entrypoint=_optional_non_empty_string(
                    item.get("runtime_entrypoint"),
                    "runtime_entrypoint",
                ),
                runtime_options=_runtime_options(item.get("runtime_options")),
            )
            key = (binding.kind, binding.family, binding.version)
            if key in seen:
                raise ValueError("artifact registry model selection is duplicated")
            seen.add(key)
            bindings.append(binding)
        return cls(bindings)

    def resolve(
        self,
        *,
        kind: str,
        family: str,
        version: str,
        model_family: str,
        model_name: str,
    ) -> RuntimeArtifactBinding:
        binding = self._bindings.get((kind, family, version))
        if binding is None:
            raise ValueError("requested artifact is not registered")
        if binding.model_family != model_family:
            raise ValueError("artifact model_family does not match service")
        if binding.model_name != model_name:
            raise ValueError("artifact model_name does not match service")
        return binding


class PlaceholderChronosExecutor:
    def is_ready(self) -> bool:
        return True

    def forecast(
        self,
        *,
        request: ForecastRequestModel,
        artifact_digest: str,
    ) -> ForecastResponseModel:
        base_value = self._last_numeric_value(request.history_data[-1])
        predictions = [
            ForecastPointModel(
                timestamp=self._extract_timestamp(row),
                value=base_value + index + 1.0,
                quantiles=[
                    QuantileModel(probability=probability, value=base_value + index + 1.0)
                    for probability in request.quantiles
                ],
            )
            for index, row in enumerate(request.forecast_data)
        ]
        artifact = None
        if request.artifact is not None:
            artifact = ArtifactResponseModel(
                kind=request.artifact.kind,
                family=request.artifact.family,
                version=request.artifact.version,
                artifact_digest=artifact_digest,
            )
        return ForecastResponseModel(artifact=artifact, predictions=predictions)

    @staticmethod
    def _last_numeric_value(row: dict[str, Any]) -> float:
        for key, value in reversed(tuple(row.items())):
            if key == "datetime":
                continue
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)):
                return float(value)
        raise ValueError("no numeric history value found")

    @staticmethod
    def _extract_timestamp(row: dict[str, Any]) -> str:
        timestamp = row.get("datetime")
        if not isinstance(timestamp, str):
            raise ValueError("forecast_data datetime is invalid")
        return timestamp


class PythonEntrypointChronosExecutor:
    def __init__(
        self,
        *,
        default_runtime_entrypoint: str,
        default_artifact_digest: str,
        model_family: str,
        model_name: str,
        artifact_registry: ArtifactRegistry | None = None,
    ) -> None:
        self._default_runtime_entrypoint = _non_empty_string(
            default_runtime_entrypoint,
            "runtime_entrypoint",
        )
        self._default_artifact_digest = _artifact_digest(default_artifact_digest)
        self._model_family = _non_empty_string(model_family, "model_family")
        self._model_name = _non_empty_string(model_name, "model_name")
        self._artifact_registry = artifact_registry

    def is_ready(self) -> bool:
        try:
            self._resolve_runtime(self._default_runtime_entrypoint)
            return True
        except Exception:
            return False

    def forecast(
        self,
        *,
        request: ForecastRequestModel,
        artifact_digest: str,
    ) -> ForecastResponseModel:
        runtime_binding = self._resolve_binding(request, artifact_digest)
        runtime = self._resolve_runtime(runtime_binding.runtime_entrypoint)
        payload = {
            "request": request.model_dump(mode="python"),
            "artifact": {
                "kind": runtime_binding.kind,
                "family": runtime_binding.family,
                "version": runtime_binding.version,
                "artifact_digest": runtime_binding.artifact_digest,
            }
            if request.artifact is not None
            else None,
            "model": {
                "model_family": self._model_family,
                "model_name": self._model_name,
            },
            "runtime_options": dict(runtime_binding.runtime_options),
        }
        raw = runtime(payload)
        return self._parse_runtime_response(raw, request, runtime_binding)

    def _resolve_binding(
        self,
        request: ForecastRequestModel,
        artifact_digest: str,
    ) -> RuntimeArtifactBinding:
        if request.artifact is None:
            return RuntimeArtifactBinding(
                kind="model",
                family="unregistered",
                version="dynamic",
                artifact_digest=_artifact_digest(artifact_digest),
                model_family=self._model_family,
                model_name=self._model_name,
                runtime_entrypoint=self._default_runtime_entrypoint,
            )
        if self._artifact_registry is None:
            return RuntimeArtifactBinding(
                kind=request.artifact.kind,
                family=request.artifact.family,
                version=request.artifact.version,
                artifact_digest=_artifact_digest(artifact_digest),
                model_family=self._model_family,
                model_name=self._model_name,
                runtime_entrypoint=self._default_runtime_entrypoint,
            )
        resolved = self._artifact_registry.resolve(
            kind=request.artifact.kind,
            family=request.artifact.family,
            version=request.artifact.version,
            model_family=self._model_family,
            model_name=self._model_name,
        )
        runtime_entrypoint = resolved.runtime_entrypoint or self._default_runtime_entrypoint
        return RuntimeArtifactBinding(
            kind=resolved.kind,
            family=resolved.family,
            version=resolved.version,
            artifact_digest=resolved.artifact_digest,
            model_family=resolved.model_family,
            model_name=resolved.model_name,
            runtime_entrypoint=runtime_entrypoint,
            runtime_options=dict(resolved.runtime_options),
        )

    @staticmethod
    def _resolve_runtime(entrypoint: str) -> RuntimeCallable:
        module_name, separator, attribute_name = entrypoint.partition(":")
        if not separator or not module_name or not attribute_name:
            raise ValueError("runtime_entrypoint must be module:attribute")
        module = importlib.import_module(module_name)
        runtime = getattr(module, attribute_name, None)
        if runtime is None or not callable(runtime):
            raise ValueError("runtime_entrypoint target is not callable")
        return runtime

    @staticmethod
    def _parse_runtime_response(
        raw: Any,
        request: ForecastRequestModel,
        binding: RuntimeArtifactBinding,
    ) -> ForecastResponseModel:
        if not isinstance(raw, dict):
            raise ValueError("runtime response must be an object")
        predictions = raw.get("predictions")
        if not isinstance(predictions, Sequence) or isinstance(predictions, (str, bytes)):
            raise ValueError("runtime predictions are invalid")
        if len(predictions) != request.horizon_steps:
            raise ValueError("runtime returned the wrong point count")
        parsed_predictions: list[ForecastPointModel] = []
        for item in predictions:
            if not isinstance(item, dict):
                raise ValueError("runtime prediction is invalid")
            timestamp = item.get("timestamp", item.get("ts"))
            value = item.get("value")
            if not isinstance(timestamp, str):
                raise ValueError("runtime prediction timestamp is invalid")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("runtime prediction value is invalid")
            parsed_quantiles = PythonEntrypointChronosExecutor._parse_quantiles(
                item.get("quantiles") or (),
                request.quantiles,
            )
            parsed_predictions.append(
                ForecastPointModel(
                    timestamp=timestamp,
                    value=float(value),
                    quantiles=parsed_quantiles,
                )
            )

        raw_artifact = raw.get("artifact")
        if request.artifact is None:
            artifact = None
        else:
            artifact = PythonEntrypointChronosExecutor._parse_artifact(raw_artifact, binding)
        return ForecastResponseModel(artifact=artifact, predictions=parsed_predictions)

    @staticmethod
    def _parse_quantiles(
        raw: Any,
        expected_probabilities: Sequence[float],
    ) -> list[QuantileModel]:
        if not raw:
            if expected_probabilities:
                raise ValueError("runtime quantiles do not match request")
            return []
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
            raise ValueError("runtime quantiles are invalid")
        parsed: list[QuantileModel] = []
        for item in raw:
            if not isinstance(item, dict):
                raise ValueError("runtime quantile is invalid")
            probability = item.get("probability")
            value = item.get("value")
            if isinstance(probability, bool) or not isinstance(probability, (int, float)):
                raise ValueError("runtime quantile probability is invalid")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("runtime quantile value is invalid")
            parsed.append(QuantileModel(probability=float(probability), value=float(value)))
        if tuple(item.probability for item in parsed) != tuple(float(x) for x in expected_probabilities):
            raise ValueError("runtime quantiles do not match request")
        return parsed

    @staticmethod
    def _parse_artifact(
        raw: Any,
        binding: RuntimeArtifactBinding,
    ) -> ArtifactResponseModel:
        if not isinstance(raw, dict):
            raise ValueError("runtime artifact is invalid")
        kind = _non_empty_string(raw.get("kind"), "artifact kind")
        family = _non_empty_string(raw.get("family"), "artifact family")
        version = _non_empty_string(raw.get("version"), "artifact version")
        digest = _artifact_digest(raw.get("artifact_digest"))
        if (
            kind != binding.kind
            or family != binding.family
            or version != binding.version
            or digest != binding.artifact_digest
        ):
            raise ValueError("runtime artifact does not match registered metadata")
        return ArtifactResponseModel(
            kind=kind,
            family=family,
            version=version,
            artifact_digest=digest,
        )


def create_executor_from_config(config: Any) -> ChronosExecutor:
    backend = getattr(config, "executor_backend", "placeholder")
    if backend == "placeholder":
        return PlaceholderChronosExecutor()
    if backend == "python-entrypoint":
        runtime_entrypoint = getattr(config, "runtime_entrypoint", None)
        if runtime_entrypoint is None:
            raise ValueError("python-entrypoint executor requires runtime_entrypoint")
        artifact_registry_path = getattr(config, "artifact_registry_path", None)
        artifact_registry = None
        if artifact_registry_path is not None:
            artifact_registry = ArtifactRegistry.from_path(artifact_registry_path)
        return PythonEntrypointChronosExecutor(
            default_runtime_entrypoint=runtime_entrypoint,
            default_artifact_digest=getattr(config, "artifact_digest"),
            model_family=getattr(config, "model_family"),
            model_name=getattr(config, "model_name"),
            artifact_registry=artifact_registry,
        )
    raise ValueError(f"unsupported executor backend: {backend}")


def _non_empty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _optional_non_empty_string(value: Any, label: str) -> str | None:
    if value is None:
        return None
    return _non_empty_string(value, label)


def _artifact_digest(value: Any) -> str:
    digest = _non_empty_string(value, "artifact_digest")
    if _DIGEST.fullmatch(digest) is None:
        raise ValueError("artifact_digest must be sha256:<64 hex>")
    return digest


def _runtime_options(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("runtime_options must be an object")
    return dict(value)
