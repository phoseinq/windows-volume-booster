<#
  install.ps1 — one-command setup for Windows Volume Booster
  Run from the repo folder:
      powershell -ExecutionPolicy Bypass -File install.ps1
  It self-elevates and does everything: VB-Cable driver, Python deps,
  app install, and autostart. No reboot needed.
#>

# ---- self-elevate ----
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)) {
    Start-Process powershell -Verb RunAs -ArgumentList "-ExecutionPolicy","Bypass","-File","`"$PSCommandPath`""
    exit
}
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$dest = "$env:LOCALAPPDATA\AudioBoost"

# ---- locate Python ----
$pyw = (Get-Command pythonw.exe -EA SilentlyContinue).Source
if (-not $pyw) { Write-Host "Python not found. Install Python 3.11+ first (python.org)."; pause; exit 1 }
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
Copy-Item (Join-Path $here 'boost_tray.pyw') (Join-Path $dest 'boost_tray.pyw') -Force

Write-Host "[4/5] Autostart on login..."
$val = "`"$pyw`" `"$dest\boost_tray.pyw`""
New-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name 'AudioBoost' -Value $val -PropertyType String -Force | Out-Null

Write-Host "[5/5] Starting..."
Start-Process $pyw -ArgumentList "`"$dest\boost_tray.pyw`""

Write-Host ""
Write-Host "Done. A speaker icon is in your tray." -ForegroundColor Green
Write-Host "Set Windows volume to 100%, then press Volume-Up to boost beyond 100%."
