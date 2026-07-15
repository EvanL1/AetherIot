"""PV forecast processing orchestration with fail-closed result semantics."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from itertools import pairwise
from typing import Any

from forecast_runtime_core import (
    ProcessorRequestError,
    ResultEnvelopeBuilder,
    ResultModelBundle,
    ResultSemantics,
    frame_error,
    validate_artifact_match,
    verify_deadline,
    verify_digest,
)

from .engine import ArtifactProvenance, EngineForecast, ForecastContext, ForecastEngine
from .models import (
    FORECAST_CONTRACT,
    FORECAST_OUTPUT_SCHEMA,
    RESULT_SCHEMA,
    ArtifactProvenanceModel,
    DataProcessingRequest,
    FallbackDescriptor,
    ForecastOutput,
    ForecastPoint,
    ProcessingResult,
    ProcessorDescriptor,
    QuantileValue,
    UnavailableDescriptor,
    compute_input_digest,
    format_utc_timestamp,
    normalize_utc_timestamp,
    parse_utc_timestamp,
)

UTC = timezone.utc


@dataclass(frozen=True, slots=True)
class ProcessorPolicy:
    processor_id: str = "pv-forecasting-edge"
    processor_version: str = "0.1.0"
    cadence_seconds: int = 1800
    history_steps: int = 128
    max_horizon_steps: int = 144
    task_revision: int = 1
    max_input_age_seconds: int = 1800
    produced_ttl_seconds: int = 3600
    fallback_ttl_seconds: int = 1800
    retry_after_seconds: int = 1800
    allow_persistence_fallback: bool = False

    def __post_init__(self) -> None:
        if any(
            value <= 0
            for value in (
                self.cadence_seconds,
                self.history_steps,
                self.max_horizon_steps,
                self.task_revision,
                self.max_input_age_seconds,
                self.produced_ttl_seconds,
                self.fallback_ttl_seconds,
                self.retry_after_seconds,
            )
        ):
            raise ValueError("processor policy limits must be positive")


_HISTORY_FEATURES = {
    "pv": "kW",
    "DHI": "W/m2",
    "DNI": "W/m2",
    "GHI": "W/m2",
    "Clearsky DHI": "W/m2",
    "Clearsky DNI": "W/m2",
    "Clearsky GHI": "W/m2",
    "Cloud Type": "1",
    "Dew Point": "Cel",
    "Solar Zenith Angle": "deg",
    "Fill Flag": "1",
    "Surface Albedo": "1",
    "Wind Speed": "m/s",
    "Precipitable": "cm",
    "Wind Direction": "deg",
    "Relative Humidity": "%",
    "Temperature": "Cel",
    "Pressure": "hPa",
    "Global Horizontal UV Irradiance 280-440": "W/m2",
    "Global Horizontal UV Irradiance 295-385": "W/m2",
}

_FUTURE_FEATURES = {
    name: unit for name, unit in _HISTORY_FEATURES.items() if name != "pv"
}

_NON_NEGATIVE_FEATURES = {
    "DHI",
    "DNI",
    "GHI",
    "Clearsky DHI",
    "Clearsky DNI",
    "Clearsky GHI",
    "Fill Flag",
    "Wind Speed",
    "Precipitable",
    "Global Horizontal UV Irradiance 280-440",
    "Global Horizontal UV Irradiance 295-385",
}
_ZERO_TO_ONE_FEATURES = {"Surface Albedo"}
_ZERO_TO_HUNDRED_FEATURES = {"Relative Humidity"}
_ANGLE_180_FEATURES = {"Solar Zenith Angle"}
_ANGLE_360_FEATURES = {"Wind Direction"}
_INTEGER_0_12_FEATURES = {"Cloud Type"}
_INTEGER_NON_NEGATIVE_FEATURES = {"Fill Flag"}


class PvForecastProcessor:
    """Validate a complete frame, invoke a model engine, and label every outcome."""

    def __init__(self, engine: ForecastEngine, policy: ProcessorPolicy | None = None) -> None:
        self._engine = engine
        self.policy = policy or ProcessorPolicy()
        self._results = ResultEnvelopeBuilder(
            models=ResultModelBundle(
                forecast_output_schema=FORECAST_OUTPUT_SCHEMA,
                result_schema=RESULT_SCHEMA,
                processor_contract=FORECAST_CONTRACT,
                artifact_model_type=ArtifactProvenanceModel,
                fallback_descriptor_type=FallbackDescriptor,
                forecast_output_type=ForecastOutput,
                forecast_point_type=ForecastPoint,
                processing_result_type=ProcessingResult,
                processor_descriptor_type=ProcessorDescriptor,
                quantile_value_type=QuantileValue,
                unavailable_descriptor_type=UnavailableDescriptor,
            ),
            semantics=ResultSemantics(
                target="pv",
                unit="kW",
                sign_convention="positive_generation",
                persistence_source_feature="pv",
            ),
            policy=self.policy,
            format_utc_timestamp=format_utc_timestamp,
            normalize_utc_timestamp=normalize_utc_timestamp,
        )

    def is_ready(self) -> bool:
        readiness = getattr(self._engine, "is_ready", None)
        if readiness is None:
            return True
        try:
            return readiness() is True
        except Exception:
            return False

    def process(self, request: DataProcessingRequest) -> ProcessingResult:
        verify_digest(request, compute_input_digest)
        verify_deadline(request, parse_utc_timestamp, now_fn=lambda: datetime.now(UTC))
        self._validate_pv_contract(request)
        history_data = self._segment_rows(
            request.frame.history.timestamps,
            request.frame.history.features,
        )
        future = request.frame.future_covariates
        if future is None:
            frame_error(request, "future covariates are required")
        forecast_data = self._segment_rows(
            future.timestamps,
            future.features,
            future=True,
        )
        context = ForecastContext(
            request_id=request.request_id,
            binding_id=request.binding.id,
            as_of=request.frame.as_of,
            cadence_seconds=request.frame.cadence_seconds,
            horizon_steps=request.options.horizon_steps,
            artifact_kind=request.artifact.kind if request.artifact else None,
            artifact_family=request.artifact.family if request.artifact else None,
            artifact_version=request.artifact.version if request.artifact else None,
            quantiles=tuple(request.options.quantiles or ()),
        )
        try:
            forecast = self._engine.forecast(
                history_data=history_data,
                forecast_data=forecast_data,
                context=context,
            )
            points = self._validate_engine_forecast(request, forecast)
            artifact = self._validate_artifact(request, forecast.artifact)
        except Exception:
            engine_succeeded = False
        else:
            engine_succeeded = True
        completed_at = verify_deadline(
            request,
            parse_utc_timestamp,
            now_fn=lambda: datetime.now(UTC),
        )
        if engine_succeeded:
            return self._results.produced(request, points, artifact, issued_at=completed_at)
        if self.policy.allow_persistence_fallback:
            return self._persistence_fallback(request, history_data, issued_at=completed_at)
        return self._results.unavailable(
            request,
            "MODEL_RUNTIME_UNAVAILABLE",
            retryable=True,
            issued_at=completed_at,
        )

    def _validate_pv_contract(self, request: DataProcessingRequest) -> None:
        if (
            request.task.id != "energy.site-pv-forecast"
            or request.task.revision != self.policy.task_revision
        ):
            frame_error(request, "task is not supported by this processor")
        if request.frame.cadence_seconds != self.policy.cadence_seconds:
            frame_error(request, "frame cadence is not supported")
        if request.artifact is not None and (
            request.artifact.kind != "model" or request.artifact.family != "site-pv"
        ):
            frame_error(request, "artifact selector is not supported")
        if request.frame.future_covariates is None:
            frame_error(request, "future covariates are required")
        if len(request.frame.history.timestamps) != self.policy.history_steps:
            frame_error(request, "history length does not match the commissioned task")
        if request.options.horizon_steps > self.policy.max_horizon_steps:
            frame_error(request, "forecast horizon exceeds the commissioned task")
        if request.frame.static_features:
            frame_error(request, "static features are not declared for this task revision")
        if request.frame.quality.missing_ratio != 0.0:
            frame_error(request, "required model inputs must not contain missing samples")
        if request.frame.quality.max_gap_seconds > 2 * request.frame.cadence_seconds:
            frame_error(request, "frame max gap exceeds the commissioned task")

        self._validate_feature_set(
            request,
            request.frame.history.features,
            _HISTORY_FEATURES,
            "history",
        )
        future = request.frame.future_covariates
        self._validate_feature_set(request, future.features, _FUTURE_FEATURES, "future_covariates")
        if len(future.timestamps) != request.options.horizon_steps:
            frame_error(request, "future horizon does not match horizon_steps")

        provenance = {(entry.segment, entry.feature): entry for entry in request.frame.provenance}
        as_of = parse_utc_timestamp(request.frame.as_of)
        if any(
            entry.segment == "future_covariates" and entry.issued_at is None
            for entry in request.frame.provenance
        ):
            frame_error(request, "future covariates require issue-time provenance")
        if any(
            as_of - parse_utc_timestamp(entry.watermark)
            > timedelta(seconds=self.policy.max_input_age_seconds)
            for entry in request.frame.provenance
            if entry.source_kind != "constant"
        ):
            frame_error(request, "a required input source is stale")
        for segment, features in (
            ("history", _HISTORY_FEATURES),
            ("future_covariates", _FUTURE_FEATURES),
        ):
            for feature in features:
                source_kind = provenance[(segment, feature)].source_kind
                expected_kind = "history" if segment == "history" else "covariate"
                if source_kind != expected_kind:
                    frame_error(
                        request,
                        "feature provenance does not match the commissioned task",
                    )

    def _validate_feature_set(
        self,
        request: DataProcessingRequest,
        features: dict[str, Any],
        expected: dict[str, str],
        segment: str,
    ) -> None:
        if set(features) != set(expected):
            frame_error(request, f"{segment} feature set does not match the task")
        for name, unit in expected.items():
            feature = features[name]
            if feature.value_type != "number" or feature.unit != unit:
                frame_error(request, f"{segment}.{name} has an invalid type or unit")
            if any(
                value is None or quality == "missing"
                for value, quality in zip(feature.values, feature.quality, strict=True)
            ):
                frame_error(request, f"{segment}.{name} contains a missing sample")
            values = feature.values
            if name in _NON_NEGATIVE_FEATURES and any(value < 0.0 for value in values):
                frame_error(request, f"{segment}.{name} must be non-negative")
            if name in _ZERO_TO_ONE_FEATURES and any(not 0.0 <= value <= 1.0 for value in values):
                frame_error(request, f"{segment}.{name} is outside [0, 1]")
            if name in _ZERO_TO_HUNDRED_FEATURES and any(
                not 0.0 <= value <= 100.0 for value in values
            ):
                frame_error(request, f"{segment}.{name} is outside [0, 100]")
            if name in _ANGLE_180_FEATURES and any(not 0.0 <= value <= 180.0 for value in values):
                frame_error(request, f"{segment}.{name} is outside [0, 180]")
            if name in _ANGLE_360_FEATURES and any(
                not 0.0 <= value < 360.0 for value in values
            ):
                frame_error(request, f"{segment}.{name} is outside [0, 360)")
            if name in _INTEGER_0_12_FEATURES and any(
                not 0.0 <= value <= 12.0 or not float(value).is_integer() for value in values
            ):
                frame_error(request, f"{segment}.{name} must be an integer inside [0, 12]")
            if name in _INTEGER_NON_NEGATIVE_FEATURES and any(
                value < 0.0 or not float(value).is_integer() for value in values
            ):
                frame_error(request, f"{segment}.{name} must be a non-negative integer")
            if name == "Pressure" and any(value <= 0.0 for value in values):
                frame_error(request, f"{segment}.{name} must be positive")

    @staticmethod
    def _segment_rows(timestamps, features, *, future: bool = False) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for index, timestamp in enumerate(timestamps):
            row: dict[str, Any] = {"datetime": timestamp}
            for name, feature in features.items():
                row[name] = feature.values[index]
            if future:
                row["pv"] = ""
            rows.append(row)
        return rows

    def _validate_engine_forecast(
        self, request: DataProcessingRequest, forecast: EngineForecast
    ) -> list[ForecastPoint]:
        future = request.frame.future_covariates
        if future is None or len(forecast.points) != request.options.horizon_steps:
            raise ValueError("engine returned the wrong point count")
        points: list[ForecastPoint] = []
        expected_quantiles = tuple(request.options.quantiles or ())
        for expected_timestamp, point in zip(future.timestamps, forecast.points, strict=True):
            if point.timestamp != expected_timestamp or not math.isfinite(point.value):
                raise ValueError("engine returned invalid point correlation")
            probabilities = tuple(quantile.probability for quantile in point.quantiles)
            if probabilities != expected_quantiles:
                raise ValueError("engine returned the wrong quantiles")
            values = [quantile.value for quantile in point.quantiles]
            if any(not math.isfinite(value) for value in values):
                raise ValueError("engine returned a non-finite quantile")
            if any(left > right for left, right in pairwise(values)):
                raise ValueError("engine returned crossing quantiles")
            points.append(
                self._results.point(
                    timestamp=point.timestamp,
                    value=point.value,
                    quantiles=(
                        [
                            QuantileValue(probability=item.probability, value=item.value)
                            for item in point.quantiles
                        ]
                        or None
                    ),
                )
            )
        return points

    @staticmethod
    def _validate_artifact(
        request: DataProcessingRequest, artifact: ArtifactProvenance | None
    ) -> ArtifactProvenanceModel | None:
        return validate_artifact_match(request, artifact, ArtifactProvenanceModel)

    def _persistence_fallback(
        self,
        request: DataProcessingRequest,
        history_data: list[dict[str, Any]],
        *,
        issued_at: datetime,
    ) -> ProcessingResult:
        last_value = history_data[-1]["pv"]
        if isinstance(last_value, bool) or not isinstance(last_value, (int, float)):
            return self._results.unavailable(
                request,
                "INSUFFICIENT_HISTORY",
                retryable=True,
                issued_at=issued_at,
            )
        future = request.frame.future_covariates
        if future is None:
            return self._results.unavailable(
                request,
                "INSUFFICIENT_HISTORY",
                retryable=True,
                issued_at=issued_at,
            )
        points = [
            self._results.point(
                timestamp=timestamp,
                value=last_value,
                quantiles=self._results.quantiles(request, last_value),
            )
            for timestamp in future.timestamps
        ]
        return self._results.persistence_fallback(request, points, issued_at=issued_at)
