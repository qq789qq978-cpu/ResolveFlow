param(
    [ValidateSet('Start', 'Stop', 'Status')]
    [string]$Action = 'Status'
)

# Isolated localhost demo fixtures. These are NOT the real .env credentials.
# Reuses the existing resolveflow:local image; never builds or removes volumes.
$ErrorActionPreference = 'Stop'
$projectDirectory = Split-Path -Parent $PSScriptRoot
$qaEnvironment = @{
    MODE = 'demo'
    APP_PORT = '8004'
    APP_API_KEY = 'qa-step11-operator'
    REVIEWER_API_KEY = 'qa-step11-reviewer'
    ADMIN_API_KEY = 'qa-step11-admin'
    POSTGRES_PASSWORD = 'qa-step11-database'
}
$previous = @{}
foreach ($name in $qaEnvironment.Keys) {
    $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}
Push-Location -LiteralPath $projectDirectory
try {
    foreach ($name in $qaEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $qaEnvironment[$name], 'Process')
    }
    switch ($Action) {
        'Start' { & docker compose -p resolveflow-qa-step11 up -d --wait --wait-timeout 180 }
        'Stop' { & docker compose -p resolveflow-qa-step11 stop }
        'Status' { & docker compose -p resolveflow-qa-step11 ps }
    }
    if ($LASTEXITCODE -ne 0) { throw "QA stack action failed: $Action" }
}
finally {
    Pop-Location
    foreach ($name in $qaEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process')
    }
}
