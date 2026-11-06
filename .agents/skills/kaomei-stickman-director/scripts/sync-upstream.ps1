param(
    [string]$Revision = "5841335db851b5f0352cf77955147393b97beabc",
    [switch]$Check,
    [switch]$Update
)

$ErrorActionPreference = "Stop"
$repository = "kaomei/stickman-video-director"
$skillRoot = Split-Path -Parent $PSScriptRoot
$upstreamDir = Join-Path $skillRoot "references\upstream"
$manifestPath = Join-Path $upstreamDir "SOURCE.json"
$sourcePaths = @(
    "LICENSE",
    "skills/directing-stickman-videos/SKILL.md",
    "skills/directing-stickman-videos/references/cozy-storytelling.md",
    "skills/directing-stickman-videos/references/examples.md",
    "skills/directing-stickman-videos/references/omni-flash-prompt-contract.md",
    "skills/directing-stickman-videos/references/prehistoric-dark-comedy.md",
    "skills/directing-stickman-videos/references/storyboard-template.md",
    "skills/directing-stickman-videos/references/style-catalog.md",
    "skills/directing-stickman-videos/references/voice-continuity.md"
)

function Get-FileSha256([string]$Path) {
    return (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
}

if ($Check -and $Update) { throw "Use either -Check or -Update." }
if (-not $Update) {
    if (-not (Test-Path -LiteralPath $manifestPath)) {
        throw "Missing upstream manifest: $manifestPath"
    }
    $manifest = Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
    if ($manifest.revision -notmatch '^[0-9a-f]{40}$') { throw "Invalid pinned revision." }
    if (@($manifest.files).Count -ne $sourcePaths.Count) { throw "Unexpected file count." }
    foreach ($sourcePath in $sourcePaths) {
        $entries = @($manifest.files | Where-Object { $_.upstream_path -eq $sourcePath })
        if ($entries.Count -ne 1) { throw "Missing or duplicate manifest entry: $sourcePath" }
        $name = Split-Path -Leaf $sourcePath
        if ($entries[0].local_name -ne $name) { throw "Unexpected local name: $sourcePath" }
        $localPath = Join-Path $upstreamDir $name
        if (-not (Test-Path -LiteralPath $localPath)) { throw "Missing file: $localPath" }
        if ((Get-FileSha256 $localPath) -ne $entries[0].sha256) {
            throw "Hash mismatch: $localPath"
        }
    }
    Write-Output "Pinned upstream snapshot matches SOURCE.json."
    exit 0
}

if ($Revision -notmatch '^[0-9a-f]{40}$') {
    throw "-Update requires a full 40-character commit SHA."
}

$stage = Join-Path $skillRoot (".sync-stage-" + [guid]::NewGuid().ToString('N'))
$skillRootFull = [IO.Path]::GetFullPath($skillRoot).TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
$stageFull = [IO.Path]::GetFullPath($stage)
if (-not $stageFull.StartsWith($skillRootFull, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing stage path outside skill root."
}

$headers = @{ "User-Agent" = "youtube-project-stickman-skill-sync" }
New-Item -ItemType Directory -Path $stage | Out-Null
try {
    $entries = @()
    foreach ($sourcePath in $sourcePaths) {
        $name = Split-Path -Leaf $sourcePath
        $target = Join-Path $stage $name
        $url = "https://raw.githubusercontent.com/$repository/$Revision/$sourcePath"
        Invoke-WebRequest -Headers $headers -Uri $url -OutFile $target -TimeoutSec 30
        if ((Get-Item -LiteralPath $target).Length -eq 0) { throw "Empty download: $sourcePath" }
        $entries += [ordered]@{
            upstream_path = $sourcePath
            local_name = $name
            sha256 = Get-FileSha256 $target
        }
    }
    if (-not (Get-Content -Raw -LiteralPath (Join-Path $stage 'LICENSE')).StartsWith('MIT License')) {
        throw "Expected MIT license was not found."
    }
    if (-not (Get-Content -Raw -LiteralPath (Join-Path $stage 'SKILL.md')).StartsWith('---')) {
        throw "Expected skill frontmatter was not found."
    }
    foreach ($entry in $entries) {
        Copy-Item -Force -LiteralPath (Join-Path $stage $entry.local_name) `
            -Destination (Join-Path $upstreamDir $entry.local_name)
    }
    $manifest = [ordered]@{
        repository = "https://github.com/$repository"
        revision = $Revision
        retrieved_at = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
        license = "MIT"
        files = $entries
    }
    $manifest | ConvertTo-Json -Depth 4 | Set-Content -Encoding utf8NoBOM -LiteralPath $manifestPath
    Write-Output "Synced reviewed upstream files at $Revision."
}
finally {
    $resolvedStage = [IO.Path]::GetFullPath($stage)
    if ((Test-Path -LiteralPath $stage) -and $resolvedStage.StartsWith(
        $skillRootFull, [StringComparison]::OrdinalIgnoreCase
    )) {
        Remove-Item -Recurse -Force -LiteralPath $resolvedStage
    }
}
