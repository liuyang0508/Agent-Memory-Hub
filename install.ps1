param(
    [switch]$VerifyOnly,
    [switch]$Uninstall,
    [switch]$Minimal,
    [switch]$DryRun
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
$TargetDir = if ($env:AGENT_MEMORY_HUB_HOME) {
    $env:AGENT_MEMORY_HUB_HOME
} else {
    Join-Path $HOME "agent-memory-hub"
}
$BrainDir = if ($env:BRAIN_DIR) {
    $env:BRAIN_DIR
} else {
    Join-Path $HOME ".agent-memory-hub"
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
    if ($VerifyOnly) { $args += "--verify-only" }
    if ($Uninstall) { $args += "--uninstall" }
    if ($Minimal) { $args += "--minimal" }
    if ($DryRun) { $args += "--dry-run" }
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

function Get-CompatiblePython {
    $candidates = @()
    if ($env:AMH_PYTHON) {
        $candidates += $env:AMH_PYTHON
    }
    $candidates += @("python3.13", "python3.12", "python3.11", "python", "python3")
    foreach ($candidate in $candidates) {
        $command = Get-Command $candidate -ErrorAction SilentlyContinue
        if (-not $command) {
            continue
        }
        try {
            $versionText = & $command.Source -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
            $parts = $versionText.Split(".")
            if ([int]$parts[0] -gt 3 -or ([int]$parts[0] -eq 3 -and [int]$parts[1] -ge 11)) {
                return $command
            }
        } catch {
            continue
        }
    }
    return $null
}

$Python = Get-CompatiblePython
$AdapterNames = if ($env:AMH_INSTALL_ADAPTERS) {
    $env:AMH_INSTALL_ADAPTERS -split "\s+"
} else {
    @(
        "codex", "claude_code", "wukong", "cursor", "cline", "continue_dev",
        "hermes_agent", "qoder", "qoder_work", "aider", "github_copilot",
        "aone_copilot", "openhuman", "opensquilla", "openclaw"
    )
}
$CoreAdapterNames = if ($env:AMH_CORE_ADAPTERS) {
    $env:AMH_CORE_ADAPTERS -split "\s+"
} else {
    @("codex", "claude_code")
}

if ($DryRun) {
    Write-Host "Agent Memory Hub local install dry run"
    Write-Host "  code:    $CodeDir"
    Write-Host "  data:    $BrainDir"
    Write-Host "  minimal: $($Minimal.IsPresent)"
    Write-Host "  uninstall: $($Uninstall.IsPresent)"
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
        "agent_runtime_kit/hooks/inject-context.sh",
        "agent_runtime_kit/hooks/inject-discipline.sh",
        "agent_runtime_kit/hooks/session-end-signal.sh",
        "agent_runtime_kit/hooks/lifecycle-event.sh",
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
    Write-Host "  data: $BrainDir (kept)"
    if ($Python) {
        foreach ($adapter in $AdapterNames) {
            & $Python.Source -m agent_brain.interfaces.cli adapter uninstall $adapter 2>$null
        }
    }
    Write-Host "AMH-managed adapter configuration removed where available. User data is kept."
    exit 0
}

if (-not $Python) {
    Write-Error "Python 3.11+ is required. Install Python, then rerun this command."
    exit 1
}

New-Item -ItemType Directory -Force -Path (Join-Path $BrainDir "items") | Out-Null

Write-Host "Installing Agent Memory Hub Python package..."
if ($Minimal) {
    & $Python.Source -m pip install -e $CodeDir
} else {
    & $Python.Source -m pip install -e "$CodeDir[web,embeddings]"
}
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

if (-not $Minimal) {
    Write-Host "Configuring available Agent adapters..."
    $installed = @()
    $installFailed = @()
    foreach ($adapter in $AdapterNames) {
        & $Python.Source -m agent_brain.interfaces.cli adapter install $adapter
        if ($LASTEXITCODE -eq 0) {
            $installed += $adapter
        } else {
            $installFailed += $adapter
            Write-Warning "Optional adapter was not configured: $adapter"
        }
    }

    Write-Host "Verifying configured Agent adapters..."
    $doctorOk = @()
    $doctorFailed = @()
    foreach ($adapter in $installed) {
        & $Python.Source -m agent_brain.interfaces.cli adapter doctor $adapter
        if ($LASTEXITCODE -eq 0) {
            $doctorOk += $adapter
        } else {
            $doctorFailed += $adapter
            Write-Warning "Adapter doctor did not pass: $adapter"
        }
    }
    $coreFailed = @($CoreAdapterNames | Where-Object { $_ -notin $doctorOk })
    if ($coreFailed.Count -gt 0) {
        Write-Error "Core adapter install verification failed: $($coreFailed -join ', ')"
        exit 1
    }
    if ($doctorFailed.Count -gt 0) {
        Write-Host "optional_adapter_doctor_failures: $($doctorFailed -join ' ')"
    }
    if ($installFailed.Count -gt 0) {
        Write-Host "optional_adapter_not_configured: $($installFailed -join ' ')"
    }
}

Write-Host "Agent Memory Hub installed and core adapters verified."
Write-Host "Run: memory doctor"
Write-Host "Cross-agent resume: memory handoff --help; memory resume --fail-empty"
Write-Host "Anonymous product telemetry is off by default. Enable with AMH_TELEMETRY=1."

$InstallSource = if ($env:AMH_INSTALL_SOURCE) { $env:AMH_INSTALL_SOURCE } else { "powershell" }
& $Python.Source -m agent_brain.platform.product_telemetry install --channel $InstallSource 2>$null
