# Removes Photo Sorter from this PC (not tested on Windows yet): the app, the image model, the Desktop icon and its
# settings. Your photos, their folders and their category lists (CATEGORIES.md) are never touched.
# Right-click > Run with PowerShell. (Plain ASCII on purpose: Windows PowerShell 5.1 misreads other characters.)

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

Clear-Host
Write-Host ""
Write-Host "  $Line" -ForegroundColor Cyan
Write-Host "   ><(((('>   PHOTO SORTER  -  remove" -ForegroundColor Cyan
Write-Host "  $Line" -ForegroundColor Cyan
Write-Host ""
Write-Host "  This removes"
Write-Host ""
Write-Host "   the app                     $AppDir"
Write-Host "   the image model             $ModelDir"
Write-Host "   the Desktop icon            $Launcher"
Write-Host "   settings, Google sign-in    $ConfDir"
Write-Host ""
Write-Host "  Your photos, their folders and their category lists stay exactly where they are."
Write-Host ""
Read-Host "  Press Enter to remove Photo Sorter, or close this window to cancel" | Out-Null

Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like "*photo-sorter*serve*" } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Ok "Closed Photo Sorter" "" }
if (Get-Command photo-sorter -ErrorAction SilentlyContinue) {
  Push-Location $env:USERPROFILE
  photo-sorter setup --remove-model *> $null
  Pop-Location
}
if ($HasUv) { uv tool uninstall photo-sorter *> $null }
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $AppDir, "$env:USERPROFILE\.local\bin\photo-sorter.exe"
Ok "App removed" $AppDir
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $ModelDir
Ok "Image model removed" $ModelDir
Remove-Item -Force -ErrorAction SilentlyContinue $Launcher
Ok "Desktop icon removed" $Launcher
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $ConfDir, $Log
Ok "Settings removed" $ConfDir

Write-Host ""
Write-Host "  Photo Sorter is removed." -ForegroundColor Green
Write-Host "  uv, the tool that installed it, stays in $env:USERPROFILE\.local\bin because other apps may use it." -ForegroundColor DarkGray
Write-Host ""
Read-Host "  Press Enter to close" | Out-Null
