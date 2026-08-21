$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$SparkSubmit = Join-Path $ProjectRoot ".venv\Scripts\spark-submit.cmd"
$HadoopHome = Join-Path $ProjectRoot ".runtime\hadoop-3.3.5"

if (-not (Test-Path $SparkSubmit)) {
    throw "PySpark is not installed in .venv. Activate the environment and run: pip install -r requirements.txt"
}
if (-not (Test-Path (Join-Path $HadoopHome "bin\winutils.exe"))) {
    throw "Windows Spark helper is missing. Run: .\scripts\setup_spark_windows.ps1"
}

$JavaCandidates = @()
$ExplicitJava = [Environment]::GetEnvironmentVariable("JAVA17_HOME")
if ($ExplicitJava) {
    $JavaCandidates += Get-Item -LiteralPath $ExplicitJava -ErrorAction SilentlyContinue
}
$JavaCandidates += Get-ChildItem "C:\Program Files\Eclipse Adoptium" -Directory -Filter "jdk-17*" -ErrorAction SilentlyContinue
$JavaCandidates += Get-ChildItem "C:\Program Files\Java" -Directory -Filter "jdk-17*" -ErrorAction SilentlyContinue
$JavaHome = $JavaCandidates | Where-Object { Test-Path (Join-Path $_.FullName "bin\java.exe") } | Sort-Object FullName -Descending | Select-Object -First 1
if (-not $JavaHome) {
    throw "Java 17 was not found. Install a Java 17 JDK or set JAVA17_HOME."
}

$env:JAVA_HOME = $JavaHome.FullName
$env:HADOOP_HOME = $HadoopHome
$env:PYSPARK_PYTHON = $Python
$env:PYSPARK_DRIVER_PYTHON = $Python
$env:PATH = "$(Join-Path $env:JAVA_HOME 'bin');$(Join-Path $env:HADOOP_HOME 'bin');$(Join-Path $ProjectRoot '.venv\Scripts');$env:PATH"

& $SparkSubmit --packages org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.5 spark/spark_streaming.py
