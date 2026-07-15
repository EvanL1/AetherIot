"""Shared result-envelope builders for forecast processors."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class ResultModelBundle:
    forecast_output_schema: str
    result_schema: str
    processor_contract: str
    artifact_model_type: type
    fallback_descriptor_type: type
    forecast_output_type: type
    forecast_point_type: type
    processing_result_type: type
    processor_descriptor_type: type
    quantile_value_type: type
    unavailable_descriptor_type: type


@dataclass(frozen=True, slots=True)
class ResultSemantics:
    target: str
    unit: str
    sign_convention: str
    persistence_source_feature: str


class ResultEnvelopeBuilder:
    def __init__(
        self,
        *,
        models: ResultModelBundle,
        semantics: ResultSemantics,
        policy: Any,
        format_utc_timestamp: Callable[[datetime], str],
        normalize_utc_timestamp: Callable[[str], str],
    ) -> None:
        self.models = models
        self.semantics = semantics
        self.policy = policy
        self.format_utc_timestamp = format_utc_timestamp
        self.normalize_utc_timestamp = normalize_utc_timestamp

    def forecast_output(self, request: Any, points: list[Any]):
        return self.models.forecast_output_type(
            schema=self.models.forecast_output_schema,
            kind="forecast",
            target=self.semantics.target,
            unit=self.semantics.unit,
            sign_convention=self.semantics.sign_convention,
            cadence_seconds=request.frame.cadence_seconds,
            timestamp_semantics="interval_end",
            points=points,
        )

    def base_result(self, request: Any) -> dict[str, Any]:
        return {
            "schema": self.models.result_schema,
            "request_id": request.request_id,
            "task": request.task,
            "binding": request.binding,
            "input_digest": request.input_digest,
            "input_watermark": self.normalize_utc_timestamp(request.frame.quality.input_watermark),
            "processor": self.models.processor_descriptor_type(
                id=self.policy.processor_id,
                version=self.policy.processor_version,
                contract=self.models.processor_contract,
            ),
        }

    def produced(self, request: Any, points: list[Any], artifact: Any, *, issued_at: datetime):
        return self.models.processing_result_type(
            **self.base_result(request),
            status="produced",
            issued_at=self.format_utc_timestamp(issued_at),
            expires_at=self.format_utc_timestamp(
                issued_at + timedelta(seconds=self.policy.produced_ttl_seconds)
            ),
            artifact=artifact,
            output=self.forecast_output(request, points),
            warnings=[],
        )

    def persistence_fallback(
        self,
        request: Any,
        points: list[Any],
        *,
        issued_at: datetime,
        warnings: list[str] | None = None,
    ):
        return self.models.processing_result_type(
            **self.base_result(request),
            status="fallback",
            issued_at=self.format_utc_timestamp(issued_at),
            expires_at=self.format_utc_timestamp(
                issued_at + timedelta(seconds=self.policy.fallback_ttl_seconds)
            ),
            fallback=self.models.fallback_descriptor_type(
                strategy="persistence",
                strategy_version="1",
                reason_code="MODEL_UNAVAILABLE",
                source_feature=self.semantics.persistence_source_feature,
                based_on_data_through=self.normalize_utc_timestamp(request.frame.history.timestamps[-1]),
            ),
            output=self.forecast_output(request, points),
            warnings=(warnings or []),
        )

    def unavailable(self, request: Any, reason_code: str, *, retryable: bool, issued_at: datetime):
        return self.models.processing_result_type(
            **self.base_result(request),
            status="unavailable",
            issued_at=self.format_utc_timestamp(issued_at),
            unavailable=self.models.unavailable_descriptor_type(
                reason_code=reason_code,
                retryable=retryable,
                retry_after_seconds=(self.policy.retry_after_seconds if retryable else None),
            ),
            warnings=[],
        )

    def point(self, *, timestamp: str, value: float, quantiles: list[Any] | None):
        return self.models.forecast_point_type(
            timestamp=self.normalize_utc_timestamp(timestamp),
            value=float(value),
            quantiles=(quantiles or None),
        )

    def quantiles(self, request: Any, value: float):
        return [
            self.models.quantile_value_type(probability=probability, value=float(value))
            for probability in request.options.quantiles or ()
        ]
