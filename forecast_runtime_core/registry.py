"""Backend registration and task-binding helpers for forecast runtime composition."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Callable

from .backend import (
    ForecastBackend,
    ForecastBackendDescriptor,
    RemoteHttpBackendConfig,
    RemoteHttpForecastBackend,
)


ForecastBackendFactory = Callable[[dict[str, Any]], ForecastBackend]


@dataclass(frozen=True, slots=True)
class ForecastBackendRegistration:
    backend_kind: str
    factory: ForecastBackendFactory
    description: str

    def __post_init__(self) -> None:
        if not self.backend_kind.strip():
            raise ValueError("backend_kind must not be empty")


@dataclass(frozen=True, slots=True)
class ForecastTaskBackendBinding:
    task_id: str
    task_revision: int
    binding_id: str | None
    backend_kind: str
    backend_config: dict[str, Any]

    def __post_init__(self) -> None:
        if not self.task_id.strip():
            raise ValueError("task_id must not be empty")
        if self.task_revision <= 0:
            raise ValueError("task_revision must be positive")
        if not self.backend_kind.strip():
            raise ValueError("backend_kind must not be empty")
        if self.binding_id is not None and not self.binding_id.strip():
            raise ValueError("binding_id must not be blank")

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "ForecastTaskBackendBinding":
        task_id = value.get("task_id")
        task_revision = value.get("task_revision")
        backend_kind = value.get("backend_kind")
        if not isinstance(task_id, str):
            raise ValueError("binding task_id must be a string")
        if not isinstance(task_revision, int):
            raise ValueError("binding task_revision must be an integer")
        if not isinstance(backend_kind, str):
            raise ValueError("binding backend_kind must be a string")
        binding_id = value.get("binding_id")
        if binding_id is not None and not isinstance(binding_id, str):
            raise ValueError("binding binding_id must be a string when present")
        backend_config = value.get("backend_config") or {}
        if not isinstance(backend_config, dict):
            raise ValueError("binding backend_config must be an object")
        return cls(
            task_id=task_id,
            task_revision=task_revision,
            binding_id=binding_id,
            backend_kind=backend_kind,
            backend_config=dict(backend_config),
        )


class ForecastBackendRegistry:
    """Registry that composes pluggable backend kinds into concrete instances."""

    def __init__(self) -> None:
        self._registrations: dict[str, ForecastBackendRegistration] = {}

    def register(self, registration: ForecastBackendRegistration) -> None:
        if registration.backend_kind in self._registrations:
            raise ValueError(f"backend kind already registered: {registration.backend_kind}")
        self._registrations[registration.backend_kind] = registration

    def register_factory(
        self,
        *,
        backend_kind: str,
        factory: ForecastBackendFactory,
        description: str,
    ) -> None:
        self.register(
            ForecastBackendRegistration(
                backend_kind=backend_kind,
                factory=factory,
                description=description,
            )
        )

    def known_backend_kinds(self) -> tuple[str, ...]:
        return tuple(sorted(self._registrations))

    def create(self, *, backend_kind: str, backend_config: dict[str, Any]) -> ForecastBackend:
        registration = self._registrations.get(backend_kind)
        if registration is None:
            raise ValueError(f"backend kind is not registered: {backend_kind}")
        return registration.factory(dict(backend_config))

    def create_for_binding(self, binding: ForecastTaskBackendBinding) -> ForecastBackend:
        return self.create(
            backend_kind=binding.backend_kind,
            backend_config=binding.backend_config,
        )


class ForecastTaskBackendBindings:
    """Resolution helper for task/binding-specific backend selection."""

    def __init__(self, bindings: list[ForecastTaskBackendBinding]) -> None:
        self._bindings = tuple(bindings)

    @classmethod
    def from_mappings(cls, values: list[dict[str, Any]]) -> "ForecastTaskBackendBindings":
        return cls([ForecastTaskBackendBinding.from_mapping(value) for value in values])

    @classmethod
    def from_json(cls, raw: str) -> "ForecastTaskBackendBindings":
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("task backend bindings JSON is invalid") from exc
        if not isinstance(decoded, list):
            raise ValueError("task backend bindings JSON must be an array")
        return cls.from_mappings(decoded)

    @classmethod
    def from_env(
        cls,
        env_name: str,
        *,
        required: bool = False,
    ) -> "ForecastTaskBackendBindings":
        raw = os.getenv(env_name)
        if raw is None or not raw.strip():
            if required:
                raise ValueError(f"{env_name} is required")
            return cls([])
        return cls.from_json(raw)

    def resolve(
        self,
        *,
        task_id: str,
        task_revision: int,
        binding_id: str | None,
    ) -> ForecastTaskBackendBinding:
        candidates = [
            binding
            for binding in self._bindings
            if binding.task_id == task_id and binding.task_revision == task_revision
        ]
        if binding_id is not None:
            for binding in candidates:
                if binding.binding_id == binding_id:
                    return binding
        for binding in candidates:
            if binding.binding_id is None:
                return binding
        raise ValueError("no backend binding matches the requested task/binding")


def create_default_backend_registry() -> ForecastBackendRegistry:
    registry = ForecastBackendRegistry()
    registry.register_factory(
        backend_kind="remote-http",
        description="Remote HTTP forecast backend sample",
        factory=lambda config: RemoteHttpForecastBackend(
            RemoteHttpBackendConfig(**config)
        ),
    )
    return registry


def describe_backend(backend: ForecastBackend) -> ForecastBackendDescriptor:
    return backend.descriptor()
