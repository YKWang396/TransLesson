# Requires PowerShell 7.4+ for Test-Json's Draft 2020-12 support.
$ErrorActionPreference = 'Stop'
$repoPath = Split-Path -Parent $PSScriptRoot
$schemaPath = Join-Path $repoPath 'contracts/project.schema.json'
$samplePath = Join-Path $repoPath 'examples/project.json'
$sampleText = Get-Content -Raw -LiteralPath $samplePath
if (-not ($sampleText | Test-Json -SchemaFile $schemaPath)) { throw 'Sample does not match schema' }
$project = $sampleText | ConvertFrom-Json -Depth 100

function Assert-Design([bool] $Condition, [string] $Message) {
    if (-not $Condition) { throw $Message }
}

function Test-ProjectInvariant($Data) {
    foreach ($collection in @('speakers', 'glossary', 'formulas', 'segments', 'notes')) {
        $ids = @($Data.$collection | ForEach-Object { $_.id })
        Assert-Design (@($ids | Select-Object -Unique).Count -eq $ids.Count) "Duplicate IDs in $collection"
    }
    $speakerIds = @($Data.speakers | ForEach-Object { $_.id })
    $formulaIds = @($Data.formulas | ForEach-Object { $_.id })
    $termIds = @($Data.glossary | ForEach-Object { $_.id })
    $segmentIds = @($Data.segments | ForEach-Object { $_.id })
    foreach ($segment in $Data.segments) {
        Assert-Design ($speakerIds -contains $segment.speaker_id) "Unknown speaker: $($segment.id)"
        Assert-Design ($segment.start_ms -lt $segment.end_ms -and $segment.end_ms -le $Data.source.duration_ms) "Invalid segment time: $($segment.id)"
        foreach ($id in $segment.formula_ids) { Assert-Design ($formulaIds -contains $id) "Unknown formula: $id" }
        foreach ($id in $segment.term_ids) { Assert-Design ($termIds -contains $id) "Unknown term: $id" }
        $placeholders = @([regex]::Matches($segment.target_text, '\{\{([a-z][a-z0-9_-]*)\}\}') | ForEach-Object { $_.Groups[1].Value })
        $uniquePlaceholders = @($placeholders | Select-Object -Unique)
        Assert-Design ($uniquePlaceholders.Count -eq $segment.formula_ids.Count) "Formula placeholder set: $($segment.id)"
        foreach ($id in $segment.formula_ids) {
            Assert-Design ($uniquePlaceholders -contains $id) "Formula placeholder missing: $id"
        }
    }
    $sortedSegments = @($Data.segments | Sort-Object start_ms)
    for ($i = 1; $i -lt $sortedSegments.Count; $i++) {
        Assert-Design ($sortedSegments[$i].start_ms -ge $sortedSegments[$i - 1].end_ms) 'Overlapping single-speaker segments'
    }
    foreach ($formula in $Data.formulas) {
        if ($formula.review -eq 'confirmed') {
            Assert-Design (-not [string]::IsNullOrWhiteSpace($formula.latex)) "Confirmed formula without LaTeX: $($formula.id)"
        }
    }
    $evidence = @($Data.formulas | ForEach-Object { $_.evidence }) + @($Data.glossary | ForEach-Object { $_.evidence }) + @($Data.speakers | Where-Object { $_.voice_mode -eq 'reference' } | ForEach-Object { $_.reference_evidence })
    foreach ($item in $evidence) {
        Assert-Design ($item.start_ms -le $item.end_ms -and $item.end_ms -le $Data.source.duration_ms) "Invalid evidence time: $($item.id)"
        if ($item.kind -eq 'speech') { Assert-Design ($item.start_ms -lt $item.end_ms) "Empty speech evidence: $($item.id)" }
        if ($item.kind -eq 'frame') {
            $box = $item.bbox_xyxy
            Assert-Design ($box[0] -lt $box[2] -and $box[1] -lt $box[3]) "Invalid bounding box: $($item.id)"
        }
    }
    foreach ($note in $Data.notes) {
        foreach ($id in $note.segment_ids) { Assert-Design ($segmentIds -contains $id) "Unknown note segment: $id" }
        foreach ($id in $note.formula_ids) { Assert-Design ($formulaIds -contains $id) "Unknown note formula: $id" }
        foreach ($match in [regex]::Matches($note.body, '\{\{([a-z][a-z0-9_-]*)\}\}')) {
            Assert-Design ($note.formula_ids -contains $match.Groups[1].Value) "Undeclared formula in note: $($note.id)"
        }
    }
}

Test-ProjectInvariant $project
Write-Output 'PASS: Draft 2020-12 schema and example cross-field invariants'

# These probes verify validation behaviour; they never modify the on-disk sample.
$negativeCases = @(
    { param($p) $p.segments[0].end_ms = 13000 },
    { param($p) $p.segments[0].speaker_id = 'missing' },
    { param($p) $p.segments[0].target_text = '公式被漏掉。' },
    { param($p) $p.formulas[0].latex = $null },
    { param($p) $p.formulas[0].evidence[1].bbox_xyxy = @(0.8, 0.2, 0.3, 0.4) },
    { param($p) $p.notes[0].segment_ids = @('missing') }
)
foreach ($mutate in $negativeCases) {
    $copy = $sampleText | ConvertFrom-Json -Depth 100
    & $mutate $copy
    $rejected = $false
    try { Test-ProjectInvariant $copy } catch { $rejected = $true }
    Assert-Design $rejected 'A negative invariant probe was accepted'
}
$schemaNegativeCases = @(
    { param($p) $p.revision = 'invalid' },
    { param($p) $p.segments[0].tts_text = '' },
    { param($p) $p.speakers[0].voice_mode = 'reference'; $p.speakers[0] | Add-Member -NotePropertyName voice_authorized -NotePropertyValue $true; $p.speakers[0] | Add-Member -NotePropertyName reference_evidence -NotePropertyValue $p.formulas[0].evidence[1] },
    { param($p) $p.formulas[0].evidence[1].frame_path = '../../outside.png' }
)
foreach ($mutate in $schemaNegativeCases) {
    $invalid = $sampleText | ConvertFrom-Json -Depth 100
    & $mutate $invalid
    $schemaRejected = $false
    try { $schemaRejected = -not (($invalid | ConvertTo-Json -Depth 100) | Test-Json -SchemaFile $schemaPath -ErrorAction Stop) } catch { $schemaRejected = $true }
    Assert-Design $schemaRejected 'Schema accepted an invalid data probe'
}
$repeated = $sampleText | ConvertFrom-Json -Depth 100
$repeated.segments[0].target_text = '先看 {{formula-1}}，再考虑 {{formula-1}}。'
Test-ProjectInvariant $repeated
Write-Output 'PASS: 10 negative probes rejected; repeated valid formula reference accepted'

$planSchemaPath = Join-Path $repoPath 'contracts/render-plan.schema.json'
$planText = Get-Content -Raw -LiteralPath (Join-Path $repoPath 'examples/render-plan.json')
if (-not ($planText | Test-Json -SchemaFile $planSchemaPath)) { throw 'Render plan does not match schema' }
function Test-RenderInvariant($Data, $Plan) {
    Assert-Design ($Plan.project_id -eq $Data.project_id -and $Plan.project_revision -eq $Data.revision) 'Snapshot identity/revision mismatch'
    Assert-Design ($Plan.source_sha256 -eq $Data.source.sha256) 'Snapshot source mismatch'
    $segmentsById = @{}
    foreach ($segment in $Data.segments) { $segmentsById[$segment.id] = $segment }
    $artifactsById = @{}
    foreach ($artifact in $Plan.artifacts) {
        Assert-Design (-not $artifactsById.ContainsKey($artifact.id)) 'Duplicate artifact ID'
        $artifactsById[$artifact.id] = $artifact
        Assert-Design ($artifact.input_revision -le $Plan.project_revision) 'Artifact from future revision'
        foreach ($id in $artifact.segment_ids) { Assert-Design ($segmentsById.ContainsKey($id)) 'Artifact references unknown segment' }
    }
    $cueIds = @{}
    $previousEnd = 0
    foreach ($cue in ($Plan.cues | Sort-Object start_ms)) {
        Assert-Design (-not $cueIds.ContainsKey($cue.id)) 'Duplicate cue ID'
        $cueIds[$cue.id] = $true
        Assert-Design ($segmentsById.ContainsKey($cue.segment_id)) 'Cue references unknown segment'
        $segment = $segmentsById[$cue.segment_id]
        Assert-Design ($cue.start_ms -ge $segment.start_ms -and $cue.end_ms -le $segment.end_ms -and $cue.start_ms -lt $cue.end_ms) 'Cue outside its segment'
        Assert-Design ($cue.start_ms -ge $previousEnd) 'Overlapping subtitle cues'
        $previousEnd = $cue.end_ms
        foreach ($id in $cue.formula_ids) { Assert-Design ($segment.formula_ids -contains $id) 'Cue references unrelated formula' }
    }
    foreach ($artifact in $Plan.artifacts) {
        foreach ($dep in $artifact.depends_on) {
            Assert-Design ($artifactsById.ContainsKey($dep.artifact_id)) 'Missing dependency artifact'
            Assert-Design ($artifactsById[$dep.artifact_id].sha256 -eq $dep.sha256) 'Dependency hash mismatch'
        }
    }
    foreach ($id in $Plan.export_artifact_ids) { Assert-Design ($artifactsById.ContainsKey($id)) 'Unknown export artifact' }
    $visited = [System.Collections.Generic.HashSet[string]]::new()
    $active = [System.Collections.Generic.HashSet[string]]::new()
    function Visit-Artifact([string] $Id, $Lookup, $Visited, $Active) {
        Assert-Design (-not $Active.Contains($Id)) 'Artifact dependency cycle'
        if ($Visited.Contains($Id)) { return }
        [void]$Active.Add($Id)
        foreach ($dep in $Lookup[$Id].depends_on) { Visit-Artifact $dep.artifact_id $Lookup $Visited $Active }
        [void]$Active.Remove($Id)
        [void]$Visited.Add($Id)
    }
    foreach ($id in $artifactsById.Keys) { Visit-Artifact $id $artifactsById $visited $active }
}
$plan = $planText | ConvertFrom-Json -Depth 100
Test-RenderInvariant $project $plan
$planNegativeCases = @(
    { param($p) $p.cues[0].end_ms = 7000 },
    { param($p) $p.project_revision = 2 },
    { param($p) $p.artifacts[2].depends_on[0].sha256 = ('9' * 64) }
)
foreach ($mutate in $planNegativeCases) {
    $copy = $planText | ConvertFrom-Json -Depth 100
    & $mutate $copy
    $rejected = $false
    try { Test-RenderInvariant $project $copy } catch { $rejected = $true }
    Assert-Design $rejected 'Invalid render plan accepted'
}
Write-Output 'PASS: render snapshot schema, cues, dependency hashes, acyclic graph; 3 invalid render probes rejected'

# Only validate relative Markdown file links. External URLs and future code paths are not fetched.
$linkCount = 0
$projectDocs = @((Get-Item -LiteralPath (Join-Path $repoPath 'README.md')))
$projectDocs += @(Get-ChildItem -LiteralPath (Join-Path $repoPath 'docs') -Recurse -Filter '*.md' -File)
foreach ($doc in $projectDocs) {
    $body = Get-Content -Raw -LiteralPath $doc.FullName
    foreach ($match in [regex]::Matches($body, '\[[^\]]+\]\(([^)]+)\)')) {
        $target = $match.Groups[1].Value
        if ($target -match '^[a-zA-Z][a-zA-Z0-9+.-]*:' -or $target.StartsWith('#')) { continue }
        $relative = ($target -split '#', 2)[0]
        Assert-Design (Test-Path -LiteralPath (Join-Path $doc.DirectoryName $relative)) "Broken local link in $($doc.Name): $target"
        $linkCount++
    }
}
Write-Output "PASS: $linkCount relative Markdown file links"
Write-Output 'Scope: design-data validation only; no media, models, math correctness or app runtime tested.'
