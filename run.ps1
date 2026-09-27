# Convenience runner for Windows. Usage: .\run.ps1 setup|deck-setup|reset|triage|eval|app|test
#
# Dependency source: set ARTIFACTORY_PYPI_URL (and ARTIFACTORY_NPM_URL for the slide builder) in .env to
# install from your organisation's Artifactory instead of the public registries. Leave blank for public PyPI/npm.
param([string]$cmd = "help")
$ErrorActionPreference = "Stop"
$env:PYTHONPATH = "src"
$env:PYTHONIOENCODING = "utf-8"
$py = ".\.venv\Scripts\python.exe"

function Import-DotEnv([string]$path = ".env") {
  # Load KEY=VALUE lines into this process (real environment variables win; inline "  # comments" are ignored)
  if (-not (Test-Path $path)) { return }
  foreach ($line in Get-Content $path) {
    if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
      $name, $value = $matches[1], ($matches[2] -replace '\s+#.*$', '').Trim().Trim('"').Trim("'")
      if ($value -and -not [Environment]::GetEnvironmentVariable($name)) { Set-Item "env:$name" $value }
    }
  }
}

function Get-IndexArgs {
  $a = @()
  if ($env:ARTIFACTORY_PYPI_URL) {
    $a += @("--index-url", $env:ARTIFACTORY_PYPI_URL)
    Write-Host "Installing Python packages from Artifactory: $(([uri]$env:ARTIFACTORY_PYPI_URL).Host)"
  } else {
    Write-Host "ARTIFACTORY_PYPI_URL not set: installing from public PyPI"
  }
  if ($env:ARTIFACTORY_CA_BUNDLE) { $a += @("--cert", $env:ARTIFACTORY_CA_BUNDLE) }   # corporate root CA (.pem)
  return $a
}

switch ($cmd) {
  "setup" {
    Import-DotEnv
    $python = if ($env:PYTHON_EXE) { $env:PYTHON_EXE } else { "python" }   # Python 3.10+
    if (-not (Test-Path $py)) { & $python -m venv .venv }
    & $py -m pip --version *> $null
    if ($LASTEXITCODE -ne 0) { & $py -m ensurepip --upgrade }          # venvs created without pip (e.g. by uv)
    $idx = Get-IndexArgs
    & $py -m pip install @idx -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw "pip install failed (check ARTIFACTORY_PYPI_URL / credentials / certificate)" }
    Write-Output "Setup complete."
  }
  "deck-setup" {
    # Only needed to rebuild the slides from deck/*.js; the finished .pptx files do not need it
    Import-DotEnv
    Push-Location deck
    try {
      if ($env:ARTIFACTORY_NPM_URL) { npm install --registry $env:ARTIFACTORY_NPM_URL } else { npm install }
    } finally { Pop-Location }
  }
  "reset"  { & $py -m meridian init --reset; & $py -m meridian ingest }
  "triage" { & $py -m meridian run }
  "eval"   { & $py -m meridian eval --out eval\latest_eval.json }
  "app"    { & $py -m streamlit run app\streamlit_app.py }
  "test"   { & $py -m pytest -q }
  default  { Write-Output "usage: .\run.ps1 setup|deck-setup|reset|triage|eval|app|test" }
}
