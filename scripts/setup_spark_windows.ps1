$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$HadoopHome = Join-Path $ProjectRoot ".runtime\hadoop-3.3.5"
$HadoopBin = Join-Path $HadoopHome "bin"

New-Item -ItemType Directory -Force -Path $HadoopBin | Out-Null

$BaseUrl = "https://raw.githubusercontent.com/cdarlint/winutils/master/hadoop-3.3.5/bin"
Invoke-WebRequest -UseBasicParsing "$BaseUrl/winutils.exe" -OutFile (Join-Path $HadoopBin "winutils.exe")
Invoke-WebRequest -UseBasicParsing "$BaseUrl/hadoop.dll" -OutFile (Join-Path $HadoopBin "hadoop.dll")

Write-Host "Spark Windows helper installed at $HadoopHome"
Write-Host "The dashboard automatically selects an installed Java 17 JDK and this local Hadoop runtime."
