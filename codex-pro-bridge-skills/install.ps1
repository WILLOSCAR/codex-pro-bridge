[CmdletBinding(DefaultParameterSetName = "Global")]
param(
    [Parameter(ParameterSetName = "Global")]
    [switch]$Global,

    [Parameter(Mandatory = $true, ParameterSetName = "Repo")]
    [string]$Repo
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$packageRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$sourceRoot = Join-Path $packageRoot ".agents\skills"
if (-not (Test-Path -LiteralPath $sourceRoot -PathType Container)) {
    throw "Missing source skills directory: $sourceRoot"
}

$repoRoot = $null
if ($PSCmdlet.ParameterSetName -eq "Repo") {
    if (-not (Test-Path -LiteralPath $Repo -PathType Container)) {
        throw "Repository directory does not exist: $Repo"
    }
    $repoRoot = (Resolve-Path -LiteralPath $Repo).Path
    $destinationRoot = Join-Path $repoRoot ".agents\skills"
}
else {
    $codexHome = if ($env:CODEX_HOME) {
        $env:CODEX_HOME
    }
    else {
        Join-Path ([Environment]::GetFolderPath("UserProfile")) ".codex"
    }
    $destinationRoot = Join-Path $codexHome "skills"
}

$managedNames = @(
    ".shared",
    "bundle-algorithm-context",
    "coordinate-auto-research",
    "experiment-plan-generator",
    "gpt-pro-algorithm-pipeline",
    "gpt-pro-paper-brainstormer",
    "gpt-pro-project-workspace",
    "gpt-pro-question-window",
    "gpt-pro-research-algorithm-reviewer",
    "gpt-pro-review-probe",
    "implementation-consistency-checker"
)

New-Item -ItemType Directory -Force -Path $destinationRoot | Out-Null
$stageRoot = Join-Path $destinationRoot (".codex-pro-bridge-install.{0}" -f [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $stageRoot | Out-Null

try {
    $installed = [System.Collections.Generic.List[string]]::new()
    foreach ($name in $managedNames) {
        $source = Join-Path $sourceRoot $name
        if (-not (Test-Path -LiteralPath $source)) {
            continue
        }
        $staged = Join-Path $stageRoot $name
        Copy-Item -LiteralPath $source -Destination $staged -Recurse
        Get-ChildItem -LiteralPath $staged -Recurse -Force -Directory |
            Where-Object { $_.Name -eq "__pycache__" } |
            Remove-Item -Recurse -Force
        Get-ChildItem -LiteralPath $staged -Recurse -Force -File |
            Where-Object { $_.Extension -in ".pyc", ".pyo" } |
            Remove-Item -Force
        $installed.Add($name)
    }

    foreach ($name in $installed) {
        if ($name -notin $managedNames) {
            throw "Refusing unexpected managed entry: $name"
        }
        $destination = Join-Path $destinationRoot $name
        if (Test-Path -LiteralPath $destination) {
            Remove-Item -LiteralPath $destination -Recurse -Force
        }
        Move-Item -LiteralPath (Join-Path $stageRoot $name) -Destination $destination
    }

    Write-Output ("Installed {0}" -f ($installed -join ", "))
    Write-Output "Destination: $destinationRoot"

    $excludePath = ""
    if ($repoRoot -and (Get-Command git -ErrorAction SilentlyContinue)) {
        $previousErrorActionPreference = $ErrorActionPreference
        $gitOutput = @()
        $gitExitCode = 1
        try {
            $ErrorActionPreference = "SilentlyContinue"
            $gitOutput = @(git -C $repoRoot rev-parse --path-format=absolute --git-path info/exclude 2>$null)
            $gitExitCode = $LASTEXITCODE
        }
        finally {
            $ErrorActionPreference = $previousErrorActionPreference
        }
        if ($gitExitCode -eq 0 -and $gitOutput.Count -gt 0) {
            $excludePath = ([string]$gitOutput[0]).Trim()
        }
    }
    if ($excludePath) {
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $excludePath) | Out-Null
        if (-not (Test-Path -LiteralPath $excludePath)) {
            New-Item -ItemType File -Path $excludePath | Out-Null
        }
        $patterns = @((Get-Content -LiteralPath $excludePath))
        foreach ($pattern in ".agents/", ".codex/") {
            if ($patterns -notcontains $pattern) {
                Add-Content -LiteralPath $excludePath -Value $pattern
                $patterns += $pattern
            }
        }
        Write-Output "Local Git exclude updated for .agents/ and .codex/."
    }
}
finally {
    if (Test-Path -LiteralPath $stageRoot) {
        Remove-Item -LiteralPath $stageRoot -Recurse -Force
    }
}

Write-Output "Restart Codex if the updated skills do not appear in an existing session."
