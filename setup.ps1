<#
  Open Loops - one-click installer for Windows.
  Right-click this file -> "Run with PowerShell". Or from a terminal:
      powershell -ExecutionPolicy Bypass -File setup.ps1
  The downloadable OpenLoops-Setup.exe (packaging/windows) fetches the repo and runs this with
      -Dest "<install folder>" -Name <first name> [-At HH:MM] -NoLaunch
  Re-run over an existing install (the exe or this file) keeps config.json, including its refresh
  time, unless -At is given explicitly.

  What it does (all on this computer, nothing sent anywhere):
    1. Installs Python and Claude Code if they're missing (using Windows' own installer, winget).
    2. Copies Open Loops to your user folder (%LOCALAPPDATA%\OpenLoops, or -Dest).
    3. Puts an "Open Loops" icon on your Desktop and in the Start menu.
    4. Sets it to refresh every weekday morning (default 09:15).
    5. Opens the app - which walks you through connecting Slack and email.
#>
[CmdletBinding()]
param([string]$At = "09:15", [string]$Name = "", [string]$Dest = "", [switch]$NoLaunch)

$ErrorActionPreference = "Stop"
function Say($t) { Write-Host ""; Write-Host "  $t" -ForegroundColor Cyan }
function Ok($t)  { Write-Host "  [ok] $t" -ForegroundColor Green }

Write-Host ""
Write-Host "  ============================" -ForegroundColor Cyan
Write-Host "   Open Loops - setup"        -ForegroundColor Cyan
Write-Host "  ============================" -ForegroundColor Cyan

# Validate before anything is installed or written: a bad time would otherwise be saved into config.json,
# register-task.ps1 would fail, and every later run without -At would reuse the saved value.
if ($At -notmatch '^(?:[01]\d|2[0-3]):[0-5]\d$') {
    Write-Host "  Refresh time '$At' must be HH:MM, 24-hour (00:00 to 23:59), for example 09:15." -ForegroundColor Yellow
    exit 1
}

# ---------- 1. Python ----------
Say "Checking Python..."
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Say "Installing Python (this can take a minute)..."
    winget install --id Python.Python.3.13 -e --accept-source-agreements --accept-package-agreements --silent | Out-Null
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
}
Ok ("Python " + ((python --version) -replace "Python ",""))

# ---------- 2. Claude Code ----------
Say "Checking Claude..."
if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
    Say "Installing Claude Code (this can take a minute)..."
    try {
        Invoke-RestMethod https://claude.ai/install.ps1 | Invoke-Expression
    } catch {
        winget install --id Anthropic.ClaudeCode -e --accept-source-agreements --accept-package-agreements --silent | Out-Null
    }
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
}
if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
    Write-Host "  Couldn't install Claude automatically. Please install it from https://claude.ai/code and run this again." -ForegroundColor Yellow
    exit 1
}
Ok "Claude is installed"

# ---------- 3. Copy files ----------
$Src  = $PSScriptRoot
# Install into the user's local app-data folder - no admin rights needed, and it works wherever the
# download was unzipped (Downloads, Desktop, a USB stick). The Desktop icon points here, so the
# downloaded folder can be deleted afterwards.
if (-not $Dest) { $Dest = Join-Path $env:LOCALAPPDATA "OpenLoops" }
$Dest = [IO.Path]::GetFullPath($Dest)
if ((Resolve-Path $Src).Path -eq $Dest) { Say "Already installed here - updating." }
Say "Installing Open Loops to $Dest ..."
New-Item -ItemType Directory -Force $Dest | Out-Null
if ((Resolve-Path $Src).Path -ne $Dest) {
    Get-ChildItem $Src -Exclude "state","voice.json","state.json","config.json","people_suggested.json",".git","docs","tests",".worktrees" | Copy-Item -Destination $Dest -Recurse -Force
    # docs\ is skipped above (website + screenshots), but the shortcut icon lives there.
    $srcIco = Join-Path $Src "docs\AppIcon.ico"
    if (Test-Path $srcIco) {
        New-Item -ItemType Directory -Force (Join-Path $Dest "docs") | Out-Null
        Copy-Item $srcIco (Join-Path $Dest "docs\AppIcon.ico") -Force
    }
}
New-Item -ItemType Directory -Force (Join-Path $Dest "state\logs") | Out-Null

# fresh state + config unless the person already has them
$StateFile = Join-Path $Dest "state.json"
if (-not (Test-Path $StateFile)) {
    $cursor = (Get-Date).AddDays(-7).ToString("yyyy-MM-ddTHH:mm:sszzz")
    @{ cursor = $cursor; last_refresh = $null; loops = @() } | ConvertTo-Json | ForEach-Object { [IO.File]::WriteAllText($StateFile, $_, (New-Object Text.UTF8Encoding $false)) }  # no BOM - Python json refuses it
}
$CfgFile = Join-Path $Dest "config.json"
if (Test-Path $CfgFile) {
    # Updating: the person's own refresh time wins unless a new one was asked for explicitly.
    # -Encoding UTF8: the app writes config.json as BOM-less UTF-8; PowerShell 5.1 would otherwise read it as ANSI
    # and the rewrite below would mangle any non-ASCII name or tone text.
    $cfg = Get-Content $CfgFile -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($PSBoundParameters.ContainsKey('At')) {
        $cfg.refresh_time = $At   # keep config.json and the scheduled task in step (as Settings does)
        $cfg | ConvertTo-Json -Depth 6 | ForEach-Object { [IO.File]::WriteAllText($CfgFile, $_, (New-Object Text.UTF8Encoding $false)) }
    } elseif ($cfg.refresh_time -match '^(?:[01]\d|2[0-3]):[0-5]\d$') {
        $At = $cfg.refresh_time   # a hand-edited bad value falls back to the default rather than breaking the task
    }
}
if (-not (Test-Path $CfgFile)) {
    while (-not $Name) { $Name = (Read-Host "  Your first name (used so messages sound like you)").Trim() }
    $tpl = Get-Content (Join-Path $Src "config.template.json") -Raw -Encoding UTF8 | ConvertFrom-Json
    $tpl.owner_name   = $Name
    $tpl.refresh_time = $At
    $tpl | ConvertTo-Json -Depth 6 | ForEach-Object { [IO.File]::WriteAllText($CfgFile, $_, (New-Object Text.UTF8Encoding $false)) }
}
Ok "Files in place"

# ---------- 4. Desktop + Start menu icons ----------
$pyw  = (Get-Command pythonw -ErrorAction SilentlyContinue).Source
if (-not $pyw) { $pyw = (Get-Command python).Source }
$ico  = Join-Path $Dest "docs\AppIcon.ico"
$ws = New-Object -ComObject WScript.Shell
foreach ($folder in @([Environment]::GetFolderPath("Desktop"), [Environment]::GetFolderPath("Programs"))) {
    $s = $ws.CreateShortcut((Join-Path $folder "Open Loops.lnk"))
    $s.TargetPath = $pyw; $s.Arguments = "-m openloops.app"; $s.WorkingDirectory = $Dest
    if (Test-Path $ico) { $s.IconLocation = "$ico,0" } else { $s.IconLocation = "%SystemRoot%\System32\shell32.dll,44" }
    $s.Description = "Open Loops - who owes you a reply"; $s.Save()
}
Ok "Desktop and Start menu icons created"

# ---------- 5. Morning refresh ----------
powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Dest "scripts\register-task.ps1") -At $At | Out-Null
# $ErrorActionPreference = "Stop" does not react to a native process's exit code in Windows PowerShell 5.1.
if ($LASTEXITCODE -ne 0) {
    Write-Host "  Couldn't set the weekday refresh (register-task.ps1 exit $LASTEXITCODE, time '$At'). Fix the problem above and run setup again, or set the time later in the app's Settings." -ForegroundColor Yellow
    exit 1
}
Ok "Will refresh itself weekdays at $At"

# ---------- 6. Open it ----------
if ($NoLaunch) {
    Ok "Installed. Open Loops will guide you through connecting Slack and email when you first open it."
} else {
    Say "Opening Open Loops - it will guide you through connecting Slack and email."
    Start-Process -FilePath $pyw -ArgumentList "-m openloops.app" -WorkingDirectory $Dest
}
Write-Host ""
Write-Host "  Done. You can close this window." -ForegroundColor Green
Write-Host ""
