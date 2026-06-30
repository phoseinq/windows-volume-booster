<#
  install.ps1 — one-command setup for Windows Volume Booster

  Run straight from PowerShell (no clone needed):
      irm https://raw.githubusercontent.com/phoseinq/windows-volume-booster/main/install.ps1 | iex

  Self-elevates (one UAC), installs VB-Cable + Python deps, the app, and autostart.
#>

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$RAW = 'https://raw.githubusercontent.com/phoseinq/windows-volume-booster/main'

# ---- self-elevate (re-run the one-liner as admin) ----
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)) {
    Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-Command',"irm $RAW/install.ps1 | iex"
    return
}

$dest = "$env:LOCALAPPDATA\AudioBoost"

# ---- Python (install automatically if missing) ----
function Find-Pyw {
    $p = (Get-Command pythonw.exe -EA SilentlyContinue).Source
    if (-not $p) {
        $c = Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*\pythonw.exe" -EA SilentlyContinue | Sort-Object FullName | Select-Object -Last 1
        if ($c) { $p = $c.FullName }
    }
    return $p
}
$pyw = Find-Pyw
if (-not $pyw) {
    Write-Host "[0/5] Installing Python..."
    try { winget install -e --id Python.Python.3.12 --scope user --silent --accept-source-agreements --accept-package-agreements 2>&1 | Out-Null } catch {}
    $pyw = Find-Pyw
    if (-not $pyw) {
        # no winget (e.g. Windows Sandbox / LTSC): get the official installer directly
        $pv = '3.12.8'
        $f = "$env:TEMP\python-$pv-amd64.exe"
        Invoke-WebRequest "https://www.python.org/ftp/python/$pv/python-$pv-amd64.exe" -OutFile $f -UseBasicParsing
        Start-Process $f -ArgumentList '/quiet','InstallAllUsers=0','PrependPath=1','Include_pip=1','Include_launcher=1' -Wait
        $pyw = Find-Pyw
    }
}
if (-not $pyw) { Write-Host "Could not install Python automatically. Get it from python.org and re-run." -ForegroundColor Red; pause; exit 1 }
$py = $pyw -replace 'pythonw\.exe$','python.exe'

Write-Host "[1/5] Python packages..."
& $py -m pip install --quiet --upgrade sounddevice numpy pystray pillow pycaw comtypes

Write-Host "[2/5] VB-Cable virtual audio driver..."
if (-not (Get-PnpDevice -Class AudioEndpoint -EA SilentlyContinue | Where-Object { $_.FriendlyName -like '*CABLE Input*' })) {
    $zip = "$env:TEMP\VBCABLE.zip"; $ex = "$env:TEMP\VBCABLE"
    Invoke-WebRequest 'https://download.vb-audio.com/Download_CABLE/VBCABLE_Driver_Pack45.zip' -OutFile $zip -UserAgent 'Mozilla/5.0'
    Remove-Item $ex -Recurse -Force -EA SilentlyContinue
    Expand-Archive $zip $ex -Force
    Start-Process "$ex\VBCABLE_Setup_x64.exe" -ArgumentList '-i','-h' -Wait
    Start-Sleep 3
} else { Write-Host "  already installed." }

Write-Host "[3/5] Installing app..."
New-Item -ItemType Directory -Force -Path $dest | Out-Null
$local = if ($PSCommandPath) { Join-Path (Split-Path -Parent $PSCommandPath) 'boost_tray.pyw' } else { $null }
if ($local -and (Test-Path $local)) { Copy-Item $local "$dest\boost_tray.pyw" -Force }
else { Invoke-WebRequest "$RAW/boost_tray.pyw" -OutFile "$dest\boost_tray.pyw" -UserAgent 'Mozilla/5.0' }

Write-Host "[4/5] Autostart on login..."
$val = "`"$pyw`" `"$dest\boost_tray.pyw`""
New-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name 'AudioBoost' -Value $val -PropertyType String -Force | Out-Null

Write-Host "[5/5] Starting..."
Start-Process $pyw -ArgumentList "`"$dest\boost_tray.pyw`""

Write-Host ""
Write-Host "Done. A speaker icon is in your tray." -ForegroundColor Green
Write-Host "Set Windows volume to 100%, then press Volume-Up to boost beyond 100%."
Start-Sleep 4
