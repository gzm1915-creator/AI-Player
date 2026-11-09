param(
    [Parameter(Mandatory=$true)][string]$GameDir,
    [switch]$CheckOnly
)
$ErrorActionPreference = 'Stop'
function Read-DllHash([string]$Path) {
    $stream = [IO.File]::OpenRead($Path)
    $hasher = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($hasher.ComputeHash($stream))).Replace('-', '').ToLowerInvariant() }
    finally { $stream.Dispose(); $hasher.Dispose() }
}
$gameRoot = (Resolve-Path -LiteralPath $GameDir).Path
foreach ($required in @('Among Us.exe', 'BepInEx\core\BepInEx.Unity.IL2CPP.dll')) {
    if (-not (Test-Path -LiteralPath (Join-Path $gameRoot $required) -PathType Leaf)) {
        throw "Expected Among Us with BepInEx: $required"
    }
}
if (@(Get-Process -Name 'Among Us' -ErrorAction SilentlyContinue).Count -gt 0) {
    throw 'Exit Among Us before updating the game component.'
}
$source = Join-Path $PSScriptRoot 'game-plugin\AUTelemetryProbe.dll'
$target = Join-Path $gameRoot 'BepInEx\plugins\AUTelemetryProbe\AUTelemetryProbe.dll'
if (-not (Test-Path -LiteralPath $target -PathType Leaf)) {
    throw 'No existing game component. Use install.ps1 for a fresh installation.'
}
$entry = @(Get-Content -LiteralPath (Join-Path $PSScriptRoot 'SHA256SUMS.txt') | Where-Object { $_ -match '^[0-9a-f]{64}  game-plugin/AUTelemetryProbe.dll$' })
if ($entry.Count -ne 1) { throw 'Game DLL integrity entry is missing or ambiguous.' }
$expected = $entry[0].Substring(0, 64)
if ((Read-DllHash $source) -ne $expected) {
    throw 'Game DLL integrity check failed. Download the complete package again.'
}
$routePath = Join-Path (Split-Path -Parent $target) 'data\skeld-route-observations.json'
$routeJson = $null
if (Test-Path -LiteralPath $routePath -PathType Leaf) {
    $route = [IO.File]::ReadAllText($routePath) | ConvertFrom-Json
    $aliasId = 'skeld.weapons.clear-asteroids'
    if (@($route.anchors | Where-Object { $_.anchorId -eq $aliasId }).Count -eq 0) {
        $baseAnchor = @($route.anchors | Where-Object { $_.anchorId -eq 'weapons.smooth_arrival' })
        $recorded = @($route.routeSegments | Where-Object {
            $_.status -eq 'observed_success' -and
            ($_.fromAnchorId -eq 'weapons.smooth_arrival' -or $_.toAnchorId -eq 'weapons.smooth_arrival')
        })
        if ($baseAnchor.Count -eq 1 -and $recorded.Count -gt 0 -and
            $null -ne $baseAnchor[0].position.x -and $null -ne $baseAnchor[0].position.y) {
            # Add a console alias only on the user's existing recorded node.
            # Do not overwrite geometry, metadata, or a customized alias.
            $route.anchors = @($route.anchors) + [pscustomobject]@{
                anchorId = $aliasId
                position = [pscustomobject]@{ x = $baseAnchor[0].position.x; y = $baseAnchor[0].position.y }
                roomId = 'Weapons'
                kind = 'task'
            }
            $routeJson = $route | ConvertTo-Json -Depth 100
        }
    }
}
if ($CheckOnly) { Write-Output 'Game update checks passed; no files changed.'; return }
$backup = $target + '.backup-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff')
Copy-Item -LiteralPath $target -Destination $backup
$routeBackup = $null
$routeTemp = $null
try {
    Copy-Item -LiteralPath $source -Destination $target -Force
    if ((Read-DllHash $target) -ne $expected) {
        throw 'Installed game DLL hash mismatch.'
    }
    if ($null -ne $routeJson) {
        $routeBackup = $routePath + '.backup-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff')
        Copy-Item -LiteralPath $routePath -Destination $routeBackup
        $routeTemp = $routePath + '.' + [Guid]::NewGuid().ToString('N') + '.tmp'
        [IO.File]::WriteAllText($routeTemp, $routeJson, (New-Object Text.UTF8Encoding($false)))
        [IO.File]::Replace($routeTemp, $routePath, $routeBackup)
        Write-Output 'Added the missing asteroid task alias on an existing recorded route node.'
    }
} catch {
    Copy-Item -LiteralPath $backup -Destination $target -Force
    if ($null -ne $routeBackup) { Copy-Item -LiteralPath $routeBackup -Destination $routePath -Force }
    throw
} finally {
    if ($null -ne $routeTemp -and (Test-Path -LiteralPath $routeTemp -PathType Leaf)) {
        Remove-Item -LiteralPath $routeTemp
    }
}
Write-Output 'Game component updated. Settings, existing route geometry and history were preserved.'
Write-Output ('Previous DLL backup: ' + $backup)
