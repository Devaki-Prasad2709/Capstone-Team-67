# Run PowerShell as Administrator before executing this script.
function Ensure-Rule(
    [string]$Name,
    [int]$Port,
    [string]$Profile,
    [string]$RemoteAddress = "Any"
) {
    $Existing = Get-NetFirewallRule -DisplayName $Name -ErrorAction SilentlyContinue
    if ($null -eq $Existing) {
        New-NetFirewallRule -DisplayName $Name -Direction Inbound -Protocol TCP `
            -LocalPort $Port -Action Allow -Profile $Profile -RemoteAddress $RemoteAddress | Out-Null
        Write-Host "Created: $Name"
    } else {
        Write-Host "Already exists: $Name"
    }
}

Ensure-Rule "Disaster Kafka TCP 9092 - Private LAN" 9092 "Private"
Ensure-Rule "Disaster Object Storage TCP 9000 - Private LAN" 9000 "Private"
Ensure-Rule "Disaster Kafka TCP 9092 - Tailscale" 9092 "Any" "100.64.0.0/10"
Ensure-Rule "Disaster Object Storage TCP 9000 - Tailscale" 9000 "Any" "100.64.0.0/10"
