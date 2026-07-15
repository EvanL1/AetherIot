"""Entrypoint for running the Chronos-style remote forecast skeleton."""

from __future__ import annotations

import uvicorn

from .config import ServiceConfig
from .service import create_app


config = ServiceConfig.from_env()
app = create_app(config=config)


def main() -> None:
    uvicorn.run(app, host=config.host, port=config.port)


if __name__ == "__main__":
    main()
