#!/usr/bin/env bash
# Build, test and package SkyFrame for Grasshopper.
#
#   rhino/build.sh            # build (Release) + unit tests + stage dist/ + yak package if yak is available
#   rhino/build.sh --no-test  # skip the unit tests
#
# Output:
#   rhino/dist/net48/SkyFrame.Grasshopper.gha    (Rhino 8, .NET Framework runtime)
#   rhino/dist/net7.0/SkyFrame.Grasshopper.gha   (Rhino 8, .NET Core runtime — the default)
#   rhino/dist/manifest.yml, icon.png
#   rhino/dist/skyframe-<version>-rh8_0-any.yak   (only when the `yak` CLI is on PATH or $YAK points at it;
#                                                  yak ships with Rhino 8: "C:\Program Files\Rhino 8\System\Yak.exe")
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIST="$HERE/dist"
RUN_TESTS=1
[[ "${1:-}" == "--no-test" ]] && RUN_TESTS=0

export DOTNET_CLI_TELEMETRY_OPTOUT=1 DOTNET_NOLOGO=1

VERSION="$(sed -n 's:.*<Version>\(.*\)</Version>.*:\1:p' "$HERE/Directory.Build.props" | head -1)"
echo "SkyFrame for Grasshopper $VERSION"

dotnet build "$HERE/SkyFrame.Rhino.sln" -c Release
if [[ $RUN_TESTS == 1 ]]; then
  dotnet test "$HERE/SkyFrame.Exchange.Tests/SkyFrame.Exchange.Tests.csproj" -c Release --no-build
fi

rm -rf "$DIST"
mkdir -p "$DIST/net48" "$DIST/net7.0"
OUT="$HERE/SkyFrame.Grasshopper/bin/Release"
cp "$OUT/net48/SkyFrame.Grasshopper.gha" "$DIST/net48/"
cp "$OUT/net7.0/SkyFrame.Grasshopper.gha" "$DIST/net7.0/"
cp "$HERE/SkyFrame.Grasshopper/Icons/SkyFrame.png" "$DIST/icon.png"
sed "s/^version: .*/version: $VERSION/" "$HERE/manifest.yml" > "$DIST/manifest.yml"

YAK="${YAK:-$(command -v yak || true)}"
if [[ -n "$YAK" ]]; then
  (cd "$DIST" && "$YAK" build --platform any)
  ls -1 "$DIST"/*.yak
else
  echo "yak CLI not found: dist/ is staged; run 'yak build --platform any' inside it on a machine with Rhino 8."
fi
echo "Done: $DIST"
