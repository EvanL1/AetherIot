"""Forecast engine boundary and the legacy Edge-Platform in-memory adapter."""

from __future__ import annotations

from collections.abc import Mapping

from forecast_runtime_core import (
    ArtifactProvenance,
    CommissionedArtifactBundle,
    EngineForecast,
    EngineForecastPoint,
    EngineQuantile,
    EngineUnavailable,
    ForecastBackend as ForecastEngine,
    ForecastContext,
    InferenceServiceLike,
    LegacyEdgePlatformForecastBackend,
    compute_artifact_bundle_digest,
    load_commissioned_artifact_bundles_from_env as load_bundles_from_env,
)

ARTIFACT_BUNDLES_ENV = "AETHER_PV_FORECASTING_ARTIFACT_BUNDLES"


class EdgePlatformInferenceServiceEngine(LegacyEdgePlatformForecastBackend):
    """PV-forecast compatibility wrapper over the shared legacy backend."""

    _DEFAULT_HORIZONS: dict[tuple[int, int], str] = {
        (1800, 16): "ultra_short",
        (1800, 144): "short_term",
        (1800, 480): "medium_term",
    }

    def __init__(
        self,
        inference_service: InferenceServiceLike,
        horizon_names: Mapping[tuple[int, int], str] | None = None,
        artifact_bundles: Mapping[tuple[str, str, str], CommissionedArtifactBundle] | None = None,
        artifact_file_resolver=None,
        readiness_probe=None,
    ) -> None:
        super().__init__(
            inference_service,
            forecast_type="pv_forecast",
            default_horizons=self._DEFAULT_HORIZONS,
            backend_id="legacy-pv-edge-platform",
            horizon_names=horizon_names,
            artifact_bundles=artifact_bundles,
            artifact_file_resolver=artifact_file_resolver,
            readiness_probe=readiness_probe,
        )


def load_commissioned_artifact_bundles_from_env(
    *,
    required: bool = True,
) -> dict[tuple[str, str, str], CommissionedArtifactBundle]:
    return load_bundles_from_env(ARTIFACT_BUNDLES_ENV, required=required)
