param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("ps1", "exe")]
    [string]$Method,

    [Parameter(Mandatory = $true)]
    [string]$AssetsDir,

    [int]$Port = 8765
)

$ErrorActionPreference = "Stop"

function Read-VersionTag {
    param([string]$Name)

    $Path = Join-Path $AssetsDir "$Name/VERSION_TAG"
    if (-not (Test-Path $Path)) {
        throw "Missing release smoke version file: $Path"
    }
    return (Get-Content -Raw -Path $Path).Trim()
}

function Test-PythonExecutable {
    param([string[]]$Command)

    if (-not $Command -or -not $Command[0]) {
        return $false
    }

    $Arguments = @()
    if ($Command.Count -gt 1) {
        $Arguments += $Command[1..($Command.Count - 1)]
    }
    $Arguments += @("-c", "import sys; print(sys.executable)")

    try {
        $Output = & $Command[0] @Arguments 2>$null
        return [bool]$Output
    } catch {
        return $false
    }
}

function Get-PythonCommand {
    if ($env:BUBBLEHUB_PYTHON) {
        $Command = @($env:BUBBLEHUB_PYTHON)
        if (Test-PythonExecutable $Command) {
            Write-Output $Command -NoEnumerate
            return
        }
        throw "BUBBLEHUB_PYTHON is set to '$($env:BUBBLEHUB_PYTHON)' but it cannot run Python."
    }

    foreach ($Name in @("python", "python3")) {
        $Command = Get-Command $Name -ErrorAction SilentlyContinue
        if ($Command) {
            $Candidate = @($Command.Source)
            if (Test-PythonExecutable $Candidate) {
                Write-Output $Candidate -NoEnumerate
                return
            }
        }
    }

    $ToolRoots = @(
        $env:pythonLocation,
        $env:PYTHON_HOME,
        $env:RUNNER_TOOL_CACHE
    ) | Where-Object { $_ }
    foreach ($Root in $ToolRoots) {
        $Candidates = @(
            Join-Path $Root "python.exe"
            Join-Path $Root "python3.exe"
            Join-Path $Root "x64/python.exe"
            Join-Path $Root "x86/python.exe"
        )
        foreach ($CandidatePath in $Candidates) {
            if (-not (Test-Path $CandidatePath)) {
                continue
            }
            $Candidate = @($CandidatePath)
            if (Test-PythonExecutable $Candidate) {
                Write-Output $Candidate -NoEnumerate
                return
            }
        }
    }

    $Py = Get-Command "py" -ErrorAction SilentlyContinue
    if ($Py) {
        $Candidate = @($Py.Source, "-3")
        if (Test-PythonExecutable $Candidate) {
            Write-Output $Candidate -NoEnumerate
            return
        }
    }

    throw @"
Python is required to serve release smoke assets.
Install Python on the runner PATH (for example with actions/setup-python@v5) or set BUBBLEHUB_PYTHON to a working python.exe.
"@
}

function Start-ArtifactServer {
    $PythonCommand = Get-PythonCommand
    if ($PythonCommand -is [string]) {
        $PythonExecutable = $PythonCommand
        $PythonPrefixArgs = @()
    } else {
        $PythonExecutable = [string]$PythonCommand[0]
        $PythonPrefixArgs = @()
        if ($PythonCommand.Count -gt 1) {
            $PythonPrefixArgs = $PythonCommand[1..($PythonCommand.Count - 1)]
        }
    }

    $Arguments = @($PythonPrefixArgs) + @("-m", "http.server", "$Port", "--bind", "0.0.0.0", "--directory", (Resolve-Path $AssetsDir).Path)
    Write-Host "Starting artifact server with: $PythonExecutable $($Arguments -join ' ')"
    $Process = Start-Process -FilePath $PythonExecutable -ArgumentList $Arguments -PassThru -WindowStyle Hidden
    $Url = "http://127.0.0.1:$Port/previous/VERSION_TAG"

    for ($i = 0; $i -lt 30; $i++) {
        if ($Process.HasExited) {
            throw "Artifact server process exited before becoming ready (exit code $($Process.ExitCode))."
        }
        try {
            Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 2 | Out-Null
            return $Process
        } catch {
            Start-Sleep -Seconds 1
        }
    }

    Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
    throw "Timed out waiting for artifact server at $Url"
}

function Get-WslDistros {
    $Raw = wsl.exe --list --quiet 2>$null
    if (-not $Raw) {
        return @()
    }

    $Text = if ($Raw -is [System.Array]) {
        ($Raw | ForEach-Object { ($_ -replace "`0", "").Trim() }) -join "`n"
    } else {
        ($Raw -replace "`0", "").Trim()
    }

    $Distros = @(
        $Text -split "`r?`n" |
            ForEach-Object { $_.Trim() } |
            Where-Object { $_ }
    )
    Write-Output $Distros -NoEnumerate
}

function Select-WslDistroName {
    param([object]$Distros)

    if ($Distros -is [string]) {
        return $Distros
    }
    if (-not $Distros -or $Distros.Count -eq 0) {
        return $null
    }
    return [string]$Distros[0]
}

function Assert-RunnerPrerequisites {
    if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
        throw "Windows release smoke tests require WSL2. Install it with: wsl --install -d Ubuntu"
    }

    $Distros = Get-WslDistros
    $BaseDistro = if ($env:BUBBLEHUB_WINDOWS_BASE_WSL_DISTRO) {
        $env:BUBBLEHUB_WINDOWS_BASE_WSL_DISTRO
    } else {
        Select-WslDistroName $Distros
    }
    if (-not $BaseDistro) {
        throw "Windows release smoke tests require a clean Ubuntu base distro. Install one with: wsl --install -d Ubuntu"
    }

    wsl.exe -d $BaseDistro bash -lc "true"
    if ($LASTEXITCODE -ne 0) {
        throw "WSL distro '$BaseDistro' is not ready. Finish its first-run setup before running Windows release smoke tests."
    }

    $Status = wsl.exe --status 2>$null
    if ($LASTEXITCODE -eq 0 -and $Status -notmatch "Default Version:\s*2") {
        Write-Warning "WSL default version is not WSL2. BubbleHub recommends: wsl --set-default-version 2"
    }
}

function New-DisposableWslDistro {
    $BaseDistro = $env:BUBBLEHUB_WINDOWS_BASE_WSL_DISTRO
    if (-not $BaseDistro) {
        $Distros = Get-WslDistros
        $BaseDistro = Select-WslDistroName $Distros
        if (-not $BaseDistro) {
            throw "No WSL distro is available. Install a clean Ubuntu base distro before running Windows release install smoke tests."
        }
    }

    $Name = "BubbleHubReleaseSmoke-$Method-$PID"
    $Root = Join-Path $env:TEMP $Name
    $Tar = Join-Path $env:TEMP "$Name.tar"
    New-Item -ItemType Directory -Force -Path $Root | Out-Null

    Write-Host "Creating disposable WSL distro '$Name' from '$BaseDistro'..."
    wsl.exe --export $BaseDistro $Tar
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to export WSL distro '$BaseDistro'."
    }
    wsl.exe --import $Name $Root $Tar --version 2
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to import disposable WSL distro '$Name'."
    }

    Remove-Item -Force $Tar -ErrorAction SilentlyContinue
    return @{ Name = $Name; Root = $Root }
}

function Remove-DisposableWslDistro {
    param(
        [string]$Name,
        [string]$Root
    )

    if ($Name) {
        wsl.exe --terminate $Name 2>$null | Out-Null
        wsl.exe --unregister $Name 2>$null | Out-Null
    }
    if ($Root -and (Test-Path $Root)) {
        Remove-Item -Recurse -Force $Root -ErrorAction SilentlyContinue
    }
}

function Invoke-Wsl {
    param(
        [string]$Distro,
        [string]$Command,
        [switch]$AsRoot
    )

    $Command = $Command -replace "`r`n", "`n" -replace "`r", "`n"
    $Args = @("-d", $Distro)
    if ($AsRoot) {
        $Args += @("-u", "root")
    }
    $Args += @("bash", "-lc", $Command)
    & wsl.exe @Args
    if ($LASTEXITCODE -ne 0) {
        throw "WSL command failed in '$Distro': $Command"
    }
}

function Get-WslHostAddress {
    param([string]$Distro)

    $Route = (& wsl.exe -d $Distro bash -lc "ip route show default").Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to discover the Windows host address from WSL distro '$Distro'."
    }
    $Address = if ($Route -match "default\s+via\s+(\S+)") { $Matches[1] } else { "" }
    if (-not $Address) {
        throw "WSL distro '$Distro' did not report a default gateway address for reaching the Windows host."
    }
    return $Address
}

function Clear-PreviousInstall {
    param([string]$Distro)

    $CleanupCommand = @'
set -e
rm -rf /opt/bubblehub
rm -f /usr/local/bin/bubble /usr/local/bin/bubblehub /usr/local/bin/bubblehub-node /usr/local/bin/bubblehub-control-center /usr/local/bin/llama-server
rm -f /usr/local/bin/bubblehub-sandbox /usr/local/bin/pytest
rm -rf /root/.cache/bubblehub /home/*/.cache/bubblehub
'@
    Invoke-Wsl -Distro $Distro -AsRoot -Command $CleanupCommand

    $Desktop = [Environment]::GetFolderPath("Desktop")
    $Programs = [Environment]::GetFolderPath("Programs")
    $Shortcuts = @(
        (Join-Path $Desktop "BubbleHub.lnk"),
        (Join-Path $Desktop "BubbleHub Control Center.lnk"),
        (Join-Path $Programs "BubbleHub/BubbleHub.lnk"),
        (Join-Path $Programs "BubbleHub/BubbleHub Control Center.lnk")
    )
    Remove-Item -Force $Shortcuts -ErrorAction SilentlyContinue
}

function Stop-BubbleHubApp {
    param([string]$Distro)

    if ($Distro) {
        wsl.exe -d $Distro bash -lc "pkill -f 'bubble app --host' >/dev/null 2>&1 || true; pkill -f bubblehub-control-center >/dev/null 2>&1 || true; pkill -f '/opt/bubblehub/share/bubblehub/app/bubblehub' >/dev/null 2>&1 || true" 2>$null | Out-Null
    }
}

function Invoke-Installer {
    param(
        [string]$VersionTag,
        [string]$Distro,
        [string]$WslBaseUrl
    )

    $Version = $VersionTag.TrimStart("v")
    $WindowsBaseUrl = "http://127.0.0.1:$Port"

    $env:BUBBLEHUB_VERSION = $VersionTag
    $env:BUBBLEHUB_RELEASE_BASE_URL = $WslBaseUrl
    $env:BUBBLEHUB_WSL_DISTRO = $Distro
    $env:BUBBLEHUB_INSTALL_APP = "1"
    $env:BUBBLEHUB_SKIP_MODEL_SETUP = "1"
    $env:BUBBLEHUB_INSTALLER_SILENT = "1"
    $env:DEBIAN_FRONTEND = "noninteractive"
    $env:TZ = "Etc/UTC"

    if ($Method -eq "ps1") {
        $InstallScriptUrl = "$WindowsBaseUrl/$VersionTag/install.ps1"
        Write-Host "--- PowerShell installer smoke: $VersionTag ---"
        Invoke-Expression "irm '$InstallScriptUrl' | iex"
        return
    }

    $ExePath = Join-Path $env:TEMP "BubbleHub-$Version-x64.exe"
    $ExeUrl = "$WindowsBaseUrl/$VersionTag/BubbleHub-$Version-x64.exe"
    Write-Host "--- EXE installer smoke: $VersionTag ---"
    Invoke-WebRequest -UseBasicParsing -Uri $ExeUrl -OutFile $ExePath
    $Process = Start-Process -FilePath $ExePath -ArgumentList "/S" -Wait -PassThru
    if ($Process.ExitCode -ne 0) {
        throw "BubbleHub EXE installer failed with exit code $($Process.ExitCode)."
    }
}

function Assert-InstalledVersion {
    param(
        [string]$VersionTag,
        [string]$Distro
    )

    $Version = $VersionTag.TrimStart("v")
    $Output = (& wsl.exe -d $Distro bash -lc "bubble --version").Trim()
    if ($LASTEXITCODE -ne 0 -or $Output -ne "bubble $Version") {
        throw "Expected 'bubble $Version' from WSL, got '$Output'."
    }

    Invoke-Wsl -Distro $Distro -Command "command -v bubble >/dev/null"
    Invoke-Wsl -Distro $Distro -Command "bubble --help >/dev/null"
    Invoke-Wsl -Distro $Distro -Command "bubble app --help >/dev/null"
    Invoke-Wsl -Distro $Distro -Command "bubble specialties list | grep -q default-instruct"

    $Shortcut = Join-Path ([Environment]::GetFolderPath("Desktop")) "BubbleHub.lnk"
    if (-not (Test-Path $Shortcut)) {
        throw "Expected BubbleHub desktop shortcut at $Shortcut."
    }
    $StartMenuShortcut = Join-Path ([Environment]::GetFolderPath("Programs")) "BubbleHub/BubbleHub.lnk"
    if (-not (Test-Path $StartMenuShortcut)) {
        throw "Expected BubbleHub Start Menu shortcut at $StartMenuShortcut."
    }
    $WindowsApp = Join-Path $env:LOCALAPPDATA "BubbleHub/BubbleHub.exe"
    if (-not (Test-Path $WindowsApp)) {
        throw "Expected BubbleHub Windows Control Center app at $WindowsApp."
    }
}

function Assert-DesktopLaunch {
    param(
        [string]$VersionTag,
        [string]$Distro
    )

    $Version = $VersionTag.TrimStart("v")
    $ShortcutPath = Join-Path ([Environment]::GetFolderPath("Desktop")) "BubbleHub.lnk"
    $Shell = New-Object -ComObject WScript.Shell
    $Shortcut = $Shell.CreateShortcut($ShortcutPath)

    Stop-BubbleHubApp -Distro $Distro
    $Process = Start-Process -FilePath $Shortcut.TargetPath -ArgumentList $Shortcut.Arguments -WorkingDirectory $Shortcut.WorkingDirectory -PassThru
    try {
        for ($i = 0; $i -lt 60; $i++) {
            try {
                $Health = Invoke-RestMethod -Uri "http://127.0.0.1:8010/health"
                if ($Health.service -eq "bubblehub" -and $Health.version -eq $Version) {
                    return
                }
            } catch {
                Start-Sleep -Seconds 1
            }
        }
        throw "Timed out waiting for BubbleHub desktop launch health response for $VersionTag."
    } finally {
        Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
        Stop-BubbleHubApp -Distro $Distro
    }
}

function Install-And-Verify {
    param(
        [string]$VersionTag,
        [string]$Distro,
        [string]$WslBaseUrl
    )

    Invoke-Installer -VersionTag $VersionTag -Distro $Distro -WslBaseUrl $WslBaseUrl
    Assert-InstalledVersion -VersionTag $VersionTag -Distro $Distro
    Assert-DesktopLaunch -VersionTag $VersionTag -Distro $Distro
}

$PreviousTag = Read-VersionTag "previous"
$CurrentTag = Read-VersionTag "current"
$Server = $null
$Distro = $null

try {
    Assert-RunnerPrerequisites
    $Server = Start-ArtifactServer
    $Distro = New-DisposableWslDistro
    Clear-PreviousInstall -Distro $Distro.Name
    $HostAddress = Get-WslHostAddress -Distro $Distro.Name
    $WslBaseUrl = "http://$HostAddress`:$Port"

    Install-And-Verify -VersionTag $PreviousTag -Distro $Distro.Name -WslBaseUrl $WslBaseUrl
    Install-And-Verify -VersionTag $CurrentTag -Distro $Distro.Name -WslBaseUrl $WslBaseUrl
} finally {
    if ($Distro) {
        Stop-BubbleHubApp -Distro $Distro.Name
        Remove-DisposableWslDistro -Name $Distro.Name -Root $Distro.Root
    }
    if ($Server) {
        Stop-Process -Id $Server.Id -Force -ErrorAction SilentlyContinue
    }
}
