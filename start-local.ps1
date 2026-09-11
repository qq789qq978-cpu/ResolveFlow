$ErrorActionPreference = 'Stop'
$projectDirectory = $PSScriptRoot
$projectPython = Join-Path $projectDirectory '.venv\Scripts\python.exe'
$pgControl = Join-Path $projectDirectory 'work\postgres\pgsql\bin\pg_ctl.exe'
$pgData = Join-Path $projectDirectory 'work\pgdata'
if ((Test-Path -LiteralPath $pgControl) -and (Test-Path -LiteralPath $pgData)) {
    & (Join-Path $projectDirectory 'work\postgres\pgsql\bin\pg_isready.exe') -h 127.0.0.1 -p 55432 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        & $pgControl -D $pgData -l (Join-Path $projectDirectory 'work\postgres.log') -o '-h 127.0.0.1 -p 55432' -w start
        if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL startup failed' }
    }
}
if (!(Test-Path -LiteralPath $projectPython)) { throw 'Install Python 3.12 dependencies first.' }
& $projectPython (Join-Path $projectDirectory 'run_local.py')
