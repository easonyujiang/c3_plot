param([Parameter(Mandatory=$true)][string]$KspRoot,
      [switch]$Install, [string]$ExpectedInstalledHash)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$managed = Join-Path $KspRoot 'KSP_x64_Data\Managed'
$buildDir = Join-Path $projectRoot 'output\curve_accuracy_20261010\plugin'
New-Item -ItemType Directory -Path $buildDir -Force | Out-Null
$target = Join-Path $buildDir 'C3MassValidation.dll'
$source = Join-Path $projectRoot 'game_plugin\C3MassValidation.cs'
$argsForCompiler = @('/nologo','/target:library',('/out:' + $target),$source)
foreach ($ref in @('Assembly-CSharp.dll','UnityEngine.dll','UnityEngine.CoreModule.dll')) {
    $argsForCompiler += ('/reference:' + (Join-Path $managed $ref))
}
& (Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe') @argsForCompiler
if ($LASTEXITCODE -ne 0) { throw 'Replay harness compilation failed' }
if ($Install) {
    $destinationDir = Join-Path $KspRoot 'GameData\C3MassValidation\Plugins'
    New-Item -ItemType Directory -Path $destinationDir -Force | Out-Null
    $destination = Join-Path $destinationDir 'C3MassValidation.dll'
    if (Test-Path -LiteralPath $destination) {
        $hash = (Get-FileHash -LiteralPath $destination).Hash
        if (-not $ExpectedInstalledHash -or $hash -ne $ExpectedInstalledHash) {
            throw 'Preserve unrecognized installed replay harness'
        }
        Copy-Item -LiteralPath $destination -Destination (Join-Path $buildDir ('previous_validation_' + $hash + '.dll'))
    }
    Copy-Item -LiteralPath $target -Destination $destination -Force
    Get-FileHash -LiteralPath $destination
}
