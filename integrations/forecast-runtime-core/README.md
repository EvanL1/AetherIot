# Forecast Runtime Core

> Status: partially implemented shared runtime slice.

This directory represents the shared runtime layer above concrete forecast
adapters such as:

- `integrations/load-forecasting`
- `integrations/pv-forecasting`

The goal is to let AetherIot act as a **generic forecast task platform** while
classic models, edge ONNX/RKNN runtimes, remote services, and time-series
foundation models act as **pluggable forecast backends**.

## Current layering

```text
pack-owned forecast task
        -> DataProcessingApplication
        -> forecast runtime core
        -> backend adapter
        -> artifact bundle
```

## What is already implemented

The shared Python slice under `forecast_runtime_core/` already owns:

- processor HTTP shell helpers;
- bearer auth and request-size guard behavior;
- bounded runner/concurrency helpers;
- common request digest and deadline validation;
- common produced/fallback/unavailable result shaping;
- common artifact bundle verification helpers;
- a shared pluggable forecast backend contract;
- a shared legacy Edge-Platform backend adapter;
- a remote HTTP backend sample;
- a foundation-model backend skeleton;
- a Chronos-style remote executor sample.

Current key modules:

- `forecast_runtime_core/http_api.py`
- `forecast_runtime_core/processor_common.py`
- `forecast_runtime_core/results.py`
- `forecast_runtime_core/artifacts.py`
- `forecast_runtime_core/backend.py`
- `forecast_runtime_core/foundation_backend.py`
- `forecast_runtime_core/registry.py`

Example companion service artifacts:

- `integrations/forecast-runtime-core/mock_chronos_service.py`
- `integrations/forecast-runtime-core/run_mock_chronos_service.py`

The concrete `load-forecasting` and `pv-forecasting` packages now keep only:

- task-specific feature contracts;
- task-specific result semantics;
- thin legacy backend wrappers (`forecast_type`, horizon presets, env names);
- composition helpers that bind a task/binding to a selected backend.

## Shared backend contract

The shared backend contract has this shape:

```text
ForecastBackend
  - descriptor()
  - is_ready()
  - forecast(history_data, forecast_data, context)
```

This means future backends can be added without cloning one full processor:

- legacy Python inference service backend;
- ONNX runtime backend;
- RKNN backend;
- remote foundation-model backend;
- local time-series foundation-model sidecar backend.

The task adapter still owns business semantics; the backend only owns how to
turn governed rows into a forecast result.

## What this layer must not own

This layer should not own:

- load/PV-specific feature names;
- domain task semantics;
- model-family-specific tensor/token layouts;
- framework-specific mandatory dependencies;
- direct Aether SHM/history/config access;
- device-control authority.

## Demo chain

The repository now includes a full local demo chain:

- `FoundationModelForecastBackend`
- `ChronosRemoteExecutor`
- protocol draft and request/response fixtures
- local mock Chronos-style service

That means the platform-to-remote-foundation-service path can already be
demonstrated locally before wiring a real Chronos service.

## Related documents

- `docs/adr/0013-generic-forecast-task-platform.md`
- `docs/plans/2026-07-14-forecast-runtime-core.md`
- `doc/AetherIot-通用预测任务平台与可插拔大模型后端设计.md`
- `doc/ChronosRemoteExecutor-远程服务协议草案.md`
- `doc/Mock-Chronos-本地演示步骤.md`
- `doc/AetherIot-预测后端落地现状与下一步汇报版.md`
