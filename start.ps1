param(
    [switch]$LocalOnly
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

if (-not (Test-Path ".venv")) {
    python -m venv .venv
}

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
}

& ".\.venv\Scripts\python.exe" -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    throw "依赖安装失败。"
}

$bindHost = if ($LocalOnly) { "127.0.0.1" } else { "0.0.0.0" }

Write-Host "电脑访问：http://127.0.0.1:8000" -ForegroundColor Cyan
if (-not $LocalOnly) {
    $lanAddresses = [System.Net.Dns]::GetHostAddresses([System.Net.Dns]::GetHostName()) |
        Where-Object { $_.AddressFamily -eq "InterNetwork" -and $_.IPAddressToString -ne "127.0.0.1" } |
        Select-Object -ExpandProperty IPAddressToString
    foreach ($lanAddress in $lanAddresses) {
        Write-Host "手机访问（需要连接同一 Wi-Fi）：http://${lanAddress}:8000" -ForegroundColor Green
    }
    Write-Host "局域网模式不会自动公开到互联网。" -ForegroundColor DarkGray
}

& ".\.venv\Scripts\python.exe" -m uvicorn app.main:app --host $bindHost --port 8000 --reload

