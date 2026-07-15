"""Binding-driven composition helpers for load forecasting processors."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

from forecast_runtime_core import (
    CommissionedArtifactBundle,
    ForecastBackendRegistry,
    ForecastTaskBackendBindings,
    create_default_backend_registry,
)

from .engine import EdgePlatformInferenceServiceEngine, ForecastContext, InferenceServiceLike
from .processor import LoadForecastProcessor, ProcessorPolicy

BACKEND_BINDINGS_ENV = "AETHER_LOAD_FORECASTING_BACKEND_BINDINGS"
TASK_ID = "energy.site-load-forecast"


def load_backend_bindings_from_env(
    *,
    required: bool = False,
) -> ForecastTaskBackendBindings:
    return ForecastTaskBackendBindings.from_env(BACKEND_BINDINGS_ENV, required=required)


def create_backend_registry(
    *,
    legacy_inference_service: InferenceServiceLike | None = None,
    legacy_artifact_bundles: Mapping[tuple[str, str, str], CommissionedArtifactBundle] | None = None,
    legacy_artifact_file_resolver: Callable[
        [ForecastContext], Mapping[str, str | Path]
    ]
    | None = None,
    legacy_readiness_probe: Callable[[], bool] | None = None,
) -> ForecastBackendRegistry:
    registry = create_default_backend_registry()
    if legacy_inference_service is not None:
        registry.register_factory(
            backend_kind="legacy-edge-platform",
            description="Load legacy Edge-Platform backend",
            factory=lambda config: EdgePlatformInferenceServiceEngine(
                legacy_inference_service,
                horizon_names=config.get("horizon_names"),
                artifact_bundles=legacy_artifact_bundles,
                artifact_file_resolver=legacy_artifact_file_resolver,
                readiness_probe=legacy_readiness_probe,
            ),
        )
    return registry


def create_processor_from_bindings(
    *,
    binding_id: str,
    backend_bindings: ForecastTaskBackendBindings,
    policy: ProcessorPolicy | None = None,
    registry: ForecastBackendRegistry | None = None,
) -> LoadForecastProcessor:
    active_policy = policy or ProcessorPolicy()
    selected = backend_bindings.resolve(
        task_id=TASK_ID,
        task_revision=active_policy.task_revision,
        binding_id=binding_id,
    )
    active_registry = registry or create_default_backend_registry()
    backend = active_registry.create_for_binding(selected)
    return LoadForecastProcessor(engine=backend, policy=active_policy)


def create_processor_from_env(
    *,
    binding_id: str,
    policy: ProcessorPolicy | None = None,
    registry: ForecastBackendRegistry | None = None,
    required_bindings: bool = True,
) -> LoadForecastProcessor:
    return create_processor_from_bindings(
        binding_id=binding_id,
        backend_bindings=load_backend_bindings_from_env(required=required_bindings),
        policy=policy,
        registry=registry,
    )
