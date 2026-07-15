param(
    [string]$ManifestPath = "C:\Panskai-work\Learn\07-项目\01-EMS\Forecast-Service\Server-Platform\MLOps\runtime\data\model_store\manifest.json",
    [string]$OutputDir = ".\integrations\chronos-remote-service\generated-preset",
    [string]$ForecastServiceBaseUrl = "http://127.0.0.1:9000",
    [string]$ForecastServiceToken = "",
    [string]$ServiceToken = "",
    [string]$ServiceHost = "127.0.0.1",
    [int]$ServicePort = 9000,
    [string]$ModelFamily = "chronos",
    [string]$ModelName = "chronos-tiny"
)

if ([string]::IsNullOrWhiteSpace($ForecastServiceToken)) {
    throw "ForecastServiceToken 不能为空"
}

if ([string]::IsNullOrWhiteSpace($ServiceToken)) {
    throw "ServiceToken 不能为空"
}

$env:PYTHONPATH = ".\integrations\chronos-remote-service\src"

python -m aether_chronos_remote_service.deployment_preset_builder `
  --manifest $ManifestPath `
  --output-dir $OutputDir `
  --forecast-service-base-url $ForecastServiceBaseUrl `
  --forecast-service-token $ForecastServiceToken `
  --service-host $ServiceHost `
  --service-port $ServicePort `
  --service-token $ServiceToken `
  --model-family $ModelFamily `
  --model-name $ModelName

if ($LASTEXITCODE -ne 0) {
    throw "deployment_preset_builder 执行失败"
}

Write-Host "已生成 EMS bridge 部署预置目录：" $OutputDir
Write-Host "包含：artifact-registry.generated.json / backend-bindings.generated.json / forecast-service.generated.env / release-metadata.generated.json"
