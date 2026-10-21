# Windows installer: right-click > Run with PowerShell (or: powershell -ExecutionPolicy Bypass -File deploy\install.ps1).
# Installs photo-sorter with the local image model and puts a "Photo Sorter" launcher on the Desktop. If the
# researchers will paste Google Drive links, the admin puts client_secret.json next to this installer first.
# Running it again updates the app.
$Here = $PSScriptRoot
Set-Location (Join-Path $Here "..")
$env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
$Log = Join-Path $env:LOCALAPPDATA "photo-sorter-install.log"     # the full output, for troubleshooting
Set-Content -Path $Log -Value ""
function Fail($msg) {
  Write-Host ""; Write-Host "X $msg" -ForegroundColor Red
  Write-Host "   Details are in $Log"; Write-Host "   Send that file, or a screenshot of this window, to whoever set up photo-sorter."
  Read-Host "Press Enter to close"; exit 1
}

Write-Host "== photo-sorter installer =="
Write-Host ""; Write-Host "1/3  Installing the app (a few minutes the first time; it stays quiet while it works) ..."
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex" *>> $Log
  if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { Fail "Could not install uv, the helper that installs the app. Check the internet connection." }
}
uv tool install --force --reinstall-package photo-sorter --python 3.12 ".[researcher]" *>> $Log
if ($LASTEXITCODE -ne 0) { Fail "Installing the app failed." }
Write-Host "   OK installed" -ForegroundColor Green
$Conf = if ($env:PHOTO_SORTER_HOME) { $env:PHOTO_SORTER_HOME } else { Join-Path $env:USERPROFILE ".config\photo-sorter" }
if (Test-Path (Join-Path $Here "client_secret.json")) {
  New-Item -ItemType Directory -Force -Path $Conf | Out-Null
  Copy-Item (Join-Path $Here "client_secret.json") $Conf -Force
  Write-Host "   OK Google sign-in file installed" -ForegroundColor Green
}

Write-Host ""; Write-Host "2/3  Getting the image model (about 1.5 GB, only the first time) ..."
Push-Location $env:USERPROFILE
photo-sorter setup
$code = $LASTEXITCODE
Pop-Location
if ($code -ne 0) { Fail "Getting the model failed. Check the internet connection and run the installer again." }

Write-Host ""; Write-Host "3/3  Putting the Photo Sorter icon on your Desktop ..."
$Launcher = Join-Path ([Environment]::GetFolderPath("Desktop")) "Photo Sorter.cmd"
Set-Content -Path $Launcher -Encoding ASCII -Value "@echo off`r`nrem Starts photo-sorter and opens it in your browser. Closing this window stops it.`r`ncd /d ""%USERPROFILE%""`r`n""%USERPROFILE%\.local\bin\photo-sorter.exe"" serve --open"
Write-Host "   OK $Launcher" -ForegroundColor Green

Write-Host ""; Write-Host "photo-sorter is installed." -ForegroundColor Green
Write-Host "  From now on, double-click 'Photo Sorter' on your Desktop: it opens in your browser,"
Write-Host "  and closing its window stops it. Starting it now ..."
Start-Process $Launcher
