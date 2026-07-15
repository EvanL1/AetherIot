# Aether PV-Forecasting Processor

This opt-in Python package adds the request-driven Aether Data Processing v1
boundary to an existing PV forecasting Edge-Platform implementation.

It accepts a complete `ProcessingFrame` at `POST /v1/process` and never queries
InfluxDB, SHM, Aether history, or site configuration by itself.

## What it does

- validates the commissioned PV forecast contract
- accepts governed PV history plus a complete future weather frame
- passes only in-memory rows into the legacy inference service
- returns typed `produced`, `fallback`, or `unavailable` results
- keeps artifact proof, health, and bearer-auth behavior aligned with the
  load-forecasting compatibility adapter

## Commissioned contract

- task: `energy.site-pv-forecast`
- processor id: `pv-forecasting-edge`
- artifact family: `site-pv`
- cadence: `1800` seconds
- default history steps: `128`
- default max horizon steps: `144`
- output target: `pv`
- output sign convention: `positive_generation`

History features:

- `pv`
- `DHI`
- `DNI`
- `GHI`
- `Clearsky DHI`
- `Clearsky DNI`
- `Clearsky GHI`
- `Cloud Type`
- `Dew Point`
- `Solar Zenith Angle`
- `Fill Flag`
- `Surface Albedo`
- `Wind Speed`
- `Precipitable`
- `Wind Direction`
- `Relative Humidity`
- `Temperature`
- `Pressure`
- `Global Horizontal UV Irradiance 280-440`
- `Global Horizontal UV Irradiance 295-385`

Future covariates are the same weather features without historical `pv`.

## Usage sketch

```python
from aether_pv_forecasting_processor import (
    EdgePlatformInferenceServiceEngine,
    ProcessorPolicy,
    PvForecastProcessor,
    create_app,
)

engine = EdgePlatformInferenceServiceEngine(
    inference_service,
    artifact_bundles=commissioned_bundles,
    artifact_file_resolver=resolve_and_pin_artifact_files,
    readiness_probe=edge_platform_model_is_ready,
)
processor = PvForecastProcessor(
    engine,
    ProcessorPolicy(allow_persistence_fallback=False),
)
app = create_app(processor=processor)
```

## Status

This package is a compatibility adapter for integrating PV forecasting into
AetherIot's governed data-processing boundary. It does not make the default
Aether runtime depend on Python, a model service, or an external database.

