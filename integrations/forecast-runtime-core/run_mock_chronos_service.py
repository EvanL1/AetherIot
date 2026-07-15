"""Run the local mock Chronos-style service for demos."""

from __future__ import annotations

import os

import uvicorn

from mock_chronos_service import create_app


def main() -> None:
    host = os.getenv("AETHER_MOCK_CHRONOS_HOST", "127.0.0.1")
    port = int(os.getenv("AETHER_MOCK_CHRONOS_PORT", "8999"))
    token = os.getenv("AETHER_MOCK_CHRONOS_TOKEN") or None
    model_family = os.getenv("AETHER_MOCK_CHRONOS_MODEL_FAMILY", "chronos")
    model_name = os.getenv("AETHER_MOCK_CHRONOS_MODEL_NAME", "chronos-tiny")

    app = create_app(
        expected_token=token,
        model_family=model_family,
        model_name=model_name,
    )
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
