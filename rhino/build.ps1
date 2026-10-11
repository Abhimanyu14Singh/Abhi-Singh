# Build, test and package SkyFrame for Grasshopper (Windows).
#
#   .\rhino\build.ps1               # build + unit tests + dist\ + .yak
#   .\rhino\build.ps1 -NoTest
#   .\rhino\build.ps1 -Yak "C:\Program Files\Rhino 8\System\Yak.exe"
#
# Output: rhino\dist\net48\ and rhino\dist\net7.0\SkyFrame.Grasshopper.gha, manifest.yml, icon.png and
# skyframe-<version>-rh8_0-any.yak (install with the Package Manager: _PackageManager → gear → Install from file,
# or `yak install <file>`).
param(
    [switch]$NoTest,
    [string]$Yak = ""
)
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$dist = Join-Path $here "dist"
$env:DOTNET_CLI_TELEMETRY_OPTOUT = "1"
$env:DOTNET_NOLOGO = "1"

[xml]$props = Get-Content (Join-Path $here "Directory.Build.props")
$version = ($props.Project.PropertyGroup | Where-Object { $_.Version } | Select-Object -First 1).Version
Write-Host "SkyFrame for Grasshopper $version"

dotnet build (Join-Path $here "SkyFrame.Rhino.sln") -c Release
if ($LASTEXITCODE -ne 0) { throw "build failed" }
if (-not $NoTest) {
    dotnet test (Join-Path $here "SkyFrame.Exchange.Tests\SkyFrame.Exchange.Tests.csproj") -c Release --no-build
    if ($LASTEXITCODE -ne 0) { throw "tests failed" }
}

if (Test-Path $dist) { Remove-Item $dist -Recurse -Force }
New-Item -ItemType Directory -Force -Path (Join-Path $dist "net48"), (Join-Path $dist "net7.0") | Out-Null
$out = Join-Path $here "SkyFrame.Grasshopper\bin\Release"
Copy-Item (Join-Path $out "net48\SkyFrame.Grasshopper.gha") (Join-Path $dist "net48")
Copy-Item (Join-Path $out "net7.0\SkyFrame.Grasshopper.gha") (Join-Path $dist "net7.0")
Copy-Item (Join-Path $here "SkyFrame.Grasshopper\Icons\SkyFrame.png") (Join-Path $dist "icon.png")
(Get-Content (Join-Path $here "manifest.yml")) -replace '^version: .*', "version: $version" |
    Set-Content -Encoding utf8 (Join-Path $dist "manifest.yml")

if (-not $Yak) {
    $candidates = @(
        (Get-Command yak -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source),
        "C:\Program Files\Rhino 8\System\Yak.exe"
    ) | Where-Object { $_ -and (Test-Path $_) }
    if ($candidates) { $Yak = $candidates[0] }
}
if ($Yak) {
    Push-Location $dist
    try {
        & $Yak build --platform any
        if ($LASTEXITCODE -ne 0) { Write-Warning "yak build failed; the .gha files in dist\ are still usable" }
    } finally { Pop-Location }
    Get-ChildItem $dist -Filter *.yak | ForEach-Object { Write-Host "Package: $($_.FullName)" }
} else {
    Write-Warning "Yak.exe not found (install Rhino 8 or pass -Yak). dist\ is staged; the .gha files can be installed by drag-and-drop."
}
Write-Host "Done: $dist"
$global:LASTEXITCODE = 0
