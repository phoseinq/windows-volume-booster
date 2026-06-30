# Windows Volume Booster

Make Windows audio louder than 100%. A small tray app with a HUD that looks like
the native Windows volume flyout, but lets you push the volume past 100% — up to
500% by default.

When Windows is already at 100% and you press Volume-Up again, a yellow volume box
pops up above the normal Windows one and keeps going: 105%, 110%, 150%, ... Press
Volume-Down to bring it back down.

## How it works

Windows itself can't go past 100%, so the app uses a virtual audio device:

```
apps → VB-Cable (default device) → this app captures it → ×boost → your speakers
```

The app routes sound through [VB-Cable](https://vb-audio.com/Cable/) (a free
virtual cable), captures it, multiplies the samples by the boost factor, and plays
the result to your real speakers. It switches the default playback device to
VB-Cable while running and switches it back when you quit.

## Requirements

- Windows 10 / 11

The installer handles everything — it installs Python (via winget if it's missing), the
VB-Cable driver, and the Python packages.

## Install

Paste this into PowerShell — that's it (no clone needed):

```powershell
irm https://raw.githubusercontent.com/phoseinq/windows-volume-booster/main/install.ps1 | iex
```

It self-elevates (one UAC prompt) and:

1. installs the Python packages (`sounddevice numpy pystray pillow pycaw comtypes`)
2. installs the VB-Cable driver (silently)
3. copies the app to `%LOCALAPPDATA%\AudioBoost`
4. adds it to startup
5. launches it

No reboot needed.

## Usage

- **Volume-Up at 100%** → the boost HUD appears and rises with each press.
- **Volume-Down** while boosting → lowers the boost first, then normal volume.
- **Drag** the bar in the HUD to set it directly.
- **Tray icon → Show** → open the HUD.
- **Tray icon → Set max boost** → change the ceiling (200%–1000%); a warning shows
  above 300% because very high levels can damage speakers.
- **Tray icon → Quit** → stop and restore your normal default device.

The bar turns from yellow to red as you go higher, and the box auto-hides after a
couple of seconds.

`KEY_STEP` (per-press step, default 5%) and `SAFE_MAX` (warning threshold) are at
the top of `boost_tray.pyw` if you want to tweak them.

## Uninstall

```powershell
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v AudioBoost /f
rmdir /s /q "%LOCALAPPDATA%\AudioBoost"
```

Then remove VB-Cable with its own uninstaller if you don't want it anymore.

## Note on EqualizerAPO

A cleaner approach is [EqualizerAPO](https://equalizerapo.com/)'s preamp, which
boosts in place with no default-device switching. It works great on most systems,
but it conflicts with some vendor audio stacks (e.g. Nahimic on MSI machines),
which is why this app uses the VB-Cable method instead — it works regardless.
