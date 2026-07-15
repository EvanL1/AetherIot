# Aether Chronos Remote Service

This integration is a minimal FastAPI service skeleton that matches the
`ChronosRemoteExecutor` protocol draft used by AetherIot.

It is intentionally small and safe:

- no direct access to Aether SHM, history, or config;
- no hidden site lookup by `binding_id`;
- no mandatory GPU or model runtime dependency;
- one pluggable executor layer that can host a placeholder runtime today and a
  real Chronos, TSFM, or similar time-series foundation model backend later.

## Current endpoints

- `GET /v1/foundation/health`
- `POST /v1/foundation/forecast`

## Start locally

```powershell
$env:AETHER_CHRONOS_SERVICE_TOKEN="secret-token"
python .\integrations\chronos-remote-service\src\aether_chronos_remote_service\app.py
```

Default bind:

- `http://127.0.0.1:9000`

## Executor backends

The service now supports two executor wiring modes:

- `placeholder`: in-process stub executor for protocol and smoke tests.
- `python-entrypoint`: load a Python callable by `module:attribute` and use it
  as the real forecasting runtime adapter.

Relevant env vars:

```powershell
$env:AETHER_CHRONOS_SERVICE_EXECUTOR_BACKEND="python-entrypoint"
$env:AETHER_CHRONOS_SERVICE_RUNTIME_ENTRYPOINT="aether_chronos_remote_service.builtin_runtimes:naive_forecast_runtime"
$env:AETHER_CHRONOS_SERVICE_ARTIFACT_REGISTRY=".\integrations\chronos-remote-service\artifact-registry.example.json"
```

When an artifact registry is configured, the request artifact selector must be
registered in that file, and the runtime must return matching artifact
metadata. This gives AetherIot a first governed path for:

- model registration;
- artifact digest binding;
- runtime entrypoint selection per artifact version.

## Bridge existing EMS models

If you already have the legacy EMS model serving API running, you can bridge it
into AetherIot without changing the Aether HTTP layer.

Use this runtime entrypoint:

```powershell
aether_chronos_remote_service.ems_serving_runtime:ems_serving_http_runtime
```

Sample registry:

- `.\integrations\chronos-remote-service\artifact-registry.ems-serving.example.json`

You can also generate that registry from the existing EMS `manifest.json`
instead of hand-editing versions and digests:

```powershell
$env:PYTHONPATH=".\integrations\chronos-remote-service\src"
python -m aether_chronos_remote_service.registry_builder `
  --manifest "C:\path\to\Forecast-Service\Server-Platform\MLOps\runtime\data\model_store\manifest.json" `
  --output ".\integrations\chronos-remote-service\artifact-registry.generated.json" `
  --base-url "http://127.0.0.1:8010"
```

The generated registry keeps:

- `load` -> `site-load`
- `pv` -> `site-pv`
- ONNX `sha256` from the EMS manifest -> Aether `artifact_digest`
- runtime entrypoint -> `ems_serving_http_runtime`

To go one step further and generate a small deployment preset directory
(`artifact registry + backend bindings + forecast service env`):

```powershell
$env:PYTHONPATH=".\integrations\chronos-remote-service\src"
python -m aether_chronos_remote_service.deployment_preset_builder `
  --manifest "C:\path\to\manifest.json" `
  --output-dir ".\integrations\chronos-remote-service\generated-preset" `
  --forecast-service-base-url "http://127.0.0.1:9000" `
  --forecast-service-token "replace-me" `
  --service-token "replace-me"
```

Generated files:

- `artifact-registry.generated.json`
- `backend-bindings.generated.json`
- `forecast-service.generated.env`
- `release-metadata.generated.json`

There is also a local PowerShell wrapper you can run directly:

```powershell
.\integrations\chronos-remote-service\generate_ems_bridge_preset.ps1 `
  -ForecastServiceToken "replace-me" `
  -ServiceToken "replace-me"
```

By default it points at the EMS manifest path already discussed in the
workspace:

- `C:\Panskai-work\Learn\07-项目\01-EMS\Forecast-Service\Server-Platform\MLOps\runtime\data\model_store\manifest.json`

And for a post-publish / Airflow-style handoff, you can generate a timestamped
release directory in one step:

```powershell
$env:PYTHONPATH=".\integrations\chronos-remote-service\src"
python -m aether_chronos_remote_service.ems_publish_hook `
  --manifest "C:\path\to\manifest.json" `
  --release-root ".\integrations\chronos-remote-service\releases" `
  --forecast-service-base-url "http://127.0.0.1:9000" `
  --forecast-service-token "replace-me" `
  --service-token "replace-me"
```

This bridge currently follows a strict fail-closed policy:

- it only supports deterministic forecasts for now;
- quantile requests are rejected;
- it requires request horizon and serving horizon to match exactly;
- it keeps artifact metadata under AetherIot registration control.

## Important note

The built-in `naive_forecast_runtime` is still only a demo runtime. The key
change is that the service now has a real adapter boundary for swapping in an
actual model callable without rewriting the HTTP layer.
