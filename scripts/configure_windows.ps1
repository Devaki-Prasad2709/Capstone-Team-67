param(
    [string]$SocialPath = "",
    [string]$DronePath = "",
    [string]$SatellitePath = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$EnvFile = Join-Path $ProjectRoot ".env"
$ExampleFile = Join-Path $ProjectRoot ".env.example"

if (-not (Test-Path $EnvFile)) {
    Copy-Item $ExampleFile $EnvFile
}

Write-Host "Windows IPv4 candidates (choose Wi-Fi for LAN or Tailscale 100.x for internet):"
Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" } |
    Select-Object InterfaceAlias, IPAddress |
    Format-Table -AutoSize

$KafkaHost = Read-Host "Enter the reachable host IPv4 to advertise (or localhost for one-system testing)"
if ([string]::IsNullOrWhiteSpace($KafkaHost)) { $KafkaHost = "localhost" }

function Set-EnvValue([string]$Name, [string]$Value) {
    $Content = Get-Content $EnvFile -Raw
    $Escaped = $Value.Replace("\\", "/")
    $Pattern = "(?m)^$([regex]::Escape($Name))=.*$"
    $Replacement = "$Name=$Escaped"
    if ($Content -match $Pattern) {
        $Content = [regex]::Replace($Content, $Pattern, $Replacement)
    } else {
        $Content += "`r`n$Replacement`r`n"
    }
    $Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($EnvFile, $Content, $Utf8NoBom)
}

Set-EnvValue "KAFKA_ADVERTISED_HOST" $KafkaHost
Set-EnvValue "KAFKA_BOOTSTRAP_SERVERS" "${KafkaHost}:9092"
Set-EnvValue "OBJECT_STORAGE_ENDPOINT_URL" "http://${KafkaHost}:9000"
if ($SocialPath) { Set-EnvValue "SOCIAL_DATASET_PATH" $SocialPath }
if ($DronePath) { Set-EnvValue "DRONE_DATASET_PATH" $DronePath }
if ($SatellitePath) { Set-EnvValue "SATELLITE_DATASET_PATH" $SatellitePath }

Write-Host "Configured $EnvFile"
Write-Host "Edit the three DATASET_PATH values if they do not match your D: drive folders."
Write-Host "Then run: docker compose --env-file .env up -d"
