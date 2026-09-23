<#
  Open Loops - one-click installer for Windows.
  Right-click this file -> "Run with PowerShell". Or from a terminal:
      powershell -ExecutionPolicy Bypass -File setup.ps1
  The downloadable OpenLoops-Setup.exe (packaging/windows) fetches the repo and runs this with
      -Dest "<install folder>" -Name <first name> [-At HH:MM] -NoLaunch
  Re-run over an existing install (the exe or this file) keeps config.json, including its refresh
  time, unless -At is given explicitly.

  Testing a fresh install beside the one you use, without touching it (INSTALL.md "Testing a fresh install"):
      powershell -ExecutionPolicy Bypass -File setup.ps1 -Dest $HOME\OpenLoops-test -NoApp -NoTask -Port 8790 -Name Test
  -Dest (or $env:OPENLOOPS_DEST) installs elsewhere; -NoApp skips the Desktop / Start-menu icons; -NoTask leaves
  the scheduled task alone (there is one per user); -Port saves the port in that copy's config.json.
  -Isolated (#36) makes it a test copy that stays away from your own files and starts no scan by itself: it implies
  -NoApp and -NoTask and writes "isolated": true (and "test_copy": true) into its config.json. It reads no to-do file
  and its page waits for Start the first scan every time it is opened; once pressed, it still uses the AI you are
  signed in to, so your real accounts (read-only: a scan sends and drafts nothing). $env:OPENLOOPS_ISOLATED = "1"
  does the same for any copy at run time.
  -Help prints this list and installs nothing.

  What it does (all on this computer, nothing sent anywhere):
    1. Installs Python if it's missing (using Windows' own installer, winget). It never installs an AI CLI
       (Claude, Codex, Grok): the app's checklist offers Install <AI> for the AI the person picks (#16, #39).
    2. Copies Open Loops to your user folder (%LOCALAPPDATA%\OpenLoops, or -Dest).
    3. Puts an "Open Loops" icon on your Desktop and in the Start menu.
    4. Sets it to refresh every weekday morning (default 09:15).
    5. Opens the app - which walks you through connecting Slack and email.
#>
# An unknown parameter (-Bogus, a typo such as -Isolatd) never reaches the script: with this param() block and
# [CmdletBinding()], PowerShell itself stops with "A parameter cannot be found that matches parameter name ..."
# before the first line below runs, so nothing is written (the install.sh side of #55 needed its own check).
[CmdletBinding()]
param([string]$At = "09:15", [string]$Name = "", [string]$Dest = "", [switch]$NoLaunch,
      [switch]$NoApp, [switch]$NoTask, [int]$Port = 0, [switch]$Isolated, [switch]$Help)

$ErrorActionPreference = "Stop"

# -Help (#55): print the flag list and stop before anything is checked, installed or written. The comment block at
# the top of this file is that list, so it is printed from here rather than kept twice.
if ($Help) {
    Write-Host "usage: setup.ps1 [-At HH:MM] [-Name NAME] [-Dest DIR] [-Port N] [-NoApp] [-NoTask] [-NoLaunch] [-Isolated] [-Help]"
    Write-Host ""
    $text = Get-Content -LiteralPath $PSCommandPath -Raw
    $start = $text.IndexOf("<#"); $end = $text.IndexOf("#>")
    if ($start -ge 0 -and $end -gt $start) { Write-Host $text.Substring($start + 2, $end - $start - 2).Trim("`r", "`n") }
    exit 0
}
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

# Same for -Port: a port saved in config.json that the app cannot use would stop it from starting.
if ($Port -and ($Port -lt 1024 -or $Port -gt 65535)) {
    Write-Host "  -Port must be a number from 1024 to 65535, for example 8790." -ForegroundColor Yellow
    exit 1
}

# -Isolated: a test copy touches no icon and no scheduled task (#36)
if ($Isolated) { $NoApp = [switch]$true; $NoTask = [switch]$true }

# ---------- 1. Python ----------
Say "Checking Python..."
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Say "Installing Python (this can take a minute)..."
    winget install --id Python.Python.3.13 -e --accept-source-agreements --accept-package-agreements --silent | Out-Null
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
}
Ok ("Python " + ((python --version) -replace "Python ",""))

# ---------- 2. AI ----------
# Nothing is installed here, and nothing here can fail the install: the app's first screen offers
# Install <AI> for whichever AI the person chooses (Claude, Codex or Grok). This only says so when none is
# found, looking on PATH and in the folders the vendors' installers use (agent.install_dirs).
$aiFound = [bool](Get-Command claude, codex, grok -ErrorAction SilentlyContinue)
foreach ($d in @((Join-Path $HOME ".local\bin"), (Join-Path $HOME ".grok\bin"),
                 (Join-Path $env:LOCALAPPDATA "Programs\OpenAI\Codex\bin"))) {
    foreach ($c in @("claude.exe", "codex.exe", "grok.exe")) {
        if (Test-Path -LiteralPath (Join-Path $d $c)) { $aiFound = $true }
    }
}
if (-not $aiFound) { Say "No AI is installed yet. Open Loops will offer to install one on its first screen." }

# ---------- 3. Copy files ----------
$Src  = $PSScriptRoot
# Install into the user's local app-data folder - no admin rights needed, and it works wherever the
# download was unzipped (Downloads, Desktop, a USB stick). The Desktop icon points here, so the
# downloaded folder can be deleted afterwards.
$DefaultDest = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA "OpenLoops"))
if (-not $Dest) { $Dest = $env:OPENLOOPS_DEST }
if (-not $Dest) { $Dest = $DefaultDest }
$Dest = [IO.Path]::GetFullPath($Dest)
# A test copy (-Dest with -NoApp and -NoTask, INSTALL.md "Testing a fresh install") is recorded as "test_copy": true
# in its config.json, so the app never tells it to fix the other copy's morning refresh (doctor.is_test_copy).
$TestCopy = (($Dest -ne $DefaultDest) -and $NoApp -and $NoTask) -or $Isolated   # -Isolated is a test copy wherever it is
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
    # The first scan reads as far back as Settings > History says (history_days, as app.py's fresh_state does), which
    # is what the page tells the person before it starts (#38): from the config.json already here, else the template.
    $days = 30
    foreach ($f in @((Join-Path $Dest "config.json"), (Join-Path $Src "config.template.json"))) {
        try {
            $hd = (Get-Content $f -Raw -Encoding UTF8 -ErrorAction Stop | ConvertFrom-Json).history_days
            if ($hd) { $days = [Math]::Min([int]$hd, 365) }
            break
        } catch { continue }
    }
    $cursor = (Get-Date).AddDays(-$days).ToString("yyyy-MM-ddTHH:mm:sszzz")
    @{ cursor = $cursor; last_refresh = $null; loops = @() } | ConvertTo-Json | ForEach-Object { [IO.File]::WriteAllText($StateFile, $_, (New-Object Text.UTF8Encoding $false)) }  # no BOM - Python json refuses it
}
$CfgFile = Join-Path $Dest "config.json"
if (Test-Path $CfgFile) {
    # Updating: the person's own refresh time wins unless a new one was asked for explicitly.
    # -Encoding UTF8: the app writes config.json as BOM-less UTF-8; PowerShell 5.1 would otherwise read it as ANSI
    # and the rewrite below would mangle any non-ASCII name or tone text.
    $cfg = Get-Content $CfgFile -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($Port) {   # an explicit -Port wins; everything else in config.json stays
        $cfg | Add-Member -NotePropertyName port -NotePropertyValue $Port -Force
        $cfg | ConvertTo-Json -Depth 6 | ForEach-Object { [IO.File]::WriteAllText($CfgFile, $_, (New-Object Text.UTF8Encoding $false)) }
    }
    if ($TestCopy -ne ($cfg.test_copy -eq $true)) {   # this run says what the copy is
        if ($TestCopy) { $cfg | Add-Member -NotePropertyName test_copy -NotePropertyValue $true -Force }
        else { $cfg.PSObject.Properties.Remove('test_copy') }
        $cfg | ConvertTo-Json -Depth 6 | ForEach-Object { [IO.File]::WriteAllText($CfgFile, $_, (New-Object Text.UTF8Encoding $false)) }
    }
    if ([bool]$Isolated -ne ($cfg.isolated -eq $true)) {   # likewise: a run without -Isolated takes the mark off
        if ($Isolated) { $cfg | Add-Member -NotePropertyName isolated -NotePropertyValue $true -Force }
        else { $cfg.PSObject.Properties.Remove('isolated') }
        $cfg | ConvertTo-Json -Depth 6 | ForEach-Object { [IO.File]::WriteAllText($CfgFile, $_, (New-Object Text.UTF8Encoding $false)) }
    }
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
    if ($Port) { $tpl | Add-Member -NotePropertyName port -NotePropertyValue $Port -Force }   # app.py: --port, OPENLOOPS_PORT, then this
    if ($TestCopy) { $tpl | Add-Member -NotePropertyName test_copy -NotePropertyValue $true -Force }   # doctor.is_test_copy
    if ($Isolated) { $tpl | Add-Member -NotePropertyName isolated -NotePropertyValue $true -Force }   # store.isolated (#36)
    $tpl | Add-Member -NotePropertyName first_scan -NotePropertyValue "later" -Force   # the task reads nothing until Start the first scan
    $tpl | ConvertTo-Json -Depth 6 | ForEach-Object { [IO.File]::WriteAllText($CfgFile, $_, (New-Object Text.UTF8Encoding $false)) }
}
Ok "Files in place"
if ($Isolated) { Ok "Isolated test copy: it reads no to-do file and starts no scan until you press Start the first scan" }

# ---------- 4. Desktop + Start menu icons ----------
$pyw  = (Get-Command pythonw -ErrorAction SilentlyContinue).Source
if (-not $pyw) { $pyw = (Get-Command python).Source }
$ico  = Join-Path $Dest "docs\AppIcon.ico"
if ($NoApp) {
    Ok "Skipped the Desktop and Start menu icons ($(if ($Isolated) { 'test copy' } else { '-NoApp' })). Start this copy with: cd `"$Dest`"; python -m openloops.app"
} else {
    $ws = New-Object -ComObject WScript.Shell
    foreach ($folder in @([Environment]::GetFolderPath("Desktop"), [Environment]::GetFolderPath("Programs"))) {
        $s = $ws.CreateShortcut((Join-Path $folder "Open Loops.lnk"))
        $s.TargetPath = $pyw; $s.Arguments = "-m openloops.app"; $s.WorkingDirectory = $Dest
        if (Test-Path $ico) { $s.IconLocation = "$ico,0" } else { $s.IconLocation = "%SystemRoot%\System32\shell32.dll,44" }
        $s.Description = "Open Loops - who owes you a reply"; $s.Save()
    }
    Ok "Desktop and Start menu icons created"
}

# ---------- 5. Morning refresh ----------
# -Isolated over a copy that has the weekday task (one per user): remove it, so "no automatic scans" is true. Only a
# task whose action runs in THIS folder; one for another copy is left alone (refresh.py skips on an isolated copy anyway).
if ($Isolated) {
    $task = Get-ScheduledTask -TaskName "Claude Open Loops Refresh" -ErrorAction SilentlyContinue
    if ($task -and ($task.Actions | Where-Object { $_.WorkingDirectory -and ([IO.Path]::GetFullPath($_.WorkingDirectory).TrimEnd('\') -eq $Dest.TrimEnd('\')) })) {
        powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Dest "scripts\register-task.ps1") -Remove | Out-Null
        Ok "Removed this copy's weekday refresh (-Isolated: it starts no scan by itself)"
    }
}
if ($NoTask) {
    Ok "Skipped the weekday refresh ($(if ($Isolated) { 'test copy' } else { '-NoTask' })): whatever was already scheduled is unchanged"
} else {
    powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Dest "scripts\register-task.ps1") -At $At | Out-Null
    # $ErrorActionPreference = "Stop" does not react to a native process's exit code in Windows PowerShell 5.1.
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  Couldn't set the weekday refresh (register-task.ps1 exit $LASTEXITCODE, time '$At'). Fix the problem above and run setup again, or set the time later in the app's Settings." -ForegroundColor Yellow
        exit 1
    }
    Ok "Will refresh itself weekdays at $At"
}

# ---------- 6. Open it ----------
if ($NoLaunch) {
    Ok "Installed. Open Loops will guide you through connecting Slack and email when you first open it."
} else {
    Say "Opening Open Loops - it will guide you through connecting Slack and email."
    $launchArgs = "-m openloops.app"
    if ($Port) { $launchArgs += " --port $Port" }   # an explicit -Port beats a leftover OPENLOOPS_PORT, as on the Mac
    Start-Process -FilePath $pyw -ArgumentList $launchArgs -WorkingDirectory $Dest
}
Write-Host ""
Write-Host "  Done. You can close this window." -ForegroundColor Green
Write-Host ""
