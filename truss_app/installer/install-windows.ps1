# Truss Lab installer for Windows (run via "Install on Windows.bat").
# Copies the app into your user folder and adds Desktop + Start-menu shortcuts.
# No admin rights needed. Nothing is downloaded.

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$src  = Join-Path $here 'TrussLab.html'

Write-Host ''
Write-Host '  Truss Lab installer' -ForegroundColor Cyan
Write-Host '  -------------------'

if (-not (Test-Path $src)) {
  Write-Host '  Could not find TrussLab.html next to this installer.' -ForegroundColor Red
  Write-Host '  Please unzip (Extract All) the whole folder first, then run the installer again.'
  exit 1
}

$dest = Join-Path $env:LOCALAPPDATA 'TrussLab'
New-Item -ItemType Directory -Force -Path $dest | Out-Null
$app = Join-Path $dest 'TrussLab.html'
Copy-Item -Force $src $app
Write-Host "  Copied app to: $app"

# Shortcuts are a convenience: if they can't be made (e.g. a locked-down PC),
# say so and still open the app rather than failing the whole install.
$made = 0
try {
  $ws = New-Object -ComObject WScript.Shell
  $places = @(
    [Environment]::GetFolderPath('Desktop'),
    (Join-Path ([Environment]::GetFolderPath('StartMenu')) 'Programs')
  )
  foreach ($dir in $places) {
    if (-not $dir -or -not (Test-Path $dir)) { continue }
    $lnk = $ws.CreateShortcut((Join-Path $dir 'Truss Lab.lnk'))
    $lnk.TargetPath  = $app
    $lnk.Description = 'Truss Lab - virtual work truss explorer'
    $lnk.Save()
    Write-Host "  Shortcut added: $dir"
    $made++
  }
} catch {
  Write-Host "  (Could not create shortcuts: $($_.Exception.Message))" -ForegroundColor Yellow
}

Write-Host ''
if ($made -gt 0) {
  Write-Host '  Done! Open Truss Lab any time from the Desktop or the Start menu.' -ForegroundColor Green
} else {
  Write-Host "  Installed. Open it any time by double-clicking: $app" -ForegroundColor Green
}
Write-Host '  Opening it now in your web browser...'
try { Start-Process $app }
catch { Write-Host "  (Couldn't open a browser automatically - double-click $app instead.)" -ForegroundColor Yellow }
