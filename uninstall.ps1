# Removes ReeFile from this PC: the app, the image model, the Desktop icon and its settings.
# Your photos, their folders and their category lists (CATEGORIES.md) are never touched. Open PowerShell and paste:
#
#   powershell -ExecutionPolicy ByPass -NoProfile -c "irm https://raw.githubusercontent.com/alegalguera/image-classification/main/uninstall.ps1 | iex"
#
# $env:PHOTO_SORTER_UNATTENDED = "1" skips the question (for automation).
# (Plain ASCII on purpose: Windows PowerShell 5.1 misreads other characters.)

function Ok([string]$What, [string]$Where) {
  Write-Host ("  OK  {0,-22} " -f $What) -ForegroundColor Green -NoNewline
  Write-Host $Where -ForegroundColor DarkGray
}

function Uninstall-PhotoSorter {
  $BinDir = Join-Path $env:USERPROFILE ".local\bin"
  $env:Path = "$BinDir;$env:Path"
  $HasUv = [bool](Get-Command uv -ErrorAction SilentlyContinue)
  $ToolDir = if ($HasUv) { (& uv tool dir 2>$null) } else { Join-Path $env:APPDATA "uv\tools" }
  $AppDir = Join-Path $ToolDir "photo-sorter"
  $HfHome = if ($env:HF_HOME) { $env:HF_HOME } else { Join-Path $env:USERPROFILE ".cache\huggingface" }
  $ModelDir = Join-Path $HfHome "hub\models--google--siglip2-base-patch16-224"
  $ConfDir = if ($env:PHOTO_SORTER_HOME) { $env:PHOTO_SORTER_HOME } else { Join-Path $env:USERPROFILE ".config\photo-sorter" }
  $Desktop = [Environment]::GetFolderPath("Desktop")
  $Shortcut = Join-Path $Desktop "ReeFile.lnk"
  $Log = Join-Path $env:LOCALAPPDATA "photo-sorter-install.log"
  $Line = "=" * 64

  if (-not $env:PHOTO_SORTER_UNATTENDED) { Clear-Host }
  Write-Host ""
  Write-Host "  $Line" -ForegroundColor Cyan
  Write-Host "   ><(((('>   ReeFile  -  remove" -ForegroundColor Cyan
  Write-Host "  $Line" -ForegroundColor Cyan
  Write-Host ""
  Write-Host "  This removes"
  Write-Host ""
  Write-Host "   the app                     $AppDir"
  Write-Host "   the image model             $ModelDir"
  Write-Host "   the Desktop icon            $Shortcut"
  Write-Host "   settings, Google sign-in    $ConfDir"
  Write-Host ""
  Write-Host "  Your photos, their folders and their category lists stay exactly where they are."
  Write-Host ""
  if (-not $env:PHOTO_SORTER_UNATTENDED) { $null = Read-Host "  Press Enter to remove ReeFile, or close this window to cancel" }

  Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like "*photo-sorter*serve*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; Ok "Closed ReeFile" "" }
  $Exe = Join-Path $BinDir "photo-sorter.exe"
  if (Test-Path $Exe) {
    Push-Location $env:USERPROFILE
    & $Exe setup --remove-model *> $null
    Pop-Location
  }
  if ($HasUv) { & uv tool uninstall photo-sorter *> $null }
  Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $AppDir, $Exe
  Ok "App removed" $AppDir
  Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $ModelDir
  Ok "Image model removed" $ModelDir
  Remove-Item -Force -ErrorAction SilentlyContinue $Shortcut, (Join-Path $Desktop "Photo Sorter.lnk"), (Join-Path $Desktop "Photo Sorter.cmd")
  Ok "Desktop icon removed" $Shortcut
  Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $ConfDir, $Log
  Ok "Settings removed" $ConfDir

  Write-Host ""
  Write-Host "  ReeFile is removed." -ForegroundColor Green
  Write-Host "  uv, the tool that installed it, stays in $BinDir because other apps may use it." -ForegroundColor DarkGray
  Write-Host ""
}

Uninstall-PhotoSorter
