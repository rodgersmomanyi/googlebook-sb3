# Compiles gbdisk.cs -> gbdisk.exe with the .NET Framework 4 C# compiler that ships with Windows (no install).
# Writes only gbdisk.exe next to this script.
$ErrorActionPreference = 'Stop'
$csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path $csc)) { $csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe' }
if (-not (Test-Path $csc)) { throw "csc.exe (.NET Framework 4) not found" }
$src = Join-Path $PSScriptRoot 'gbdisk.cs'
$out = Join-Path $PSScriptRoot 'gbdisk.exe'
& $csc /nologo /optimize+ /platform:x64 /target:exe "/out:$out" /r:System.Web.Extensions.dll /r:System.Core.dll $src
if ($LASTEXITCODE -ne 0) { throw "csc failed ($LASTEXITCODE)" }
Write-Host "built $out"
