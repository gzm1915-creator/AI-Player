param(
    [Parameter(Mandatory=$true)][string]$GameDir
)
$ErrorActionPreference = 'Stop'
function Require-File([string]$Path, [string]$Message) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw $Message }
}
Require-File (Join-Path $GameDir 'Among Us.exe') 'GameDir must contain Among Us.exe.'
Require-File (Join-Path $GameDir 'GameAssembly.dll') 'This adapter requires the Windows IL2CPP build of Among Us.'
Require-File (Join-Path $GameDir 'BepInEx\core\BepInEx.Unity.IL2CPP.dll') 'Install BepInEx 6 Unity IL2CPP x64 first; see README.md.'
if ((Get-Process -Name 'Among Us' -ErrorAction SilentlyContinue)) {
    throw 'Close Among Us before installing the game-side component.'
}
$gameRoot = [IO.Path]::GetFullPath($GameDir)
$probe = Join-Path $gameRoot 'BepInEx\plugins\AUTelemetryProbe'
$configPath = Join-Path $gameRoot 'BepInEx\config\com.dsh.autelprobe.cfg'
if (Test-Path -LiteralPath $configPath) {
    throw 'An existing AUTelemetryProbe configuration was found. This installer targets an unmodified game plus BepInEx. Back up and migrate the existing plugin/config manually; it will not be overwritten.'
}
$manifest = Join-Path $PSScriptRoot 'SHA256SUMS.txt'
Require-File $manifest 'Package manifest missing.'
foreach ($line in Get-Content -LiteralPath $manifest) {
    if ($line -match '^([0-9a-f]{64})  (.+)$') {
        $hash = $matches[1]
        $relative = $matches[2]
        $file = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot $relative))
        if (-not $file.StartsWith(([IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\') + '\'), [StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid manifest path.' }
        $algorithm = [Security.Cryptography.SHA256]::Create()
        $stream = [IO.File]::OpenRead($file)
        try { $actual = [BitConverter]::ToString($algorithm.ComputeHash($stream)).Replace('-', '').ToLowerInvariant() }
        finally { $stream.Dispose(); $algorithm.Dispose() }
        if ($actual -ne $hash) { throw "Integrity check failed: $relative" }
    }
}
if (Test-Path -LiteralPath $probe) {
    throw 'Adapter target already exists; back it up and follow manual update instructions.'
}
# This installer writes to the game only. N.E.K.O. uses its native import flow.
New-Item -ItemType Directory -Path $probe -Force | Out-Null
Copy-Item -Path (Join-Path $PSScriptRoot 'game-plugin\*') -Destination $probe -Recurse
Write-Host 'Game-side adapter installed. N.E.K.O. files were not modified.'
Write-Host 'Next connect your AI host. For N.E.K.O., import the included .neko-plugin and set the game directory in its panel. Other hosts: see HOST_INTEGRATION.md.'
