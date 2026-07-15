"""Shared pluggable forecast backend contract and reusable backend adapters."""

from __future__ import annotations

import hmac
import json
import math
import re
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Protocol

from .artifacts import CommissionedArtifactBundle, EngineUnavailable, normalize_bundle_files

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class EngineQuantile:
    probability: float
    value: float


@dataclass(frozen=True, slots=True)
class EngineForecastPoint:
    timestamp: str
    value: float
    quantiles: tuple[EngineQuantile, ...] = ()


@dataclass(frozen=True, slots=True)
class ArtifactProvenance:
    kind: str
    family: str
    version: str
    artifact_digest: str


@dataclass(frozen=True, slots=True)
class EngineForecast:
    points: tuple[EngineForecastPoint, ...]
    artifact: ArtifactProvenance | None = None


@dataclass(frozen=True, slots=True)
class ForecastContext:
    request_id: str
    binding_id: str
    as_of: str
    cadence_seconds: int
    horizon_steps: int
    artifact_kind: str | None
    artifact_family: str | None
    artifact_version: str | None
    quantiles: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class ForecastBackendCapabilities:
    supports_quantiles: bool
    supports_remote_inference: bool
    requires_explicit_artifact: bool


@dataclass(frozen=True, slots=True)
class ForecastBackendDescriptor:
    backend_id: str
    backend_kind: str
    version: str
    capabilities: ForecastBackendCapabilities


class ForecastBackend(Protocol):
    """Pluggable forecast backend contract for governed task adapters."""

    def descriptor(self) -> ForecastBackendDescriptor: ...

    def is_ready(self) -> bool: ...

    def forecast(
        self,
        *,
        history_data: list[dict[str, Any]],
        forecast_data: list[dict[str, Any]],
        context: ForecastContext,
    ) -> EngineForecast: ...


class InferenceServiceLike(Protocol):
    """Structural type implemented by the existing Edge-Platform service."""

    def run_inference(
        self, pre_out: dict[str, Any], raw_event: dict[str, Any]
    ) -> Mapping[str, Any]: ...


class RemoteHttpTransport(Protocol):
    """Bounded transport used by the remote HTTP backend."""

    def post_json(
        self,
        *,
        url: str,
        payload: Mapping[str, Any],
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> Mapping[str, Any]: ...

    def get_json(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> Mapping[str, Any]: ...


class UrllibRemoteHttpTransport:
    """Default stdlib transport so the shared backend stays dependency-light."""

    @staticmethod
    def post_json(
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
                return UrllibRemoteHttpTransport._decode_json_body(response.read())
        except (urllib.error.URLError, TimeoutError):
            raise EngineUnavailable("remote forecast backend is unavailable") from None

    @staticmethod
    def get_json(
        *,
        url: str,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> Mapping[str, Any]:
        request = urllib.request.Request(
            url=url,
            headers={
                "Accept": "application/json",
                **dict(headers),
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                return UrllibRemoteHttpTransport._decode_json_body(response.read())
        except (urllib.error.URLError, TimeoutError):
            return {"ready": False}

    @staticmethod
    def _decode_json_body(body: bytes) -> Mapping[str, Any]:
        try:
            decoded = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise EngineUnavailable("remote forecast backend returned invalid JSON") from None
        if not isinstance(decoded, Mapping):
            raise EngineUnavailable("remote forecast backend returned an invalid body")
        return decoded


@dataclass(frozen=True, slots=True)
class RemoteHttpBackendConfig:
    base_url: str
    forecast_path: str = "/v1/forecast"
    health_path: str = "/v1/health"
    timeout_seconds: float = 10.0
    api_token: str | None = None
    backend_id: str = "remote-http-forecast"
    backend_kind: str = "remote-http"
    backend_version: str = "0.1.0"
    supports_quantiles: bool = True
    requires_explicit_artifact: bool = True
    verify_health_before_forecast: bool = False

    def __post_init__(self) -> None:
        if not self.base_url.startswith(("http://", "https://")):
            raise ValueError("remote backend base_url must be http or https")
        if self.timeout_seconds <= 0:
            raise ValueError("remote backend timeout must be positive")
        if not self.forecast_path.startswith("/"):
            raise ValueError("forecast_path must start with '/'")
        if not self.health_path.startswith("/"):
            raise ValueError("health_path must start with '/'")


class RemoteHttpForecastBackend:
    """Generic remote HTTP backend sample for pluggable model services."""

    def __init__(
        self,
        config: RemoteHttpBackendConfig,
        *,
        transport: RemoteHttpTransport | None = None,
    ) -> None:
        self._config = config
        self._transport = transport or UrllibRemoteHttpTransport()
        self._descriptor = ForecastBackendDescriptor(
            backend_id=config.backend_id,
            backend_kind=config.backend_kind,
            version=config.backend_version,
            capabilities=ForecastBackendCapabilities(
                supports_quantiles=config.supports_quantiles,
                supports_remote_inference=True,
                requires_explicit_artifact=config.requires_explicit_artifact,
            ),
        )

    def descriptor(self) -> ForecastBackendDescriptor:
        return self._descriptor

    def is_ready(self) -> bool:
        try:
            body = self._transport.get_json(
                url=self._join_url(self._config.health_path),
                headers=self._headers(),
                timeout_seconds=self._config.timeout_seconds,
            )
        except Exception:
            return False
        return body.get("ready") is True

    def forecast(
        self,
        *,
        history_data: list[dict[str, Any]],
        forecast_data: list[dict[str, Any]],
        context: ForecastContext,
    ) -> EngineForecast:
        if self._config.requires_explicit_artifact and (
            context.artifact_kind is None
            or context.artifact_family is None
            or context.artifact_version is None
        ):
            raise EngineUnavailable("remote backend requires an explicit artifact selector")
        if context.quantiles and not self._config.supports_quantiles:
            raise EngineUnavailable("remote backend does not support quantiles")
        if self._config.verify_health_before_forecast and not self.is_ready():
            raise EngineUnavailable("remote forecast backend is not ready")
        try:
            body = self._transport.post_json(
                url=self._join_url(self._config.forecast_path),
                payload=self._payload(
                    history_data=history_data,
                    forecast_data=forecast_data,
                    context=context,
                ),
                headers=self._headers(),
                timeout_seconds=self._config.timeout_seconds,
            )
        except EngineUnavailable:
            raise
        except Exception:
            raise EngineUnavailable("remote forecast backend is unavailable") from None
        return self._parse_remote_forecast(body, context)

    def _payload(
        self,
        *,
        history_data: list[dict[str, Any]],
        forecast_data: list[dict[str, Any]],
        context: ForecastContext,
    ) -> dict[str, Any]:
        return {
            "request_id": context.request_id,
            "binding_id": context.binding_id,
            "as_of": context.as_of,
            "cadence_seconds": context.cadence_seconds,
            "horizon_steps": context.horizon_steps,
            "artifact": {
                "kind": context.artifact_kind,
                "family": context.artifact_family,
                "version": context.artifact_version,
            }
            if context.artifact_kind is not None
            else None,
            "quantiles": list(context.quantiles),
            "history_data": history_data,
            "forecast_data": forecast_data,
        }

    def _headers(self) -> dict[str, str]:
        if self._config.api_token is None:
            return {}
        return {"Authorization": f"Bearer {self._config.api_token}"}

    def _join_url(self, path: str) -> str:
        return f"{self._config.base_url.rstrip('/')}{path}"

    @staticmethod
    def _parse_remote_forecast(
        body: Mapping[str, Any],
        context: ForecastContext,
    ) -> EngineForecast:
        predictions = body.get("predictions")
        if not isinstance(predictions, Sequence) or isinstance(predictions, (str, bytes)) or not predictions:
            raise EngineUnavailable("remote forecast backend predictions are missing")
        points: list[EngineForecastPoint] = []
        expected_probabilities = tuple(context.quantiles)
        for item in predictions:
            if not isinstance(item, Mapping):
                raise EngineUnavailable("remote forecast backend prediction is invalid")
            timestamp = item.get("timestamp", item.get("ts"))
            point_value = item.get("value")
            if not isinstance(timestamp, str):
                raise EngineUnavailable("remote forecast backend timestamp is invalid")
            if isinstance(point_value, bool) or not isinstance(point_value, (int, float)):
                raise EngineUnavailable("remote forecast backend value is invalid")
            if not math.isfinite(point_value):
                raise EngineUnavailable("remote forecast backend value is invalid")
            raw_quantiles = item.get("quantiles") or ()
            quantiles = RemoteHttpForecastBackend._parse_quantiles(raw_quantiles)
            probabilities = tuple(entry.probability for entry in quantiles)
            if probabilities != expected_probabilities:
                raise EngineUnavailable("remote forecast backend quantiles do not match request")
            points.append(
                EngineForecastPoint(
                    timestamp=timestamp,
                    value=float(point_value),
                    quantiles=quantiles,
                )
            )
        artifact = RemoteHttpForecastBackend._parse_remote_artifact(body.get("artifact"), context)
        return EngineForecast(points=tuple(points), artifact=artifact)

    @staticmethod
    def _parse_quantiles(value: Any) -> tuple[EngineQuantile, ...]:
        if not value:
            return ()
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            raise EngineUnavailable("remote forecast backend quantiles are invalid")
        parsed: list[EngineQuantile] = []
        last_probability = -1.0
        for item in value:
            if not isinstance(item, Mapping):
                raise EngineUnavailable("remote forecast backend quantile is invalid")
            probability = item.get("probability")
            quantile_value = item.get("value")
            if (
                isinstance(probability, bool)
                or not isinstance(probability, (int, float))
                or not 0.0 <= float(probability) <= 1.0
            ):
                raise EngineUnavailable("remote forecast backend quantile probability is invalid")
            if (
                isinstance(quantile_value, bool)
                or not isinstance(quantile_value, (int, float))
                or not math.isfinite(quantile_value)
            ):
                raise EngineUnavailable("remote forecast backend quantile value is invalid")
            if float(probability) <= last_probability:
                raise EngineUnavailable("remote forecast backend quantiles are not ordered")
            last_probability = float(probability)
            parsed.append(
                EngineQuantile(probability=float(probability), value=float(quantile_value))
            )
        return tuple(parsed)

    @staticmethod
    def _parse_remote_artifact(
        value: Any,
        context: ForecastContext,
    ) -> ArtifactProvenance | None:
        if value is None:
            return None
        if not isinstance(value, Mapping):
            raise EngineUnavailable("remote forecast backend artifact is invalid")
        kind = value.get("kind")
        family = value.get("family")
        version = value.get("version")
        digest = value.get("artifact_digest")
        if not all(isinstance(item, str) and item.strip() for item in (kind, family, version, digest)):
            raise EngineUnavailable("remote forecast backend artifact is invalid")
        if not _DIGEST.fullmatch(digest):
            raise EngineUnavailable("remote forecast backend artifact digest is invalid")
        if context.artifact_kind is not None and kind != context.artifact_kind:
            raise EngineUnavailable("remote forecast backend artifact kind does not match request")
        if context.artifact_family is not None and family != context.artifact_family:
            raise EngineUnavailable("remote forecast backend artifact family does not match request")
        if context.artifact_version is not None and version != context.artifact_version:
            raise EngineUnavailable("remote forecast backend artifact version does not match request")
        return ArtifactProvenance(
            kind=kind,
            family=family,
            version=version,
            artifact_digest=digest,
        )


class LegacyEdgePlatformForecastBackend:
    """Shared adapter for legacy Python inference services behind one contract."""

    _DEFAULT_VERSION: ClassVar[str] = "0.1.0"

    def __init__(
        self,
        inference_service: InferenceServiceLike,
        *,
        forecast_type: str,
        default_horizons: Mapping[tuple[int, int], str],
        backend_id: str,
        backend_kind: str = "legacy-edge-platform",
        artifact_bundles: Mapping[tuple[str, str, str], CommissionedArtifactBundle] | None = None,
        artifact_file_resolver: Callable[[ForecastContext], Mapping[str, str | Path]] | None = None,
        readiness_probe: Callable[[], bool] | None = None,
        horizon_names: Mapping[tuple[int, int], str] | None = None,
    ) -> None:
        self._inference_service = inference_service
        self._forecast_type = forecast_type
        self._backend_descriptor = ForecastBackendDescriptor(
            backend_id=backend_id,
            backend_kind=backend_kind,
            version=self._DEFAULT_VERSION,
            capabilities=ForecastBackendCapabilities(
                supports_quantiles=False,
                supports_remote_inference=False,
                requires_explicit_artifact=True,
            ),
        )
        self._horizon_names = dict(default_horizons)
        if horizon_names is not None:
            self._horizon_names.update(horizon_names)
        self._artifact_bundles = dict(artifact_bundles or {})
        self._artifact_file_resolver = artifact_file_resolver
        self._readiness_probe = readiness_probe
        if any(
            len(key) != 3
            or not all(isinstance(component, str) and component.strip() for component in key)
            or not isinstance(bundle, CommissionedArtifactBundle)
            for key, bundle in self._artifact_bundles.items()
        ):
            raise ValueError("artifact bundle policy is invalid")
        if artifact_file_resolver is not None and not callable(artifact_file_resolver):
            raise ValueError("artifact file resolver must be callable")
        if readiness_probe is not None and not callable(readiness_probe):
            raise ValueError("readiness probe must be callable")

    def descriptor(self) -> ForecastBackendDescriptor:
        return self._backend_descriptor

    def is_ready(self) -> bool:
        if (
            not self._artifact_bundles
            or self._artifact_file_resolver is None
            or self._readiness_probe is None
        ):
            return False
        try:
            for bundle in self._artifact_bundles.values():
                bundle.verify_unchanged()
            return self._readiness_probe() is True
        except Exception:
            return False

    def forecast(
        self,
        *,
        history_data: list[dict[str, Any]],
        forecast_data: list[dict[str, Any]],
        context: ForecastContext,
    ) -> EngineForecast:
        if context.artifact_version is None:
            raise EngineUnavailable("an explicit model version is required")
        if context.quantiles:
            raise EngineUnavailable("the legacy engine does not support quantiles")
        if context.artifact_kind is None or context.artifact_family is None:
            raise EngineUnavailable("an explicit model selector is required")
        artifact_key = (
            context.artifact_kind,
            context.artifact_family,
            context.artifact_version,
        )
        bundle = self._artifact_bundles.get(artifact_key)
        if bundle is None:
            raise EngineUnavailable("requested artifact bundle is not commissioned")
        bundle.verify_unchanged()

        horizon = self._horizon_names.get(
            (context.cadence_seconds, context.horizon_steps), "custom"
        )
        pre_out = {
            "ok": True,
            "plant_id": context.binding_id,
            "forecast_type": self._forecast_type,
            "horizon": horizon,
            "as_of": context.as_of,
            "data": {"history": history_data, "forecast": forecast_data},
        }
        raw_event = {
            "plant_id": context.binding_id,
            "forecast_type": self._forecast_type,
            "horizon": horizon,
            "as_of": context.as_of,
            "model_version": context.artifact_version,
        }
        self._verify_resolved_artifact_files(bundle, context)
        self._verify_legacy_readiness()

        try:
            response = self._inference_service.run_inference(pre_out, raw_event)
            body = self._parse_body(response)
            if body.get("model_version") != context.artifact_version:
                raise EngineUnavailable("edge platform model version does not match request")
            note = body.get("note")
            if isinstance(note, str) and "BASELINE_FALLBACK" in note.upper():
                raise EngineUnavailable("legacy fallback response rejected")
            points = self._parse_points(body.get("predictions"))
            artifact = self._parse_artifact(body, context, bundle)
        except EngineUnavailable:
            raise
        except Exception:
            raise EngineUnavailable("edge platform inference failed") from None
        return EngineForecast(points=points, artifact=artifact)

    def _verify_resolved_artifact_files(
        self,
        bundle: CommissionedArtifactBundle,
        context: ForecastContext,
    ) -> None:
        resolver = self._artifact_file_resolver
        if resolver is None:
            raise EngineUnavailable("legacy artifact path resolution is not commissioned")
        try:
            resolved_files = normalize_bundle_files(resolver(context))
        except Exception:
            raise EngineUnavailable("legacy artifact path resolution failed") from None
        if set(resolved_files) != set(bundle.files) or any(
            resolved_files[name].resolve(strict=True) != bundle.files[name].resolve(strict=True)
            for name in bundle.files
        ):
            raise EngineUnavailable("legacy artifact paths do not match commissioned bundle")

    def _verify_legacy_readiness(self) -> None:
        probe = self._readiness_probe
        if probe is None:
            raise EngineUnavailable("legacy readiness probe is not commissioned")
        try:
            ready = probe()
        except Exception:
            raise EngineUnavailable("legacy readiness probe failed") from None
        if ready is not True:
            raise EngineUnavailable("legacy inference service is not ready")

    @staticmethod
    def _parse_body(response: Mapping[str, Any]) -> Mapping[str, Any]:
        if response.get("statusCode") != 200:
            raise EngineUnavailable("edge platform inference failed")
        body: Any = response.get("body")
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError:
                raise EngineUnavailable("edge platform response is not JSON") from None
        if not isinstance(body, Mapping):
            raise EngineUnavailable("edge platform response body is invalid")
        return body

    @staticmethod
    def _parse_points(value: Any) -> tuple[EngineForecastPoint, ...]:
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or not value:
            raise EngineUnavailable("edge platform predictions are missing")
        points: list[EngineForecastPoint] = []
        for item in value:
            if not isinstance(item, Mapping):
                raise EngineUnavailable("edge platform prediction is invalid")
            timestamp = item.get("ts")
            point_value = item.get("value")
            if not isinstance(timestamp, str):
                raise EngineUnavailable("edge platform prediction timestamp is invalid")
            if isinstance(point_value, bool) or not isinstance(point_value, (int, float)):
                raise EngineUnavailable("edge platform prediction value is invalid")
            if not math.isfinite(point_value):
                raise EngineUnavailable("edge platform prediction value is invalid")
            points.append(EngineForecastPoint(timestamp=timestamp, value=float(point_value)))
        return tuple(points)

    @staticmethod
    def _parse_artifact(
        body: Mapping[str, Any],
        context: ForecastContext,
        bundle: CommissionedArtifactBundle,
    ) -> ArtifactProvenance:
        if (
            context.artifact_kind is None
            or context.artifact_family is None
            or context.artifact_version is None
        ):
            raise EngineUnavailable("an explicit model selector is required")
        reported_digest = body.get("artifact_digest")
        if reported_digest is not None and (
            not isinstance(reported_digest, str) or not _DIGEST.fullmatch(reported_digest)
        ):
            raise EngineUnavailable("edge platform artifact digest is invalid")
        if reported_digest is not None and not hmac.compare_digest(
            bundle.actual_digest, reported_digest
        ):
            raise EngineUnavailable("edge platform artifact digest does not match local bundle")
        return ArtifactProvenance(
            kind=context.artifact_kind,
            family=context.artifact_family,
            version=context.artifact_version,
            artifact_digest=bundle.actual_digest,
        )
