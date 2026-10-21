# Photo Sorter installer for Windows (not tested on Windows yet).
# Right-click > Run with PowerShell. It says what it will install and where, then asks before starting.
# Running it again updates the app. To remove everything later, run uninstall.ps1 the same way.
# Google Drive links (not needed for Google Drive for Desktop folders) need a sign-in file: put client_secret.json
# next to this installer before sharing it, and it is set up too.
# (This file is plain ASCII on purpose: Windows PowerShell 5.1 misreads other characters.)

$Here = $PSScriptRoot
Set-Location (Join-Path $Here "..")
$env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
$HasUv = [bool](Get-Command uv -ErrorAction SilentlyContinue)
$ToolDir = if ($HasUv) { (uv tool dir 2>$null) } else { Join-Path $env:APPDATA "uv\tools" }
$AppDir = Join-Path $ToolDir "photo-sorter"
$HfHome = if ($env:HF_HOME) { $env:HF_HOME } else { Join-Path $env:USERPROFILE ".cache\huggingface" }
$ModelDir = Join-Path $HfHome "hub\models--google--siglip2-base-patch16-224"
$ConfDir = if ($env:PHOTO_SORTER_HOME) { $env:PHOTO_SORTER_HOME } else { Join-Path $env:USERPROFILE ".config\photo-sorter" }
$Launcher = Join-Path ([Environment]::GetFolderPath("Desktop")) "Photo Sorter.cmd"
$Log = Join-Path $env:LOCALAPPDATA "photo-sorter-install.log"
$Line = "=" * 60

function Ok($what, $where) {
  Write-Host ("  OK  {0,-22} " -f $what) -ForegroundColor Green -NoNewline
  Write-Host $where -ForegroundColor DarkGray
}
function Fail($msg) {
  Write-Host ""; Write-Host "  X  $msg" -ForegroundColor Red
  Write-Host "     The details are in $Log"
  Write-Host "     Send that file, or a screenshot of this window, to whoever helps you with Photo Sorter."
  Read-Host "  Press Enter to close"; exit 1
}

Set-Content -Path $Log -Value ""
$Free = [math]::Floor((Get-PSDrive -Name $env:USERPROFILE.Substring(0, 1)).Free / 1GB)
Clear-Host
Write-Host ""
Write-Host "  $Line" -ForegroundColor Cyan
Write-Host "   ><(((('>   PHOTO SORTER  -  installer" -ForegroundColor Cyan
Write-Host "   Sorts research photos into folders, right here on this PC."
Write-Host "  $Line" -ForegroundColor Cyan
Write-Host ""
Write-Host "  What it will do"
Write-Host ""
if (Test-Path $AppDir) { Write-Host "   1  Update the app                                   about 1 GB" }
else { Write-Host "   1  Install the app                                  about 1 GB" }
Write-Host "      from pypi.org  ->  $AppDir" -ForegroundColor DarkGray
if (-not $HasUv) { Write-Host "      first it installs uv, the free tool that installs apps like this one (from astral.sh)" -ForegroundColor DarkGray }
if (Test-Path $ModelDir) { Write-Host "   2  Image model (SigLIP 2, by Google)                already here" }
else { Write-Host "   2  Download the image model (SigLIP 2, by Google)   1.4 GB" }
Write-Host "      from huggingface.co  ->  $ModelDir" -ForegroundColor DarkGray
Write-Host "   3  Put a 'Photo Sorter' icon on your Desktop"
Write-Host "      ->  $Launcher" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  Your photos are never uploaded: Photo Sorter runs on this PC."
if ($Free -lt 4) { Write-Host "  Free space: $Free GB. About 3 GB are needed; free up some space first if you can." -ForegroundColor Yellow }
else { Write-Host "  Free space: $Free GB (about 3 GB needed)" -ForegroundColor DarkGray }
Write-Host "  A log of every step is kept in $Log" -ForegroundColor DarkGray
Write-Host ""
Read-Host "  Press Enter to start, or close this window to cancel" | Out-Null

# 1. the app
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like "*photo-sorter*serve*" } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Ok "Closed Photo Sorter" "it restarts with the new version at the end" }
if (-not $HasUv) {
  Write-Host "  ... Installing uv, the tool that installs the app"
  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex" *>> $Log
  if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { Fail "Could not install uv. Check the internet connection and run the installer again." }
  Ok "uv installed" "$env:USERPROFILE\.local\bin\uv.exe"
}
Write-Host "  ... Installing the app (a few minutes the first time)"
uv tool install --force --reinstall-package photo-sorter --python 3.12 ".[researcher]" *>> $Log
if ($LASTEXITCODE -ne 0) { Fail "Installing the app failed." }
$AppDir = Join-Path (uv tool dir 2>$null) "photo-sorter"
Ok "App installed" $AppDir
if (Test-Path (Join-Path $Here "client_secret.json")) {
  New-Item -ItemType Directory -Force -Path $ConfDir | Out-Null
  Copy-Item (Join-Path $Here "client_secret.json") $ConfDir -Force
  Ok "Google sign-in file" $ConfDir
}

# 2. the image model
if (Test-Path $ModelDir) { Write-Host "  ... Checking the image model" }
else { Write-Host "  ... Downloading the image model (1.4 GB, a few minutes)" }
Push-Location $env:USERPROFILE
photo-sorter setup *>> $Log
$code = $LASTEXITCODE
Pop-Location
if ($code -ne 0) { Fail "Getting the image model failed. Check the internet connection and run the installer again." }
Ok "Image model ready" $ModelDir

# 3. the Desktop icon
Set-Content -Path $Launcher -Encoding ASCII -Value "@echo off`r`nrem Starts Photo Sorter and opens it in your browser. Keep this window open while you work: closing it stops Photo Sorter.`r`ncd /d ""%USERPROFILE%""`r`n""%USERPROFILE%\.local\bin\photo-sorter.exe"" serve --open"
Ok "Desktop icon" $Launcher

Write-Host ""
Write-Host "  $Line" -ForegroundColor Cyan
Write-Host "   Photo Sorter is installed" -ForegroundColor Green
Write-Host ""
Write-Host "   Start it    double-click 'Photo Sorter' on your Desktop"
Write-Host "   Update it   run this installer again"
Write-Host "   Remove it   right-click uninstall.ps1, next to this installer > Run with PowerShell"
Write-Host "  $Line" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Opening Photo Sorter now: it shows up in your browser. Keep its small window open while you work."
Start-Process $Launcher
