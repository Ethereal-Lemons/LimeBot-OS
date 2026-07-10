$ErrorActionPreference = "Stop"
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)

if (-not (Test-Path -LiteralPath ".env")) {
    Copy-Item -LiteralPath ".env.example" -Destination ".env"
    Write-Host "Created .env from .env.example"
}

if (-not (Test-Path -LiteralPath "limebot.json")) {
    $Content = @'
{
  "skills": {
    "enabled": []
  }
}
'@
    [System.IO.File]::WriteAllText(
        (Join-Path (Get-Location) "limebot.json"),
        $Content,
        $Utf8NoBom
    )
    Write-Host "Created limebot.json"
}

if (-not (Test-Path -LiteralPath "allowed_paths.txt")) {
    $Content = @'
# Paths available to LimeBot inside the container.
./persona
./logs
./temp
'@
    [System.IO.File]::WriteAllText(
        (Join-Path (Get-Location) "allowed_paths.txt"),
        $Content,
        $Utf8NoBom
    )
    Write-Host "Created allowed_paths.txt"
}

@(
    "data",
    "logs",
    "temp",
    "persona/memory",
    "persona/sessions",
    "skills",
    "bridge/session"
) | ForEach-Object {
    New-Item -ItemType Directory -Path $_ -Force | Out-Null
}

Write-Host "Docker runtime files are ready."
