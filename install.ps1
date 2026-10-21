# ReeFile installer for Windows 10 and 11. Open PowerShell (no need for "Run as administrator") and paste:
#
#   powershell -ExecutionPolicy ByPass -NoProfile -c "irm https://raw.githubusercontent.com/alegalguera/image-classification/main/install.ps1 | iex"
#
# It says what it will install and where, and asks before starting. Running it again updates ReeFile.
# Everything goes into your user folder, and nothing needs to be installed first: uv (the free tool it installs
# ReeFile with) brings its own copy of Python, separate from any other Python or conda on this PC.
# To remove it: uninstall.ps1, the same way.
#
# For developers and automation:
#   $env:PHOTO_SORTER_SOURCE = <folder or URL>   install from a local copy (or another archive) instead of GitHub
#   $env:PHOTO_SORTER_UNATTENDED = "1"           don't ask and don't open the app at the end
# (Plain ASCII on purpose: Windows PowerShell 5.1 misreads other characters.)

$Repo = "https://github.com/alegalguera/image-classification"
$Raw = "https://raw.githubusercontent.com/alegalguera/image-classification/main"
$Log = Join-Path $env:LOCALAPPDATA "photo-sorter-install.log"
$Line = "=" * 64

function Ok([string]$What, [string]$Where) {
  Write-Host ("  OK  {0,-22} " -f $What) -ForegroundColor Green -NoNewline
  Write-Host $Where -ForegroundColor DarkGray
}

function Get-FolderMB([string]$Dir) {
  if (-not (Test-Path $Dir)) { return 0 }
  $sum = (Get-ChildItem $Dir -Recurse -File -Force -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum
  if ($sum) { return [math]::Floor($sum / 1MB) } else { return 0 }
}

function Test-VCRuntime {
  # PyTorch needs the Microsoft Visual C++ Runtime, which most PCs already have
  foreach ($key in "HKLM:\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64",
                   "HKLM:\SOFTWARE\WOW6432Node\Microsoft\VisualStudio\14.0\VC\Runtimes\x64") {
    $v = Get-ItemProperty -Path $key -ErrorAction SilentlyContinue
    if ($v -and $v.Installed -eq 1) { return $true }
  }
  return $false
}

function Invoke-Quietly([string]$Label, [string]$Exe, [string[]]$Arguments, [string]$WatchDir) {
  # runs a program with its output going to the log, showing a timer (and download progress for WatchDir)
  $out = [IO.Path]::GetTempFileName(); $err = [IO.Path]::GetTempFileName()
  $quoted = $Arguments | ForEach-Object { if ($_ -match '\s') { '"' + $_ + '"' } else { $_ } }
  $p = Start-Process -FilePath $Exe -ArgumentList $quoted -WorkingDirectory $env:USERPROFILE -NoNewWindow -PassThru `
         -RedirectStandardOutput $out -RedirectStandardError $err
  $null = $p.Handle
  $start = Get-Date
  $base = if ($WatchDir) { Get-FolderMB $WatchDir } else { 0 }
  $tick = 0
  while (-not $p.HasExited) {
    $extra = ""
    if ($WatchDir -and ($tick % 3 -eq 0)) {
      $mb = (Get-FolderMB $WatchDir) - $base
      $script:Progress = if ($mb -gt 0) { "  $mb MB of about 1,500 MB" } else { "  connecting to huggingface.co..." }
    }
    if ($WatchDir) { $extra = $script:Progress }
    $t = (Get-Date) - $start
    Write-Host -NoNewline ("`r  ... {0}  {1:mm\:ss}{2}        " -f $Label, $t, $extra)
    Start-Sleep -Milliseconds 700
    $tick++
  }
  $p.WaitForExit()
  Write-Host -NoNewline ("`r" + (" " * 110) + "`r")
  Get-Content $out, $err -ErrorAction SilentlyContinue | Add-Content -Path $Log
  Remove-Item $out, $err -ErrorAction SilentlyContinue
  return $p.ExitCode
}

function Install-PhotoSorter {
  $ErrorActionPreference = "Continue"            # messages from the tools it runs are not errors
  $ProgressPreference = "SilentlyContinue"
  [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
  $Unattended = [bool]$env:PHOTO_SORTER_UNATTENDED
  $Arch = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
  if ($Arch -ne "AMD64") {
    throw "This PC has an $Arch processor. ReeFile needs a standard 64-bit PC (Intel or AMD): PyTorch, the image-recognition library it uses, has no version for this processor yet."
  }
  $BinDir = Join-Path $env:USERPROFILE ".local\bin"
  $env:Path = "$BinDir;$env:Path"
  $env:UV_PYTHON_PREFERENCE = "only-managed"    # always its own Python, never conda's or another one on this PC
  $Source = if ($env:PHOTO_SORTER_SOURCE) { $env:PHOTO_SORTER_SOURCE } else { "$Repo/archive/refs/heads/main.zip" }
  $From = "github.com"
  $Spec = "photo-sorter[researcher] @ $Source"
  if (Test-Path $Source -PathType Container) {
    $Source = (Resolve-Path $Source).Path
    $From = $Source
    $Spec = "photo-sorter[researcher] @ file:///" + ($Source -replace '\\', '/')
  }
  $HasUv = [bool](Get-Command uv -ErrorAction SilentlyContinue)
  $ToolDir = if ($HasUv) { (& uv tool dir 2>$null) } else { Join-Path $env:APPDATA "uv\tools" }
  $AppDir = Join-Path $ToolDir "photo-sorter"
  $HfHome = if ($env:HF_HOME) { $env:HF_HOME } else { Join-Path $env:USERPROFILE ".cache\huggingface" }
  $ModelDir = Join-Path $HfHome "hub\models--google--siglip2-base-patch16-224"
  $ConfDir = if ($env:PHOTO_SORTER_HOME) { $env:PHOTO_SORTER_HOME } else { Join-Path $env:USERPROFILE ".config\photo-sorter" }
  $Desktop = [Environment]::GetFolderPath("Desktop")
  $Shortcut = Join-Path $Desktop "ReeFile.lnk"
  Set-Content -Path $Log -Value "photo-sorter install log"
  $Free = [math]::Floor((Get-PSDrive -Name $env:USERPROFILE.Substring(0, 1)).Free / 1GB)

  if (-not $Unattended) { Clear-Host }
  Write-Host ""
  Write-Host "  $Line" -ForegroundColor Cyan
  Write-Host "   ><(((('>   ReeFile  -  installer" -ForegroundColor Cyan
  Write-Host "   Sorts research photos into folders, right here on this PC."
  Write-Host "  $Line" -ForegroundColor Cyan
  Write-Host ""
  Write-Host "  What it will do"
  Write-Host ""
  if (Test-Path $AppDir) { Write-Host "   1  Update the app                                   about 1 GB" }
  else { Write-Host "   1  Install the app                                  about 1 GB" }
  Write-Host "      from $From and pypi.org  ->  $AppDir" -ForegroundColor DarkGray
  if (-not $HasUv) { Write-Host "      with uv, the free tool that installs it and brings its own Python (from astral.sh  ->  $BinDir)" -ForegroundColor DarkGray }
  if (-not (Test-VCRuntime)) { Write-Host "      and the Microsoft Visual C++ Runtime, which it needs (Windows will ask for permission)" -ForegroundColor DarkGray }
  if (Test-Path $ModelDir) { Write-Host "   2  Image model (SigLIP 2, by Google)                already here" }
  else { Write-Host "   2  Download the image model (SigLIP 2, by Google)   1.4 GB" }
  Write-Host "      from huggingface.co  ->  $ModelDir" -ForegroundColor DarkGray
  Write-Host "   3  Put a 'ReeFile' icon on your Desktop"
  Write-Host "      ->  $Shortcut" -ForegroundColor DarkGray
  Write-Host ""
  Write-Host "  Everything stays in your user folder, separate from any other Python or conda on this PC."
  Write-Host "  Your photos are never uploaded: ReeFile runs on this PC."
  if ($Free -lt 4) { Write-Host "  Free space: $Free GB. About 3 GB are needed; free up some space first if you can." -ForegroundColor Yellow }
  else { Write-Host "  Free space: $Free GB (about 3 GB needed)" -ForegroundColor DarkGray }
  Write-Host "  A log of every step is kept in $Log" -ForegroundColor DarkGray
  Write-Host ""
  if (-not $Unattended) { $null = Read-Host "  Press Enter to start, or close this window to cancel" }

  # 1. the app
  Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like "*photo-sorter*serve*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; Ok "Closed ReeFile" "it restarts with the new version at the end" }
  if (-not (Test-VCRuntime)) {
    Write-Host "  ... Installing the Microsoft Visual C++ Runtime (Windows asks for permission)"
    $vc = Join-Path $env:TEMP "vc_redist.x64.exe"
    Invoke-WebRequest "https://aka.ms/vs/17/release/vc_redist.x64.exe" -OutFile $vc -UseBasicParsing
    try { Start-Process $vc -ArgumentList "/install", "/passive", "/norestart" -Verb RunAs -Wait } catch { }
    if (-not (Test-VCRuntime)) { throw "The Microsoft Visual C++ Runtime could not be installed. Install it from https://aka.ms/vs/17/release/vc_redist.x64.exe and run this again." }
    Ok "Visual C++ Runtime" "installed"
  }
  if (-not $HasUv) {
    Write-Host "  ... Installing uv"
    $code = Invoke-Quietly "Installing uv" "powershell" @("-ExecutionPolicy", "ByPass", "-NoProfile", "-c", "`$env:UV_NO_MODIFY_PATH='1'; irm https://astral.sh/uv/install.ps1 | iex") ""
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { throw "Could not install uv. Check the internet connection and run the installer again." }
    Ok "uv installed" (Join-Path $BinDir "uv.exe")
  }
  $Uv = (Get-Command uv).Source
  $code = Invoke-Quietly "Installing the app (a few minutes the first time)" $Uv @("tool", "install", "--force", "--reinstall-package", "photo-sorter", "--python", "3.12", $Spec) ""
  if ($code -ne 0) { throw "Installing the app failed." }
  $AppDir = Join-Path (& $Uv tool dir 2>$null) "photo-sorter"
  Ok "App installed" $AppDir
  if ((Test-Path $Source -PathType Container) -and (Test-Path (Join-Path $Source "client_secret.json"))) {
    New-Item -ItemType Directory -Force -Path $ConfDir | Out-Null
    Copy-Item (Join-Path $Source "client_secret.json") $ConfDir -Force
    Ok "Google sign-in file" $ConfDir
  }

  # 2. the image model
  $Exe = Join-Path $BinDir "photo-sorter.exe"
  if (Test-Path $ModelDir) { $code = Invoke-Quietly "Checking the image model" $Exe @("setup") "" }
  else { $code = Invoke-Quietly "Downloading the image model" $Exe @("setup") $HfHome }
  if ($code -ne 0) { throw "Getting the image model failed." }
  Ok "Image model ready" $ModelDir

  # 3. the Desktop icon
  Remove-Item (Join-Path $Desktop "Photo Sorter.cmd"), (Join-Path $Desktop "Photo Sorter.lnk") -ErrorAction SilentlyContinue   # older names
  $lnk = (New-Object -ComObject WScript.Shell).CreateShortcut($Shortcut)
  $lnk.TargetPath = $Exe
  $lnk.Arguments = "serve --open"
  $lnk.WorkingDirectory = $env:USERPROFILE
  $lnk.Description = "Start ReeFile (it opens in your browser)"
  $lnk.Save()
  Ok "Desktop icon" $Shortcut

  Write-Host ""
  Write-Host "  $Line" -ForegroundColor Cyan
  Write-Host "   ReeFile is installed" -ForegroundColor Green
  Write-Host ""
  Write-Host "   Start it    double-click 'ReeFile' on your Desktop"
  Write-Host "   Update it   paste the same line again"
  Write-Host "   Remove it   paste:  powershell -ExecutionPolicy ByPass -NoProfile -c `"irm $Raw/uninstall.ps1 | iex`""
  Write-Host "  $Line" -ForegroundColor Cyan
  Write-Host ""
  if (-not $Unattended) {
    Write-Host "  Opening ReeFile now: it shows up in your browser. Keep its small window open while you work."
    Start-Process $Shortcut
  }
}

try {
  Install-PhotoSorter
} catch {
  Write-Host ""
  Write-Host "  X  $($_.Exception.Message)" -ForegroundColor Red
  if (Test-Path $Log) {
    Write-Host "     Last lines of the log:" -ForegroundColor DarkGray
    Get-Content $Log -Tail 6 | ForEach-Object { Write-Host ("       " + "$_".Substring(0, [math]::Min(160, "$_".Length))) -ForegroundColor DarkGray }
    Write-Host "     The full details are in $Log"
  }
  Write-Host "     Send that file, or a screenshot of this window, to whoever helps you with ReeFile."
  Write-Host ""
  if ($env:PHOTO_SORTER_UNATTENDED) { exit 1 }
}
