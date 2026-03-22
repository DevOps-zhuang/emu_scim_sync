param(
    [ValidateSet('svg', 'png', 'both')]
    [string]$Format = 'svg'
)

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$diagramDir = Join-Path $root 'docs/media/entra-id-app-registration-guide'

$diagramNames = @(
    'saml-and-sync-app-overview',
    'overall-implementation-sequence',
    'enterprise-app-gallery-creation-flow',
    'saml-scim-identity-linking',
    'graph-app-registration-overview',
    'sso-and-scim-validation-sequence'
)

function Render-Diagram {
    param(
        [string]$InputFile,
        [string]$OutputFile
    )

    npx -y @mermaid-js/mermaid-cli -i $InputFile -o $OutputFile
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to render $InputFile"
    }
}

foreach ($name in $diagramNames) {
    $inputFile = Join-Path $diagramDir ($name + '.mmd')

    if (-not (Test-Path $inputFile)) {
        throw "Missing Mermaid source: $inputFile"
    }

    if ($Format -in @('svg', 'both')) {
        $svgFile = Join-Path $diagramDir ($name + '.svg')
        Render-Diagram -InputFile $inputFile -OutputFile $svgFile
    }

    if ($Format -in @('png', 'both')) {
        $pngFile = Join-Path $diagramDir ($name + '.png')
        Render-Diagram -InputFile $inputFile -OutputFile $pngFile
    }
}

Write-Output "Rendered diagrams in format: $Format"