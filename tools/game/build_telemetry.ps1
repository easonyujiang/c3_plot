param(
    [Parameter(Mandatory=$true)][string]$KspRoot,
    [switch]$Install,
    [string]$ExpectedInstalledHash
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$managed = Join-Path $KspRoot 'KSP_x64_Data\Managed'
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$buildDir = Join-Path $projectRoot 'output\plugins'
New-Item -ItemType Directory -Force -Path $buildDir | Out-Null
$target = Join-Path $buildDir 'C3MassTelemetry.dll'
$sources = @((Join-Path $projectRoot 'game_plugin\C3MassTelemetry.cs'), (Join-Path $projectRoot 'game_plugin\C3EditorExporter.cs'))
$refs = @('Assembly-CSharp.dll','UnityEngine.dll','UnityEngine.CoreModule.dll','UnityEngine.PhysicsModule.dll','UnityEngine.UI.dll')
$compilerArgs = @('/nologo','/target:library',('/out:' + $target)) + $sources
foreach ($ref in $refs) { $compilerArgs += ('/reference:' + (Join-Path $managed $ref)) }
& $compiler @compilerArgs
if ($LASTEXITCODE -ne 0) { throw 'Telemetry compilation failed' }
Get-FileHash -LiteralPath $target -Algorithm SHA256
if ($Install) {
    $destination = Join-Path $KspRoot 'GameData\C3MassTelemetry\Plugins'
    New-Item -ItemType Directory -Force -Path $destination | Out-Null
    $installed = Join-Path $destination 'C3MassTelemetry.dll'
    if (Test-Path -LiteralPath $installed) {
        $priorHash = (Get-FileHash -LiteralPath $installed -Algorithm SHA256).Hash
        if (-not $ExpectedInstalledHash -or $priorHash -ne $ExpectedInstalledHash) {
            throw 'Existing telemetry plugin hash differs or was not supplied; inspect it first'
        }
        Copy-Item -LiteralPath $installed -Destination (Join-Path $buildDir ('previous_' + $priorHash + '.dll'))
    }
    Copy-Item -LiteralPath $target -Destination $installed -Force
    Get-FileHash -LiteralPath $installed -Algorithm SHA256
}
