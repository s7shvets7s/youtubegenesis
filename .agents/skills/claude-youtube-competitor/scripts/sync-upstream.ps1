param(
    [string]$Revision = "84c3fa6805200632fadb4fe2795ef265511271c7",
    [switch]$Check
)

$ErrorActionPreference = "Stop"

$repository = "AgriciDaniel/claude-youtube"
$sourcePath = "skills/claude-youtube/sub-skills/competitor.md"
$skillRoot = Split-Path -Parent $PSScriptRoot
$upstreamDir = Join-Path $skillRoot "references\upstream"
$competitorPath = Join-Path $upstreamDir "competitor.md"
$licensePath = Join-Path $upstreamDir "LICENSE"
$manifestPath = Join-Path $upstreamDir "SOURCE.json"

function Get-FileSha256([string]$Path) {
    return (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
}

if ($Check) {
    if (-not (Test-Path -LiteralPath $manifestPath)) {
        throw "Missing upstream manifest: $manifestPath"
    }

    $manifest = Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
    $expectedFiles = @("competitor.md", "LICENSE")
    foreach ($expectedName in $expectedFiles) {
        $matchingEntries = @($manifest.files | Where-Object {
            $_.local_name -eq $expectedName
        })
        if ($matchingEntries.Count -ne 1) {
            throw "SOURCE.json must contain exactly one entry for $expectedName."
        }
        $entry = $matchingEntries[0]
        $localPath = Join-Path $upstreamDir $expectedName
        if (-not (Test-Path -LiteralPath $localPath)) {
            throw "Missing vendored file: $localPath"
        }
        $actualHash = Get-FileSha256 $localPath
        if ($actualHash -ne $entry.sha256) {
            throw "Hash mismatch for $localPath"
        }
    }

    Write-Output "Upstream snapshot matches SOURCE.json."
    exit 0
}

$headers = @{ "User-Agent" = "youtube-project-upstream-sync" }
$commitApi = "https://api.github.com/repos/$repository/commits/$Revision"
$commit = Invoke-RestMethod -Headers $headers -Uri $commitApi
$resolvedRevision = [string]$commit.sha
if ($resolvedRevision -notmatch "^[0-9a-f]{40}$") {
    throw "GitHub returned an invalid commit SHA."
}

$temporaryRoot = [IO.Path]::GetFullPath(
    (Join-Path ([IO.Path]::GetTempPath()) "youtube-competitor-$([guid]::NewGuid())")
)
$allowedTempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
if (-not $temporaryRoot.StartsWith($allowedTempRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to use a temporary directory outside the system temp root."
}

New-Item -ItemType Directory -Path $temporaryRoot | Out-Null
try {
    $downloadedCompetitor = Join-Path $temporaryRoot "competitor.md"
    $downloadedLicense = Join-Path $temporaryRoot "LICENSE"
    $rawBase = "https://raw.githubusercontent.com/$repository/$resolvedRevision"

    Invoke-WebRequest -Headers $headers -Uri "$rawBase/$sourcePath" -OutFile $downloadedCompetitor
    Invoke-WebRequest -Headers $headers -Uri "$rawBase/LICENSE" -OutFile $downloadedLicense

    $competitorText = Get-Content -Raw -LiteralPath $downloadedCompetitor
    $licenseText = Get-Content -Raw -LiteralPath $downloadedLicense
    if (-not $competitorText.StartsWith("# Competitor Sub-Skill")) {
        throw "Downloaded competitor.md does not have the expected heading."
    }
    if (-not $licenseText.StartsWith("MIT License")) {
        throw "Downloaded repository license is not the expected MIT license."
    }

    New-Item -ItemType Directory -Force -Path $upstreamDir | Out-Null
    Copy-Item -Force -LiteralPath $downloadedCompetitor -Destination $competitorPath
    Copy-Item -Force -LiteralPath $downloadedLicense -Destination $licensePath

    $manifest = [ordered]@{
        repository = "https://github.com/$repository"
        revision = $resolvedRevision
        retrieved_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        license = "MIT"
        files = @(
            [ordered]@{
                upstream_path = $sourcePath
                local_name = "competitor.md"
                sha256 = Get-FileSha256 $competitorPath
            },
            [ordered]@{
                upstream_path = "LICENSE"
                local_name = "LICENSE"
                sha256 = Get-FileSha256 $licensePath
            }
        )
    }
    $manifest | ConvertTo-Json -Depth 4 |
        Set-Content -Encoding utf8NoBOM -LiteralPath $manifestPath

    Write-Output "Synced $sourcePath at $resolvedRevision."
}
finally {
    if (Test-Path -LiteralPath $temporaryRoot) {
        $resolvedTemporaryRoot = [IO.Path]::GetFullPath($temporaryRoot)
        if ($resolvedTemporaryRoot.StartsWith(
            $allowedTempRoot,
            [StringComparison]::OrdinalIgnoreCase
        )) {
            Remove-Item -Recurse -Force -LiteralPath $resolvedTemporaryRoot
        }
    }
}
