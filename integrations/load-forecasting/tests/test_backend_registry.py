from __future__ import annotations

import pytest

from forecast_runtime_core import (
    ForecastBackendRegistry,
    ForecastTaskBackendBinding,
    ForecastTaskBackendBindings,
    RemoteHttpForecastBackend,
    create_default_backend_registry,
    describe_backend,
)


class StubBackend:
    def __init__(self, backend_id: str) -> None:
        self._backend_id = backend_id

    def descriptor(self):
        return type(
            "Descriptor",
            (),
            {
                "backend_id": self._backend_id,
                "backend_kind": "stub",
                "version": "0.1.0",
                "capabilities": None,
            },
        )()

    def is_ready(self) -> bool:
        return True

    def forecast(self, *, history_data, forecast_data, context):
        raise NotImplementedError


def test_default_registry_creates_remote_http_backend() -> None:
    registry = create_default_backend_registry()

    backend = registry.create(
        backend_kind="remote-http",
        backend_config={"base_url": "https://forecast.example"},
    )

    assert isinstance(backend, RemoteHttpForecastBackend)
    descriptor = describe_backend(backend)
    assert descriptor.backend_kind == "remote-http"
    assert descriptor.capabilities.supports_remote_inference is True


def test_registry_rejects_duplicate_backend_kinds() -> None:
    registry = ForecastBackendRegistry()
    registry.register_factory(
        backend_kind="stub",
        description="stub backend",
        factory=lambda config: StubBackend(config["backend_id"]),
    )

    with pytest.raises(ValueError, match="already registered"):
        registry.register_factory(
            backend_kind="stub",
            description="duplicate stub backend",
            factory=lambda config: StubBackend(config["backend_id"]),
        )


def test_task_backend_bindings_prefer_exact_binding_then_default() -> None:
    bindings = ForecastTaskBackendBindings.from_mappings(
        [
            {
                "task_id": "energy.site-load-forecast",
                "task_revision": 1,
                "binding_id": None,
                "backend_kind": "remote-http",
                "backend_config": {"base_url": "https://default.example"},
            },
            {
                "task_id": "energy.site-load-forecast",
                "task_revision": 1,
                "binding_id": "site-a",
                "backend_kind": "remote-http",
                "backend_config": {"base_url": "https://site-a.example"},
            },
        ]
    )

    exact = bindings.resolve(
        task_id="energy.site-load-forecast",
        task_revision=1,
        binding_id="site-a",
    )
    fallback = bindings.resolve(
        task_id="energy.site-load-forecast",
        task_revision=1,
        binding_id="site-b",
    )

    assert exact.backend_config["base_url"] == "https://site-a.example"
    assert fallback.backend_config["base_url"] == "https://default.example"


def test_registry_creates_backend_for_resolved_binding() -> None:
    registry = create_default_backend_registry()
    binding = ForecastTaskBackendBinding(
        task_id="energy.site-load-forecast",
        task_revision=1,
        binding_id="site-a",
        backend_kind="remote-http",
        backend_config={
            "base_url": "https://site-a.example",
            "backend_id": "site-a-remote",
        },
    )

    backend = registry.create_for_binding(binding)
    descriptor = describe_backend(backend)

    assert descriptor.backend_id == "site-a-remote"


def test_bindings_fail_closed_when_nothing_matches() -> None:
    bindings = ForecastTaskBackendBindings.from_mappings(
        [
            {
                "task_id": "energy.site-pv-forecast",
                "task_revision": 1,
                "binding_id": None,
                "backend_kind": "remote-http",
                "backend_config": {"base_url": "https://pv.example"},
            }
        ]
    )

    with pytest.raises(ValueError, match="no backend binding matches"):
        bindings.resolve(
            task_id="energy.site-load-forecast",
            task_revision=1,
            binding_id="site-a",
        )
