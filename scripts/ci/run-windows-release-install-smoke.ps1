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

function Get-PythonCommand {
    foreach ($Name in @("python", "python3")) {
        $Command = Get-Command $Name -ErrorAction SilentlyContinue
        if ($Command) {
            return @($Command.Source)
        }
    }
    $Py = Get-Command "py" -ErrorAction SilentlyContinue
    if ($Py) {
        return @($Py.Source, "-3")
    }
    throw "Python is required to serve release smoke assets."
}

function Start-ArtifactServer {
    $Python = Get-PythonCommand
    $Arguments = @()
    if ($Python.Count -gt 1) {
        $Arguments += $Python[1..($Python.Count - 1)]
    }
    $Arguments += @("-m", "http.server", "$Port", "--bind", "0.0.0.0", "--directory", (Resolve-Path $AssetsDir).Path)
    $Process = Start-Process -FilePath $Python[0] -ArgumentList $Arguments -PassThru -WindowStyle Hidden
    $Url = "http://127.0.0.1:$Port/previous/VERSION_TAG"

    for ($i = 0; $i -lt 30; $i++) {
        try {
            Invoke-WebRequest -UseBasicParsing -Uri $Url | Out-Null
            return $Process
        } catch {
            Start-Sleep -Seconds 1
        }
    }

    Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
    throw "Timed out waiting for artifact server at $Url"
}

function Get-WslDistros {
    $Raw = @(wsl.exe --list --quiet 2>$null)
    return @(
        $Raw |
            ForEach-Object { ($_ -replace "`0", "").Trim() } |
            Where-Object { $_ }
    )
}

function New-DisposableWslDistro {
    $BaseDistro = $env:BUBBLEHUB_WINDOWS_BASE_WSL_DISTRO
    if (-not $BaseDistro) {
        $Distros = Get-WslDistros
        if ($Distros.Count -eq 0) {
            throw "No WSL distro is available. Install a clean Ubuntu base distro before running Windows release install smoke tests."
        }
        $BaseDistro = $Distros[0]
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

    $Address = (& wsl.exe -d $Distro bash -lc "ip route show default | awk '{print `$3; exit}'").Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to discover the Windows host address from WSL distro '$Distro'."
    }
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
rm -f /usr/local/bin/bubblehub /usr/local/bin/bubblehub-node /usr/local/bin/bubblehub-control-center /usr/local/bin/llama-server
rm -f /usr/local/bin/bubblehub-sandbox /usr/local/bin/pytest
rm -rf /root/.cache/bubblehub /home/*/.cache/bubblehub
'@
    Invoke-Wsl -Distro $Distro -AsRoot -Command $CleanupCommand

    $Shortcut = Join-Path ([Environment]::GetFolderPath("Desktop")) "BubbleHub Control Center.lnk"
    $StartMenuShortcut = Join-Path ([Environment]::GetFolderPath("Programs")) "BubbleHub/BubbleHub Control Center.lnk"
    Remove-Item -Force $Shortcut, $StartMenuShortcut -ErrorAction SilentlyContinue
}

function Stop-BubbleHubApp {
    param([string]$Distro)

    if ($Distro) {
        wsl.exe -d $Distro bash -lc "pkill -f 'bubblehub app' >/dev/null 2>&1 || true; pkill -f bubblehub-control-center >/dev/null 2>&1 || true" 2>$null | Out-Null
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
    $env:DEBIAN_FRONTEND = "noninteractive"
    $env:TZ = "Etc/UTC"

    if ($Method -eq "ps1") {
        $InstallScriptUrl = "$WindowsBaseUrl/$VersionTag/install.ps1"
        Write-Host "--- PowerShell installer smoke: $VersionTag ---"
        Invoke-Expression (Invoke-RestMethod -Uri $InstallScriptUrl)
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
    $Output = (& wsl.exe -d $Distro bash -lc "bubblehub --version").Trim()
    if ($LASTEXITCODE -ne 0 -or $Output -ne "bubblehub $Version") {
        throw "Expected 'bubblehub $Version' from WSL, got '$Output'."
    }

    Invoke-Wsl -Distro $Distro -Command "command -v bubblehub-control-center >/dev/null"

    $Shortcut = Join-Path ([Environment]::GetFolderPath("Desktop")) "BubbleHub Control Center.lnk"
    if (-not (Test-Path $Shortcut)) {
        throw "Expected BubbleHub Control Center desktop shortcut at $Shortcut."
    }
}

function Assert-DesktopLaunch {
    param(
        [string]$VersionTag,
        [string]$Distro
    )

    $Version = $VersionTag.TrimStart("v")
    $ShortcutPath = Join-Path ([Environment]::GetFolderPath("Desktop")) "BubbleHub Control Center.lnk"
    $Shell = New-Object -ComObject WScript.Shell
    $Shortcut = $Shell.CreateShortcut($ShortcutPath)

    Stop-BubbleHubApp -Distro $Distro
    $Process = Start-Process -FilePath $Shortcut.TargetPath -ArgumentList $Shortcut.Arguments -WorkingDirectory $Shortcut.WorkingDirectory -PassThru
    try {
        for ($i = 0; $i -lt 60; $i++) {
            try {
                $Health = Invoke-RestMethod -Uri "http://127.0.0.1:8010/health"
                if ($Health.service -eq "bubblehub-control-center" -and $Health.version -eq $Version) {
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
