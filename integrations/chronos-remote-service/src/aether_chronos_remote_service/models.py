"""Protocol models for the Chronos-style remote forecast skeleton."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ArtifactSelectorModel(BaseModel):
    kind: str
    family: str
    version: str


class QuantileModel(BaseModel):
    probability: float
    value: float


class ForecastPointModel(BaseModel):
    timestamp: str
    value: float
    quantiles: list[QuantileModel] = Field(default_factory=list)


class ForecastRequestModel(BaseModel):
    request_id: str
    binding_id: str
    as_of: str
    cadence_seconds: int
    horizon_steps: int
    artifact: ArtifactSelectorModel | None = None
    quantiles: list[float] = Field(default_factory=list)
    history_data: list[dict[str, Any]]
    forecast_data: list[dict[str, Any]]
    model_family: str
    model_name: str


class ArtifactResponseModel(BaseModel):
    kind: str
    family: str
    version: str
    artifact_digest: str


class ForecastResponseModel(BaseModel):
    artifact: ArtifactResponseModel | None = None
    predictions: list[ForecastPointModel]


class HealthResponseModel(BaseModel):
    ready: bool
    model_family: str
    model_name: str


class ErrorResponseModel(BaseModel):
    error: dict[str, str]
