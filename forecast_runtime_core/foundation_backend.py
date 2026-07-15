"""Foundation-model forecast backend skeleton for future Chronos/TSFM-style adapters."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from .artifacts import EngineUnavailable
from .backend import (
    ArtifactProvenance,
    EngineForecast,
    EngineForecastPoint,
    EngineQuantile,
    ForecastBackendCapabilities,
    ForecastBackendDescriptor,
    ForecastContext,
    RemoteHttpTransport,
    UrllibRemoteHttpTransport,
)


class FoundationModelExecutor(Protocol):
    """Executor contract implemented by concrete foundation-model runtimes."""

    def is_ready(self) -> bool: ...

    def forecast(
        self,
        *,
        history_data: list[dict],
        forecast_data: list[dict],
        context: ForecastContext,
        model_family: str,
        model_name: str,
    ) -> EngineForecast: ...


@dataclass(frozen=True, slots=True)
class ChronosRemoteExecutorConfig:
    base_url: str
    model_family: str = "chronos"
    model_name: str = "chronos-tiny"
    forecast_path: str = "/v1/foundation/forecast"
    health_path: str = "/v1/foundation/health"
    timeout_seconds: float = 15.0
    api_token: str | None = None

    def __post_init__(self) -> None:
        if not self.base_url.startswith(("http://", "https://")):
            raise ValueError("chronos base_url must be http or https")
        if not self.model_family.strip():
            raise ValueError("chronos model_family must not be empty")
        if not self.model_name.strip():
            raise ValueError("chronos model_name must not be empty")
        if not self.forecast_path.startswith("/"):
            raise ValueError("chronos forecast_path must start with '/'")
        if not self.health_path.startswith("/"):
            raise ValueError("chronos health_path must start with '/'")
        if self.timeout_seconds <= 0:
            raise ValueError("chronos timeout must be positive")


class ChronosRemoteExecutor:
    """Remote HTTP executor sample for Chronos-style foundation model services."""

    def __init__(
        self,
        config: ChronosRemoteExecutorConfig,
        *,
        transport: RemoteHttpTransport | None = None,
    ) -> None:
        self._config = config
        self._transport = transport or UrllibRemoteHttpTransport()

    def is_ready(self) -> bool:
        try:
            body = self._transport.get_json(
                url=self._join_url(self._config.health_path),
                headers=self._headers(),
                timeout_seconds=self._config.timeout_seconds,
            )
        except Exception:
            return False
        if body.get("ready") is not True:
            return False
        if body.get("model_family") not in (None, self._config.model_family):
            return False
        if body.get("model_name") not in (None, self._config.model_name):
            return False
        return True

    def forecast(
        self,
        *,
        history_data: list[dict],
        forecast_data: list[dict],
        context: ForecastContext,
        model_family: str,
        model_name: str,
    ) -> EngineForecast:
        try:
            body = self._transport.post_json(
                url=self._join_url(self._config.forecast_path),
                payload={
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
                    "model_family": model_family,
                    "model_name": model_name,
                },
                headers=self._headers(),
                timeout_seconds=self._config.timeout_seconds,
            )
        except EngineUnavailable:
            raise
        except Exception:
            raise EngineUnavailable("chronos remote executor is unavailable") from None
        return self._parse_response(body, context)

    def _join_url(self, path: str) -> str:
        return f"{self._config.base_url.rstrip('/')}{path}"

    def _headers(self) -> dict[str, str]:
        if self._config.api_token is None:
            return {}
        return {"Authorization": f"Bearer {self._config.api_token}"}

    @staticmethod
    def _parse_response(
        body: dict[str, Any],
        context: ForecastContext,
    ) -> EngineForecast:
        predictions = body.get("predictions")
        if not isinstance(predictions, Sequence) or isinstance(predictions, (str, bytes)) or not predictions:
            raise EngineUnavailable("chronos remote executor predictions are missing")
        points: list[EngineForecastPoint] = []
        expected_quantiles = tuple(context.quantiles)
        for item in predictions:
            if not isinstance(item, dict):
                raise EngineUnavailable("chronos remote executor prediction is invalid")
            timestamp = item.get("timestamp", item.get("ts"))
            value = item.get("value")
            if not isinstance(timestamp, str):
                raise EngineUnavailable("chronos remote executor timestamp is invalid")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise EngineUnavailable("chronos remote executor value is invalid")
            quantiles = ChronosRemoteExecutor._parse_quantiles(item.get("quantiles") or ())
            if tuple(entry.probability for entry in quantiles) != expected_quantiles:
                raise EngineUnavailable("chronos remote executor quantiles do not match request")
            points.append(
                EngineForecastPoint(
                    timestamp=timestamp,
                    value=float(value),
                    quantiles=quantiles,
                )
            )
        artifact = ChronosRemoteExecutor._parse_artifact(body.get("artifact"))
        return EngineForecast(points=tuple(points), artifact=artifact)

    @staticmethod
    def _parse_quantiles(value: Any) -> tuple[EngineQuantile, ...]:
        if not value:
            return ()
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            raise EngineUnavailable("chronos remote executor quantiles are invalid")
        parsed: list[EngineQuantile] = []
        for item in value:
            if not isinstance(item, dict):
                raise EngineUnavailable("chronos remote executor quantile is invalid")
            probability = item.get("probability")
            quantile_value = item.get("value")
            if isinstance(probability, bool) or not isinstance(probability, (int, float)):
                raise EngineUnavailable("chronos remote executor quantile probability is invalid")
            if isinstance(quantile_value, bool) or not isinstance(quantile_value, (int, float)):
                raise EngineUnavailable("chronos remote executor quantile value is invalid")
            parsed.append(
                EngineQuantile(probability=float(probability), value=float(quantile_value))
            )
        return tuple(parsed)

    @staticmethod
    def _parse_artifact(value: Any) -> ArtifactProvenance | None:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise EngineUnavailable("chronos remote executor artifact is invalid")
        kind = value.get("kind")
        family = value.get("family")
        version = value.get("version")
        digest = value.get("artifact_digest")
        if not all(isinstance(item, str) and item.strip() for item in (kind, family, version, digest)):
            raise EngineUnavailable("chronos remote executor artifact is invalid")
        return ArtifactProvenance(
            kind=kind,
            family=family,
            version=version,
            artifact_digest=digest,
        )


@dataclass(frozen=True, slots=True)
class FoundationModelBackendConfig:
    backend_id: str
    backend_version: str = "0.1.0"
    backend_kind: str = "foundation-model"
    model_family: str = "timeseries-foundation"
    model_name: str = "unset"
    supports_quantiles: bool = True
    requires_explicit_artifact: bool = True
    supports_remote_inference: bool = True
    min_history_points: int = 1

    def __post_init__(self) -> None:
        if not self.backend_id.strip():
            raise ValueError("foundation backend_id must not be empty")
        if not self.model_family.strip():
            raise ValueError("foundation model_family must not be empty")
        if not self.model_name.strip():
            raise ValueError("foundation model_name must not be empty")
        if self.min_history_points <= 0:
            raise ValueError("foundation min_history_points must be positive")


class FoundationModelForecastBackend:
    """Thin governed wrapper for future Chronos/Moirai/TSFM executors."""

    def __init__(
        self,
        config: FoundationModelBackendConfig,
        *,
        executor: FoundationModelExecutor,
    ) -> None:
        self._config = config
        self._executor = executor
        self._descriptor = ForecastBackendDescriptor(
            backend_id=config.backend_id,
            backend_kind=config.backend_kind,
            version=config.backend_version,
            capabilities=ForecastBackendCapabilities(
                supports_quantiles=config.supports_quantiles,
                supports_remote_inference=config.supports_remote_inference,
                requires_explicit_artifact=config.requires_explicit_artifact,
            ),
        )

    def descriptor(self) -> ForecastBackendDescriptor:
        return self._descriptor

    def is_ready(self) -> bool:
        try:
            return self._executor.is_ready() is True
        except Exception:
            return False

    def forecast(
        self,
        *,
        history_data: list[dict],
        forecast_data: list[dict],
        context: ForecastContext,
    ) -> EngineForecast:
        if len(history_data) < self._config.min_history_points:
            raise EngineUnavailable("foundation backend history window is insufficient")
        if self._config.requires_explicit_artifact and (
            context.artifact_kind is None
            or context.artifact_family is None
            or context.artifact_version is None
        ):
            raise EngineUnavailable("foundation backend requires an explicit artifact selector")
        if context.quantiles and not self._config.supports_quantiles:
            raise EngineUnavailable("foundation backend does not support quantiles")
        try:
            result = self._executor.forecast(
                history_data=history_data,
                forecast_data=forecast_data,
                context=context,
                model_family=self._config.model_family,
                model_name=self._config.model_name,
            )
        except EngineUnavailable:
            raise
        except Exception:
            raise EngineUnavailable("foundation backend execution failed") from None
        self._validate_points(result.points, expected_count=context.horizon_steps)
        self._validate_artifact(result.artifact, context)
        return result

    @staticmethod
    def _validate_points(
        points: Sequence[EngineForecastPoint],
        *,
        expected_count: int,
    ) -> None:
        if len(points) != expected_count:
            raise EngineUnavailable("foundation backend returned the wrong point count")

    @staticmethod
    def _validate_artifact(
        artifact: ArtifactProvenance | None,
        context: ForecastContext,
    ) -> None:
        if artifact is None:
            return
        if context.artifact_kind is not None and artifact.kind != context.artifact_kind:
            raise EngineUnavailable("foundation backend artifact kind does not match request")
        if context.artifact_family is not None and artifact.family != context.artifact_family:
            raise EngineUnavailable("foundation backend artifact family does not match request")
        if context.artifact_version is not None and artifact.version != context.artifact_version:
            raise EngineUnavailable("foundation backend artifact version does not match request")
