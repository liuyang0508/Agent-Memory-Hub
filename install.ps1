<#
Agent Memory Hub installer for Windows.

Examples:
  .\install.ps1
  .\install.ps1 -VerifyOnly
  .\install.ps1 -Uninstall
  .\install.ps1 -Uninstall -PurgeData
  .\install.ps1 -Uninstall -PurgeData -RemoveCheckout

Uninstall backs up and removes AMH-managed integrations. User memories under
.agent-memory-hub are preserved unless -PurgeData is explicitly supplied.
#>
param(
    [switch]$VerifyOnly,
    [switch]$Uninstall,
    [switch]$Minimal,
    [switch]$DryRun,
    [switch]$PurgeData,
    [switch]$KeepPackage,
    [switch]$RemoveCheckout
)

$ErrorActionPreference = "Stop"

$ReleaseRepo = "__AMH_GITHUB_REPOSITORY__"
$ReleaseRef = "__AMH_GITHUB_REF_NAME__"

if ($env:AMH_REPO_SLUG) {
    $ReleaseRepo = $env:AMH_REPO_SLUG
}
if ($env:AMH_RELEASE_REF) {
    $ReleaseRef = $env:AMH_RELEASE_REF
}
if ($ReleaseRepo -eq "__AMH_GITHUB_REPOSITORY__") {
    $ReleaseRepo = "liuyang0508/agent-memory-hub"
}
if ($ReleaseRef -eq "__AMH_GITHUB_REF_NAME__") {
    $ReleaseRef = "main"
}

if ($env:AMH_REPO_URL) {
    $RepoUrl = $env:AMH_REPO_URL
} elseif ($ReleaseRepo -eq "liuyang0508/agent-memory-hub") {
    $RepoUrl = "https://github.com/liuyang0508/agent-memory-hub.git"
} else {
    $RepoUrl = "https://github.com/$ReleaseRepo.git"
}
$Ref = if ($env:AMH_REF) {
    $env:AMH_REF
} elseif ($env:AMH_BRANCH) {
    $env:AMH_BRANCH
} else {
    $ReleaseRef
}
$UserHome = if ($env:AMH_USER_HOME) {
    $env:AMH_USER_HOME
} else {
    $HOME
}
$TargetDir = if ($env:AGENT_MEMORY_HUB_HOME) {
    $env:AGENT_MEMORY_HUB_HOME
} else {
    Join-Path $UserHome "agent-memory-hub"
}
$BrainDir = if ($env:BRAIN_DIR) {
    $env:BRAIN_DIR
} else {
    Join-Path $UserHome ".agent-memory-hub"
}

function Test-InSourceCheckout {
    param([string]$Dir)
    return (Test-Path (Join-Path $Dir "pyproject.toml") -PathType Leaf) -and
        (Test-Path (Join-Path $Dir "agent_brain") -PathType Container)
}

function Get-CurrentScriptDir {
    if ($PSScriptRoot) {
        return $PSScriptRoot
    }
    return (Get-Location).Path
}

function Get-ForwardArgs {
    $args = @()
    if ($VerifyOnly) { $args += "-VerifyOnly" }
    if ($Uninstall) { $args += "-Uninstall" }
    if ($Minimal) { $args += "-Minimal" }
    if ($DryRun) { $args += "-DryRun" }
    if ($PurgeData) { $args += "-PurgeData" }
    if ($KeepPackage) { $args += "-KeepPackage" }
    if ($RemoveCheckout) { $args += "-RemoveCheckout" }
    return $args
}

$ScriptDir = Get-CurrentScriptDir

if (-not (Test-InSourceCheckout $ScriptDir)) {
    if ($DryRun) {
        Write-Host "Agent Memory Hub remote install dry run"
        Write-Host "  repo:   $RepoUrl"
        Write-Host "  ref:    $Ref"
        Write-Host "  target: $TargetDir"
        exit 0
    }

    if ($VerifyOnly) {
        Write-Host "Agent Memory Hub remote install verification"
        Write-Host "  repo:   $RepoUrl"
        Write-Host "  ref:    $Ref"
        Write-Host "  target: $TargetDir"
        if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
            Write-Error "missing: git"
            exit 1
        }
        if ((Test-Path $TargetDir) -and -not (Test-Path (Join-Path $TargetDir ".git"))) {
            Write-Error "target exists but is not a git checkout: $TargetDir"
            exit 1
        }
        Write-Host "git=ok"
        Write-Host "installer_self_check=ok"
        exit 0
    }

    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        Write-Error "git is required. Install git first, then rerun this command."
        exit 1
    }

    if (Test-Path (Join-Path $TargetDir ".git")) {
        Write-Host "Updating existing Agent Memory Hub checkout: $TargetDir"
        git -C $TargetDir fetch --depth=1 origin $Ref
        git -C $TargetDir checkout --detach FETCH_HEAD
    } elseif (Test-Path $TargetDir) {
        Write-Error "Target exists but is not a git checkout: $TargetDir"
        exit 1
    } else {
        Write-Host "Cloning Agent Memory Hub into $TargetDir"
        git clone --depth=1 --branch $Ref $RepoUrl $TargetDir
    }

    $LocalInstaller = Join-Path $TargetDir "install.ps1"
    if (-not (Test-Path $LocalInstaller)) {
        Write-Error "install.ps1 was not found in cloned checkout: $LocalInstaller"
        exit 1
    }
    & $LocalInstaller @(Get-ForwardArgs)
    exit $LASTEXITCODE
}

$CodeDir = $ScriptDir
$AppVenv = Join-Path $CodeDir ".venv"
$AppPython = if ($env:AGENT_MEMORY_HUB_APP_PYTHON) {
    $env:AGENT_MEMORY_HUB_APP_PYTHON
} else {
    Join-Path (Join-Path $AppVenv "Scripts") "python.exe"
}
$UserBin = if ($env:AGENT_MEMORY_HUB_BIN) {
    $env:AGENT_MEMORY_HUB_BIN
} else {
    Join-Path (Join-Path $UserHome ".local") "bin"
}
$MemoryShim = Join-Path $UserBin "memory.cmd"
$PathMarker = Join-Path $BrainDir ".windows-user-path-added"
$Python = $null

function Test-PythonBootstrap {
    param([AllowNull()][string]$Candidate)

    if (-not $Candidate -or -not (Test-Path -LiteralPath $Candidate -PathType Leaf)) {
        return $false
    }
    $previousErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    try {
        $probeOutput = & $Candidate -c `
            "import sys, pip; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" 2>$null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    } finally {
        $ErrorActionPreference = $previousErrorAction
    }
}

function Test-PythonRuntime {
    param([AllowNull()][string]$Candidate)

    if (-not (Test-PythonBootstrap -Candidate $Candidate)) {
        return $false
    }
    $previousErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    try {
        $probeOutput = & $Candidate -c `
            "import agent_brain.interfaces.cli, typer, yaml" 2>$null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    } finally {
        $ErrorActionPreference = $previousErrorAction
    }
}

function Get-PythonBootstrapCandidates {
    $candidates = New-Object System.Collections.Generic.List[string]
    foreach ($candidate in @(
        $env:AGENT_MEMORY_HUB_PYTHON,
        $AppPython
    )) {
        if ($candidate) {
            $candidates.Add($candidate)
        }
    }
    foreach ($name in @("python", "python3")) {
        foreach ($command in @(Get-Command $name -All -ErrorAction SilentlyContinue)) {
            if ($command.Source) {
                $candidates.Add($command.Source)
            }
        }
    }
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($launcher) {
        $launcherOutput = & $launcher.Source -0p 2>$null
        foreach ($line in @($launcherOutput)) {
            if ($line -match "([A-Za-z]:\\.*python(?:3(?:\.\d+)?)?\.exe)\s*$") {
                $candidates.Add($Matches[1].Trim())
            }
        }
    }
    $codexRuntimePython = Join-Path `
        $UserHome `
        ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
    $candidates.Add($codexRuntimePython)

    $seen = New-Object System.Collections.Generic.HashSet[string] `
        ([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($candidate in $candidates) {
        if (-not $candidate) {
            continue
        }
        try {
            $fullPath = [System.IO.Path]::GetFullPath($candidate)
        } catch {
            continue
        }
        if ($seen.Add($fullPath)) {
            Write-Output $fullPath
        }
    }
}

function Find-PythonBootstrap {
    foreach ($candidate in (Get-PythonBootstrapCandidates)) {
        if (Test-PythonBootstrap -Candidate $candidate) {
            return $candidate
        }
    }
    return $null
}

function Write-AtomicUtf8Text {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Content
    )

    $tempPath = "$Path.tmp"
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($tempPath, $Content, $encoding)
    Move-Item -LiteralPath $tempPath -Destination $Path -Force
}

function Install-MemoryShim {
    New-Item -ItemType Directory -Force -Path $UserBin | Out-Null
    if (Test-Path -LiteralPath $MemoryShim -PathType Leaf) {
        $existing = [System.IO.File]::ReadAllText($MemoryShim)
        if ($existing -notmatch "agent_brain\.interfaces\.cli") {
            throw "Refusing to overwrite non-AMH command shim: $MemoryShim"
        }
    }
    $shim = "@echo off`r`n`"$AppPython`" -m agent_brain.interfaces.cli %*`r`n"
    Write-AtomicUtf8Text -Path $MemoryShim -Content $shim

    $processEntries = @($env:Path -split ";" | Where-Object { $_ })
    if ($UserBin -notin $processEntries) {
        $env:Path = "$UserBin;$env:Path"
    }

    # Test/custom-home installs must not mutate the real Windows user PATH.
    if ($env:AMH_SKIP_PATH_UPDATE -or $env:AMH_USER_HOME) {
        return
    }
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $entries = @($userPath -split ";" | Where-Object { $_ })
    $alreadyPresent = $false
    foreach ($entry in $entries) {
        try {
            if ([System.IO.Path]::GetFullPath($entry).TrimEnd("\") -ieq `
                [System.IO.Path]::GetFullPath($UserBin).TrimEnd("\")) {
                $alreadyPresent = $true
                break
            }
        } catch {
            continue
        }
    }
    if (-not $alreadyPresent) {
        $updatedPath = if ($userPath) { "$userPath;$UserBin" } else { $UserBin }
        [Environment]::SetEnvironmentVariable("Path", $updatedPath, "User")
        New-Item -ItemType Directory -Force -Path $BrainDir | Out-Null
        Write-AtomicUtf8Text -Path $PathMarker -Content "$UserBin`n"
    }
}

function Remove-MemoryShim {
    if (-not (Test-Path -LiteralPath $MemoryShim -PathType Leaf)) {
        return $false
    }
    $content = [System.IO.File]::ReadAllText($MemoryShim)
    if ($content -notmatch "agent_brain\.interfaces\.cli") {
        Write-Warning "Preserved non-AMH command shim: $MemoryShim"
        return $false
    }
    Remove-Item -LiteralPath $MemoryShim -Force
    return $true
}

function Remove-AmhUserPathEntry {
    if (-not (Test-Path -LiteralPath $PathMarker -PathType Leaf)) {
        return $false
    }
    $markedPath = [System.IO.File]::ReadAllText($PathMarker).Trim()
    if (-not $markedPath -or $markedPath -ine $UserBin) {
        Write-Warning "Preserved unmatched AMH PATH marker: $PathMarker"
        return $false
    }
    if (-not $env:AMH_SKIP_PATH_UPDATE -and -not $env:AMH_USER_HOME) {
        $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
        $kept = @(
            $userPath -split ";" |
                Where-Object { $_ -and $_ -ine $markedPath }
        )
        [Environment]::SetEnvironmentVariable("Path", ($kept -join ";"), "User")
    }
    Remove-Item -LiteralPath $PathMarker -Force
    return $true
}

function Remove-AmhAppVenv {
    if (-not (Test-Path -LiteralPath $AppVenv -PathType Container)) {
        return $false
    }
    $resolved = [System.IO.Path]::GetFullPath($AppVenv).TrimEnd("\")
    $resolvedCode = [System.IO.Path]::GetFullPath($CodeDir).TrimEnd("\")
    if ([System.IO.Path]::GetDirectoryName($resolved) -ine $resolvedCode -or
        [System.IO.Path]::GetFileName($resolved) -ne ".venv") {
        throw "Refusing unsafe venv deletion target: $resolved"
    }
    Remove-Item -LiteralPath $resolved -Recurse -Force
    return $true
}

function Backup-ManagedFile {
    param([Parameter(Mandatory = $true)][string]$Path)

    $stamp = Get-Date -Format "yyyyMMddHHmmssfff"
    $backupPath = "$Path.bak.uninstall.$stamp"
    Copy-Item -LiteralPath $Path -Destination $backupPath -Force
    Write-Host "    backup: $backupPath"
}

function Set-ManagedTextFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Original,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Updated
    )

    if ($Updated -ceq $Original) {
        return $false
    }
    Backup-ManagedFile -Path $Path
    if ([string]::IsNullOrWhiteSpace($Updated)) {
        Remove-Item -LiteralPath $Path -Force
    } else {
        Write-AtomicUtf8Text -Path $Path -Content $Updated
    }
    return $true
}

function Remove-SentinelBlock {
    param(
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Content,
        [Parameter(Mandatory = $true)][string]$Begin,
        [Parameter(Mandatory = $true)][string]$End
    )

    $updated = $Content
    while ($true) {
        $start = $updated.IndexOf($Begin, [System.StringComparison]::Ordinal)
        if ($start -lt 0) {
            break
        }
        $endStart = $updated.IndexOf($End, $start, [System.StringComparison]::Ordinal)
        if ($endStart -lt 0) {
            $finish = $updated.Length
        } else {
            $finish = $endStart + $End.Length
        }
        $prefix = $updated.Substring(0, $start).TrimEnd([char[]]"`r`n")
        $suffix = $updated.Substring($finish).TrimStart([char[]]"`r`n")
        if ($prefix -and $suffix) {
            $updated = "$prefix`r`n`r`n$suffix"
        } elseif ($prefix) {
            $updated = "$prefix`r`n"
        } else {
            $updated = $suffix
        }
    }
    return $updated
}

function Test-AmhHookCommand {
    param([AllowNull()][string]$Command)

    if (-not $Command) {
        return $false
    }
    $normalized = $Command.Replace("\", "/")
    foreach ($scriptName in @(
        "inject-discipline.sh",
        "inject-context.sh",
        "session-end-signal.sh",
        "lifecycle-event.sh"
    )) {
        if (
            $normalized.Contains("/agent_runtime_kit/hooks/$scriptName") -or
            $normalized.Contains("/brain/hooks/$scriptName")
        ) {
            return $true
        }
    }
    return $false
}

function Get-CodexEventKeyLabel {
    param([Parameter(Mandatory = $true)][string]$Event)

    $labels = @{
        "PreToolUse" = "pre_tool_use"
        "PermissionRequest" = "permission_request"
        "PostToolUse" = "post_tool_use"
        "PreCompact" = "pre_compact"
        "PostCompact" = "post_compact"
        "SessionStart" = "session_start"
        "UserPromptSubmit" = "user_prompt_submit"
        "SubagentStart" = "subagent_start"
        "SubagentStop" = "subagent_stop"
        "Stop" = "stop"
    }
    return $labels[$Event]
}

function Remove-AmhHooksFromJsonConfig {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [switch]$TrackCodexTrust,
        [switch]$RemoveMcpServer
    )

    $trustKeys = @()
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return [pscustomobject]@{
            Changed = $false
            RemovedHooks = 0
            RemovedMcp = 0
            TrustKeys = @()
        }
    }

    $original = [System.IO.File]::ReadAllText($Path)
    try {
        $config = $original | ConvertFrom-Json
    } catch {
        throw "Refusing to overwrite malformed JSON: $Path ($($_.Exception.Message))"
    }

    $changed = $false
    $removedHooks = 0
    $hooksObject = $config.PSObject.Properties["hooks"].Value
    if ($null -ne $hooksObject) {
        foreach ($eventProperty in @($hooksObject.PSObject.Properties)) {
            $entries = @($eventProperty.Value)
            $keptEntries = @()
            for ($groupIndex = 0; $groupIndex -lt $entries.Count; $groupIndex++) {
                $entry = $entries[$groupIndex]
                if ($null -eq $entry -or $null -eq $entry.PSObject.Properties["hooks"]) {
                    $keptEntries += $entry
                    continue
                }
                $handlers = @($entry.hooks)
                $keptHandlers = @()
                $removedFromEntry = 0
                for ($handlerIndex = 0; $handlerIndex -lt $handlers.Count; $handlerIndex++) {
                    $hook = $handlers[$handlerIndex]
                    $command = ""
                    if ($null -ne $hook -and $null -ne $hook.PSObject.Properties["command"]) {
                        $command = [string]$hook.command
                    }
                    if (Test-AmhHookCommand -Command $command) {
                        $removedHooks++
                        $removedFromEntry++
                        if ($TrackCodexTrust) {
                            $label = Get-CodexEventKeyLabel -Event $eventProperty.Name
                            if ($label) {
                                $fullHooksPath = [System.IO.Path]::GetFullPath($Path)
                                $trustKeys += "${fullHooksPath}:${label}:${groupIndex}:${handlerIndex}"
                            }
                        }
                    } else {
                        $keptHandlers += $hook
                    }
                }
                if ($removedFromEntry -eq 0) {
                    $keptEntries += $entry
                } elseif ($keptHandlers.Count -gt 0) {
                    $entry.hooks = @($keptHandlers)
                    $keptEntries += $entry
                    $changed = $true
                } else {
                    $changed = $true
                }
            }
            if ($changed -or $keptEntries.Count -ne $entries.Count) {
                $hooksObject.($eventProperty.Name) = @($keptEntries)
            }
        }
    }

    $removedMcp = 0
    if ($RemoveMcpServer -and $null -ne $config.PSObject.Properties["mcpServers"]) {
        $mcpServers = $config.mcpServers
        if ($null -ne $mcpServers.PSObject.Properties["agent-memory-hub"]) {
            $mcpServers.PSObject.Properties.Remove("agent-memory-hub")
            $removedMcp = 1
            $changed = $true
        }
    }

    if ($changed) {
        $updated = ($config | ConvertTo-Json -Depth 100) + "`r`n"
        Set-ManagedTextFile -Path $Path -Original $original -Updated $updated | Out-Null
    }
    return [pscustomobject]@{
        Changed = $changed
        RemovedHooks = $removedHooks
        RemovedMcp = $removedMcp
        TrustKeys = @($trustKeys)
    }
}

function Remove-TomlSectionByHeader {
    param(
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Content,
        [Parameter(Mandatory = $true)][string]$Header
    )

    $updated = $Content
    $headerPattern = New-Object System.Text.RegularExpressions.Regex(
        "(?m)^[ `t]*$([regex]::Escape($Header))[ `t]*`r?$"
    )
    $tablePattern = New-Object System.Text.RegularExpressions.Regex("(?m)^[ `t]*\[")
    while ($true) {
        $match = $headerPattern.Match($updated)
        if (-not $match.Success) {
            break
        }
        $next = $tablePattern.Match($updated, $match.Index + $match.Length)
        $finish = $updated.Length
        if ($next.Success) {
            $finish = $next.Index
        }
        $prefix = $updated.Substring(0, $match.Index).TrimEnd([char[]]"`r`n")
        $suffix = $updated.Substring($finish).TrimStart([char[]]"`r`n")
        if ($prefix -and $suffix) {
            $updated = "$prefix`r`n`r`n$suffix"
        } elseif ($prefix) {
            $updated = "$prefix`r`n"
        } else {
            $updated = $suffix
        }
    }
    return $updated
}

function Remove-AmhCodexToml {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [string[]]$TrustKeys = @()
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $false
    }
    $original = [System.IO.File]::ReadAllText($Path)
    $updated = Remove-TomlSectionByHeader -Content $original -Header "[mcp_servers.agent-memory-hub]"
    foreach ($key in $TrustKeys) {
        $escapedKey = $key.Replace("\", "\\").Replace('"', '\"')
        foreach ($header in @(
            "[hooks.state.'$key']",
            "[hooks.state.`"$key`"]",
            "[hooks.state.`"$escapedKey`"]"
        )) {
            $updated = Remove-TomlSectionByHeader -Content $updated -Header $header
        }
    }
    return Set-ManagedTextFile -Path $Path -Original $original -Updated $updated
}

function Remove-AmhRememberCommand {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $false
    }
    $content = [System.IO.File]::ReadAllText($Path)
    if ($content -notmatch "agent-memory-hub|write-memory\.sh|memory harvest") {
        Write-Warning "Preserved non-AMH /remember command: $Path"
        return $false
    }
    Backup-ManagedFile -Path $Path
    Remove-Item -LiteralPath $Path -Force
    return $true
}

function Remove-AmhDataDirectory {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$HomePath,
        [Parameter(Mandatory = $true)][string]$CodePath
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        return $false
    }
    $resolved = [System.IO.Path]::GetFullPath($Path).TrimEnd("\", "/")
    $resolvedHome = [System.IO.Path]::GetFullPath($HomePath).TrimEnd("\", "/")
    $resolvedCode = [System.IO.Path]::GetFullPath($CodePath).TrimEnd("\", "/")
    $root = [System.IO.Path]::GetPathRoot($resolved).TrimEnd("\", "/")
    if (
        -not $resolved -or
        $resolved -eq $root -or
        $resolved -eq $resolvedHome -or
        $resolved -eq $resolvedCode
    ) {
        throw "Refusing unsafe recursive deletion target: $resolved"
    }
    Remove-Item -LiteralPath $resolved -Recurse -Force
    return $true
}

function Remove-AmhCheckoutDirectory {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$HomePath,
        [Parameter(Mandatory = $true)][string]$BrainPath
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        return $false
    }
    $resolved = [System.IO.Path]::GetFullPath($Path).TrimEnd("\", "/")
    $resolvedHome = [System.IO.Path]::GetFullPath($HomePath).TrimEnd("\", "/")
    $resolvedBrain = [System.IO.Path]::GetFullPath($BrainPath).TrimEnd("\", "/")
    $root = [System.IO.Path]::GetPathRoot($resolved).TrimEnd("\", "/")
    if (
        -not $resolved -or
        $resolved -eq $root -or
        $resolved -eq $resolvedHome -or
        $resolved -eq $resolvedBrain -or
        -not (Test-InSourceCheckout $resolved)
    ) {
        throw "Refusing unsafe or non-AMH checkout deletion target: $resolved"
    }
    Set-Location -LiteralPath $resolvedHome
    Remove-Item -LiteralPath $resolved -Recurse -Force
    return $true
}

function Get-AmhAdapterNames {
    if ($env:AMH_INSTALL_ADAPTERS) {
        return @(
            $env:AMH_INSTALL_ADAPTERS -split "\s+" |
                Where-Object { $_ }
        )
    }
    return @(
        "codex", "claude_code", "wukong", "cursor", "cline", "continue_dev",
        "hermes_agent", "qoder", "qoder_work", "aider", "github_copilot",
        "aone_copilot", "openhuman", "opensquilla", "openclaw"
    )
}

function Invoke-AmhAdapterAction {
    param(
        [Parameter(Mandatory = $true)][ValidateSet("install", "uninstall", "doctor")][string]$Action,
        [Parameter(Mandatory = $true)][string]$Adapter
    )

    $memoryCommand = Get-Command memory -ErrorAction SilentlyContinue
    if (-not $memoryCommand -and -not $Python) {
        return [pscustomobject]@{
            Success = $false
            Available = $false
            Output = "Neither memory CLI nor Python is available"
        }
    }

    $previousErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        if ($Action -eq "install" -and $Python) {
            # Use the interpreter that just installed the editable package so a
            # stale `memory` command elsewhere on PATH cannot restore config
            # from an older checkout.
            $commandOutput = & $Python.Source -m agent_brain.interfaces.cli adapter $Action $Adapter 2>&1
        } elseif ($memoryCommand) {
            $commandOutput = & $memoryCommand.Source adapter $Action $Adapter 2>&1
        } else {
            $commandOutput = & $Python.Source -m agent_brain.interfaces.cli adapter $Action $Adapter 2>&1
        }
        $exitCode = $LASTEXITCODE
        return [pscustomobject]@{
            Success = $exitCode -eq 0
            Available = $true
            Output = (($commandOutput | Out-String).Trim())
        }
    } catch {
        return [pscustomobject]@{
            Success = $false
            Available = $true
            Output = $_.Exception.Message
        }
    } finally {
        $ErrorActionPreference = $previousErrorAction
    }
}

$BootstrapPythonPath = Find-PythonBootstrap
if (Test-PythonRuntime -Candidate $AppPython) {
    $Python = Get-Command $AppPython -ErrorAction SilentlyContinue
} elseif ($BootstrapPythonPath) {
    $Python = Get-Command $BootstrapPythonPath -ErrorAction SilentlyContinue
}

if ($DryRun) {
    Write-Host "Agent Memory Hub local install dry run"
    Write-Host "  code:    $CodeDir"
    Write-Host "  data:    $BrainDir"
    Write-Host "  minimal: $($Minimal.IsPresent)"
    Write-Host "  uninstall: $($Uninstall.IsPresent)"
    Write-Host "  purge data: $($PurgeData.IsPresent)"
    Write-Host "  remove checkout: $($RemoveCheckout.IsPresent)"
    Write-Host "  app venv: $AppVenv"
    Write-Host "  bootstrap python: $BootstrapPythonPath"
    exit 0
}

if ($VerifyOnly) {
    Write-Host "Agent Memory Hub local install verification"
    Write-Host "  code:    $CodeDir"
    Write-Host "  data:    $BrainDir"
    $missing = @()
    foreach ($path in @(
        "pyproject.toml",
        "agent_brain",
        "agent_runtime_kit/templates/remember.md.template",
        "agent_runtime_kit/mcp/server.sh"
    )) {
        if (Test-Path (Join-Path $CodeDir $path)) {
            Write-Host "ok: $path"
        } else {
            $missing += $path
        }
    }
    if (-not $Python) {
        $missing += "python"
    } else {
        $versionText = & $Python.Source -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
        $parts = $versionText.Split(".")
        if ([int]$parts[0] -lt 3 -or ([int]$parts[0] -eq 3 -and [int]$parts[1] -lt 11)) {
            $missing += "python>=3.11"
        } else {
            Write-Host "python=$versionText"
        }
    }
    if ($missing.Count -gt 0) {
        foreach ($item in $missing) {
            Write-Error "missing: $item"
        }
        Write-Host "installer_self_check=failed"
        exit 1
    }
    Write-Host "installer_self_check=ok"
    exit 0
}

if ($Uninstall) {
    Write-Host "Agent Memory Hub uninstall"
    Write-Host "  code: $CodeDir"
    if ($PurgeData) {
        Write-Host "  data: $BrainDir (purge requested)"
    } else {
        Write-Host "  data: $BrainDir (kept)"
    }
    Write-Host ""

    $failures = @()
    $codexDir = Join-Path $UserHome ".codex"
    $codexAgents = Join-Path $codexDir "AGENTS.md"
    $codexHooks = Join-Path $codexDir "hooks.json"
    $codexConfig = Join-Path $codexDir "config.toml"

    try {
        if (Test-Path -LiteralPath $codexAgents -PathType Leaf) {
            $originalAgents = [System.IO.File]::ReadAllText($codexAgents)
            $updatedAgents = Remove-SentinelBlock `
                -Content $originalAgents `
                -Begin "<!-- BEGIN agent-memory-hub -->" `
                -End "<!-- END agent-memory-hub -->"
            if (Set-ManagedTextFile -Path $codexAgents -Original $originalAgents -Updated $updatedAgents) {
                Write-Host "  OK Codex AGENTS.md managed block removed"
            }
        }

        $codexHookResult = Remove-AmhHooksFromJsonConfig `
            -Path $codexHooks `
            -TrackCodexTrust
        if ($codexHookResult.Changed) {
            Write-Host "  OK Codex hooks removed: $($codexHookResult.RemovedHooks)"
        }
        if (Remove-AmhCodexToml -Path $codexConfig -TrustKeys $codexHookResult.TrustKeys) {
            Write-Host "  OK Codex MCP/trust configuration removed"
        }
    } catch {
        $failures += "Codex cleanup: $($_.Exception.Message)"
    }

    try {
        $claudeDir = Join-Path $UserHome ".claude"
        $claudeSettings = Join-Path $claudeDir "settings.json"
        $claudeResult = Remove-AmhHooksFromJsonConfig `
            -Path $claudeSettings `
            -RemoveMcpServer
        if ($claudeResult.Changed) {
            Write-Host "  OK Claude hooks/MCP removed"
        }
        $rememberCommand = Join-Path (Join-Path $claudeDir "commands") "remember.md"
        if (Remove-AmhRememberCommand -Path $rememberCommand) {
            Write-Host "  OK /remember removed"
        }
    } catch {
        $failures += "Claude cleanup: $($_.Exception.Message)"
    }

    if ((Get-Command memory -ErrorAction SilentlyContinue) -or
        (Test-PythonRuntime -Candidate $AppPython)) {
        Write-Host ""
        Write-Host "  Removing other configured AMH adapters..."
        foreach ($adapter in (Get-AmhAdapterNames)) {
            if ($adapter -in @("codex", "claude_code")) {
                continue
            }
            $adapterResult = Invoke-AmhAdapterAction -Action "uninstall" -Adapter $adapter
            if (-not $adapterResult.Success) {
                Write-Warning "Adapter uninstall did not complete: $adapter"
            }
        }
    }

    if (-not $KeepPackage) {
        if (Remove-MemoryShim) {
            Write-Host "  OK memory command shim removed"
        }
        if (Remove-AmhUserPathEntry) {
            Write-Host "  OK AMH user PATH entry removed"
        }
        if (Test-Path -LiteralPath $AppVenv -PathType Container) {
            try {
                if (Remove-AmhAppVenv) {
                    Write-Host "  OK isolated AMH Python environment removed"
                }
            } catch {
                $failures += "Python environment removal: $($_.Exception.Message)"
            }
        } elseif ($BootstrapPythonPath) {
            # Compatibility cleanup for Windows installs made before the
            # project-local venv was introduced.
            $previousErrorAction = $ErrorActionPreference
            $ErrorActionPreference = "SilentlyContinue"
            & $BootstrapPythonPath -m pip show agent-memory-hub 2>$null | Out-Null
            $packageInstalled = $LASTEXITCODE -eq 0
            $ErrorActionPreference = $previousErrorAction
            if ($packageInstalled) {
                Write-Host ""
                Write-Host "  Removing legacy Agent Memory Hub Python package..."
                $ErrorActionPreference = "Continue"
                & $BootstrapPythonPath -m pip uninstall -y agent-memory-hub
                $uninstallExitCode = $LASTEXITCODE
                $ErrorActionPreference = $previousErrorAction
                if ($uninstallExitCode -ne 0) {
                    $failures += "Python package uninstall failed"
                }
            }
        } else {
            Write-Warning "No pip-capable Python was found; no legacy Python package could be removed."
        }
    }

    if ($PurgeData) {
        try {
            if (Remove-AmhDataDirectory -Path $BrainDir -HomePath $UserHome -CodePath $CodeDir) {
                Write-Host "  OK AMH user data removed: $BrainDir"
            }
        } catch {
            $failures += "Data purge: $($_.Exception.Message)"
        }
    }

    if ($RemoveCheckout -and $failures.Count -eq 0) {
        try {
            if (Remove-AmhCheckoutDirectory -Path $TargetDir -HomePath $UserHome -BrainPath $BrainDir) {
                Write-Host "  OK AMH checkout removed: $TargetDir"
            }
        } catch {
            $failures += "Checkout removal: $($_.Exception.Message)"
        }
    }

    Write-Host ""
    if ($failures.Count -gt 0) {
        Write-Host "Uninstall completed with errors:"
        foreach ($failure in $failures) {
            Write-Host "  - $failure"
        }
        exit 1
    }
    Write-Host "Uninstall complete."
    if (-not $PurgeData) {
        Write-Host "User data kept: $BrainDir"
        Write-Host "To remove it too, rerun with -Uninstall -PurgeData."
    }
    if (-not $RemoveCheckout) {
        Write-Host "Source checkout kept: $CodeDir"
        Write-Host "To remove the installed checkout too, rerun with -Uninstall -RemoveCheckout."
    }
    exit 0
}

if (-not $BootstrapPythonPath) {
    Write-Error (
        "A pip-capable Python 3.11+ is required. Install Python with pip, " +
        "or set AGENT_MEMORY_HUB_PYTHON to a compatible python.exe."
    )
    exit 1
}

New-Item -ItemType Directory -Force -Path (Join-Path $BrainDir "items") | Out-Null

if ((Test-Path -LiteralPath $AppVenv -PathType Container) -and
    -not (Test-PythonBootstrap -Candidate $AppPython)) {
    $stamp = Get-Date -Format "yyyyMMddHHmmssfff"
    $venvBackup = "$AppVenv.bak.repair.$stamp"
    Write-Warning "Existing AMH venv is incomplete; preserving it as $venvBackup"
    Move-Item -LiteralPath $AppVenv -Destination $venvBackup
}
if (-not (Test-Path -LiteralPath $AppPython -PathType Leaf)) {
    Write-Host "Creating isolated Agent Memory Hub Python environment..."
    & $BootstrapPythonPath -m venv $AppVenv
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $AppPython -PathType Leaf)) {
        Write-Error "Failed to create AMH venv with $BootstrapPythonPath"
        exit 1
    }
}
$Python = Get-Command $AppPython -ErrorAction SilentlyContinue
if (-not $Python) {
    Write-Error "AMH venv Python was not found after creation: $AppPython"
    exit 1
}

Write-Host "Installing Agent Memory Hub Python package..."
if ($Minimal) {
    & $Python.Source -m pip install -e $CodeDir
} else {
    & $Python.Source -m pip install -e "$CodeDir[web,embeddings]"
}
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
if (-not (Test-PythonRuntime -Candidate $AppPython)) {
    Write-Error "AMH Python environment is incomplete after installation: $AppPython"
    exit 1
}
try {
    Install-MemoryShim
    Write-Host "memory command shim: $MemoryShim"
} catch {
    Write-Error $_.Exception.Message
    exit 1
}

if (-not $Minimal) {
    Write-Host ""
    Write-Host "Configuring Agent Memory Hub adapters..."
    $adapterInstallFailures = @()
    $configuredAdapters = @()
    foreach ($adapter in (Get-AmhAdapterNames)) {
        $adapterResult = Invoke-AmhAdapterAction -Action "install" -Adapter $adapter
        if ($adapterResult.Success) {
            $configuredAdapters += $adapter
            Write-Host "  OK $adapter"
            if ($adapterResult.Output -match "incompatible config") {
                Write-Host "    $($adapterResult.Output)"
            }
        } else {
            $adapterInstallFailures += $adapter
            Write-Warning "Adapter was not configured: $adapter"
            if ($adapterResult.Output) {
                Write-Host "    $($adapterResult.Output)"
            }
        }
    }

    $requiredFailures = @()
    if ((Test-Path -LiteralPath (Join-Path $UserHome ".codex")) -and "codex" -in $adapterInstallFailures) {
        $requiredFailures += "codex"
    }
    if ((Test-Path -LiteralPath (Join-Path $UserHome ".claude")) -and "claude_code" -in $adapterInstallFailures) {
        $requiredFailures += "claude_code"
    }
    if ($requiredFailures.Count -gt 0) {
        Write-Error "Required local adapters failed to reinstall: $($requiredFailures -join ', ')"
        exit 1
    }
    if ($adapterInstallFailures.Count -gt 0) {
        Write-Host "optional_adapter_not_configured: $($adapterInstallFailures -join ' ')"
    }
}

$pendingDir = Join-Path $BrainDir "pending"
$pendingRecords = if (Test-Path -LiteralPath $pendingDir -PathType Container) {
    @(Get-ChildItem -LiteralPath $pendingDir -Filter "*.jsonl" -File -ErrorAction SilentlyContinue)
} else {
    @()
}
if ($pendingRecords.Count -gt 0) {
    Write-Host ""
    Write-Host "Replaying safe pending memories..."
    $previousErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $syncOutput = & $Python.Source -m agent_brain.interfaces.cli `
        sync-pending --apply --safe-only --summary-only 2>&1
    $syncExitCode = $LASTEXITCODE
    $ErrorActionPreference = $previousErrorAction
    if ($syncOutput) {
        Write-Host (($syncOutput | Out-String).Trim())
    }
    if ($syncExitCode -ne 0) {
        Write-Warning (
            "Some pending memories require review or could not be replayed. " +
            "Run: memory sync-pending --summary-only"
        )
    }
}

Write-Host "Agent Memory Hub installed."
Write-Host "Run: memory doctor"
