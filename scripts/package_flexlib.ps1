# Copies the FlexLib DLLs into runtime/flexlib/ so pythonnet can load them.
#
# In SmartSDR v3, FlexLib.dll lived as a loose DLL under
# "C:\Program Files\FlexRadio Systems\SmartSDR v<x>\". In v4, SmartSDR ships
# as monolithic self-contained .exes (CAT.exe, DAX.exe, SmartSDR.exe) with
# FlexLib bundled inside — they cannot be unblocked/copied as loose DLLs.
#
# For v4, download the official FlexLib redistributable (~1.18 MB) from:
#   https://www.flexradio.com/software/smartsdr-v4-x-api-flexlib/
# and pass either the zip file or the extracted directory.
#
# Usage:
#   .\scripts\package_flexlib.ps1 -FlexLibZip "$env:USERPROFILE\Downloads\FlexLib-v4.2.18.zip"
#   .\scripts\package_flexlib.ps1 -SourceDir  "$env:USERPROFILE\Downloads\FlexLib-v4.2.18\"
#   .\scripts\package_flexlib.ps1 -SmartSdrRoot "C:\Program Files\FlexRadio Systems"   # legacy v3 layout
param(
    [string]$FlexLibZip,
    [string]$SourceDir,
    [string]$SmartSdrRoot = "C:\Program Files\FlexRadio Systems"
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path $PSScriptRoot -Parent
$target = Join-Path $repoRoot "runtime\flexlib"
New-Item -ItemType Directory -Force $target | Out-Null

$wanted = @("FlexLib.dll", "Util.dll", "Vita.dll", "FlexUtilApi.dll")

function Copy-FlexLibDlls {
    param([string]$From)
    $copied = 0
    foreach ($name in $wanted) {
        $src = Join-Path $From $name
        if (Test-Path $src) {
            Copy-Item $src $target -Force
            Unblock-File (Join-Path $target $name)
            Write-Host "Copied $name"
            $copied++
        } else {
            Write-Warning "Missing in source: $name (continuing)"
        }
    }
    return $copied
}

# Path 1: extracted directory
if ($SourceDir) {
    if (-not (Test-Path $SourceDir)) {
        Write-Error "SourceDir not found: $SourceDir"
        exit 1
    }
    Write-Host "Source: $SourceDir"
    Write-Host "Target: $target"
    $n = Copy-FlexLibDlls -From $SourceDir
    if ($n -eq 0) {
        Write-Error "No matching DLLs found under $SourceDir. Expected one of: $($wanted -join ', ')."
        exit 1
    }
    Write-Host "Done."
    exit 0
}

# Path 2: zip file
if ($FlexLibZip) {
    if (-not (Test-Path $FlexLibZip)) {
        Write-Error "FlexLibZip not found: $FlexLibZip"
        exit 1
    }
    $tmp = Join-Path $env:TEMP ("flexlib_extract_" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Force $tmp | Out-Null
    try {
        Write-Host "Extracting $FlexLibZip -> $tmp"
        Expand-Archive -Path $FlexLibZip -DestinationPath $tmp -Force
        # Some packages put DLLs at root, others nest them. Find first dir with FlexLib.dll.
        $dllHost = Get-ChildItem -Path $tmp -Recurse -Filter "FlexLib.dll" -ErrorAction SilentlyContinue |
                   Select-Object -First 1
        if (-not $dllHost) {
            Write-Error "FlexLib.dll not found anywhere inside $FlexLibZip. Wrong package?"
            exit 1
        }
        $sourceDirFromZip = Split-Path $dllHost.FullName -Parent
        Write-Host "Source: $sourceDirFromZip"
        Write-Host "Target: $target"
        Copy-FlexLibDlls -From $sourceDirFromZip | Out-Null
        Write-Host "Done."
        exit 0
    } finally {
        Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
    }
}

# Path 3: legacy v3 layout (loose DLLs under SmartSDR install)
if (Test-Path $SmartSdrRoot) {
    $smartSdrDir = Get-ChildItem $SmartSdrRoot -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like "SmartSDR v*" } |
        Sort-Object Name -Descending |
        Select-Object -First 1

    if ($smartSdrDir) {
        Write-Host "Source: $($smartSdrDir.FullName)  (legacy v3 layout)"
        Write-Host "Target: $target"
        $n = Copy-FlexLibDlls -From $smartSdrDir.FullName
        if ($n -gt 0) {
            Write-Host "Done."
            exit 0
        }
        Write-Warning "Found '$($smartSdrDir.Name)' but no loose DLLs inside. This is the v4 layout."
    }
}

Write-Error @"
Could not locate FlexLib DLLs. SmartSDR v4 ships FlexLib bundled inside
self-contained .exes — loose DLLs are no longer extractable from the install.

Download the official FlexLib redistributable from:
  https://www.flexradio.com/software/smartsdr-v4-x-api-flexlib/

Then re-run this script with -FlexLibZip pointing at the downloaded .zip,
or -SourceDir pointing at the extracted folder. Example:

  .\scripts\package_flexlib.ps1 -FlexLibZip "`$env:USERPROFILE\Downloads\FlexLib-v4.2.18.zip"
"@
exit 1
