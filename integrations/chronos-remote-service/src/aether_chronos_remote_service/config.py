"""Service configuration for the Chronos-style remote forecast skeleton."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ServiceConfig:
    host: str = "127.0.0.1"
    port: int = 9000
    token: str | None = None
    model_family: str = "chronos"
    model_name: str = "chronos-tiny"
    artifact_digest: str = "sha256:" + "3" * 64
    executor_backend: str = "placeholder"
    runtime_entrypoint: str | None = None
    artifact_registry_path: str | None = None

    @classmethod
    def from_env(cls) -> "ServiceConfig":
        return cls(
            host=os.getenv("AETHER_CHRONOS_SERVICE_HOST", "127.0.0.1"),
            port=int(os.getenv("AETHER_CHRONOS_SERVICE_PORT", "9000")),
            token=os.getenv("AETHER_CHRONOS_SERVICE_TOKEN") or None,
            model_family=os.getenv("AETHER_CHRONOS_SERVICE_MODEL_FAMILY", "chronos"),
            model_name=os.getenv("AETHER_CHRONOS_SERVICE_MODEL_NAME", "chronos-tiny"),
            artifact_digest=os.getenv(
                "AETHER_CHRONOS_SERVICE_ARTIFACT_DIGEST",
                "sha256:" + "3" * 64,
            ),
            executor_backend=os.getenv(
                "AETHER_CHRONOS_SERVICE_EXECUTOR_BACKEND",
                "placeholder",
            ),
            runtime_entrypoint=os.getenv("AETHER_CHRONOS_SERVICE_RUNTIME_ENTRYPOINT") or None,
            artifact_registry_path=os.getenv("AETHER_CHRONOS_SERVICE_ARTIFACT_REGISTRY") or None,
        )
