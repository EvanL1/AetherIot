"""Post-publish hook helpers for wiring EMS model publication into Aether presets."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from .deployment_preset_builder import main as deployment_preset_builder_main


def create_release_directory(base_dir: str | Path, *, label: str | None = None) -> Path:
    root = Path(base_dir)
    root.mkdir(parents=True, exist_ok=True)
    suffix = label or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    release_dir = root / suffix
    release_dir.mkdir(parents=True, exist_ok=True)
    return release_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run Aether preset generation as a post-publish hook after EMS manifest update.",
    )
    parser.add_argument("--manifest", required=True, help="Path to EMS manifest.json")
    parser.add_argument("--release-root", required=True, help="Directory where timestamped preset releases are stored")
    parser.add_argument("--forecast-service-base-url", required=True, help="Aether forecast service base URL for processors")
    parser.add_argument("--forecast-service-token", required=True, help="Token used by processors when calling forecast service")
    parser.add_argument("--service-token", required=True, help="Bearer token enforced by forecast service")
    parser.add_argument("--service-host", default="127.0.0.1")
    parser.add_argument("--service-port", type=int, default=9000)
    parser.add_argument("--model-family", default="chronos")
    parser.add_argument("--model-name", default="chronos-tiny")
    parser.add_argument("--ems-timeout-seconds", type=float, default=30.0)
    parser.add_argument("--release-label", default=None, help="Optional fixed label instead of UTC timestamp")
    args = parser.parse_args(argv)

    release_dir = create_release_directory(args.release_root, label=args.release_label)
    return deployment_preset_builder_main(
        [
            "--manifest",
            args.manifest,
            "--output-dir",
            str(release_dir),
            "--forecast-service-base-url",
            args.forecast_service_base_url,
            "--forecast-service-token",
            args.forecast_service_token,
            "--service-host",
            args.service_host,
            "--service-port",
            str(args.service_port),
            "--service-token",
            args.service_token,
            "--model-family",
            args.model_family,
            "--model-name",
            args.model_name,
            "--ems-timeout-seconds",
            str(args.ems_timeout_seconds),
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
