$ErrorActionPreference = "Stop"

$Repo = if ($env:BUBBLEHUB_REPO) { $env:BUBBLEHUB_REPO } else { "bublhub/bubblehub" }
$Version = if ($env:BUBBLEHUB_VERSION) { $env:BUBBLEHUB_VERSION } else { "latest" }

if ($Version -eq "latest") {
    $InstallUrl = "https://github.com/$Repo/releases/latest/download/install.sh"
} else {
    $InstallUrl = "https://github.com/$Repo/releases/download/$Version/install.sh"
}

function ConvertTo-BashSingleQuoted {
    param([string]$Value)
    return "'" + $Value.Replace("'", "'\''") + "'"
}

function New-ControlCenterShortcut {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Shell,
        [Parameter(Mandatory = $true)]
        [string]$ShortcutPath,
        [Parameter(Mandatory = $true)]
        [string]$LauncherScript,
        [Parameter(Mandatory = $true)]
        [string]$WorkingDirectory
    )

    $Shortcut = $Shell.CreateShortcut($ShortcutPath)
    $Shortcut.TargetPath = "powershell.exe"
    $Shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$LauncherScript`""
    $Shortcut.WorkingDirectory = $WorkingDirectory
    $Shortcut.Description = "Open the BubbleHub Control Center through WSL"
    $Shortcut.Save()
}

function Install-WindowsLaunchers {
    param([bool]$InstallDesktopShortcut)

    $InstallRoot = Join-Path $env:LOCALAPPDATA "BubbleHub"
    $Programs = [Environment]::GetFolderPath("Programs")
    $StartMenuDir = Join-Path $Programs "BubbleHub"
    New-Item -ItemType Directory -Force -Path $InstallRoot | Out-Null
    New-Item -ItemType Directory -Force -Path $StartMenuDir | Out-Null

    if ($InstallDesktopShortcut) {
        $LauncherScript = Join-Path $InstallRoot "bubblehub-control-center.ps1"
        @'
$ErrorActionPreference = "Stop"
$Port = if ($env:BUBBLEHUB_APP_PORT) { $env:BUBBLEHUB_APP_PORT } else { "8010" }
$Command = "BUBBLEHUB_WINDOWS_APP=1 bubblehub app --host 127.0.0.1 --port $Port"
wsl.exe bash -lc $Command
'@ | Set-Content -Path $LauncherScript -Encoding UTF8
    }

    $CmdLauncher = Join-Path $InstallRoot "bubblehub.cmd"
    @'
@echo off
wsl.exe bash -lc "bubblehub %*"
'@ | Set-Content -Path $CmdLauncher -Encoding ASCII

    if ($InstallDesktopShortcut) {
        $Shell = New-Object -ComObject WScript.Shell
        $ShortcutPaths = @(
            (Join-Path $StartMenuDir "BubbleHub Control Center.lnk"),
            (Join-Path ([Environment]::GetFolderPath("Desktop")) "BubbleHub Control Center.lnk")
        )
        foreach ($ShortcutPath in $ShortcutPaths) {
            New-ControlCenterShortcut `
                -Shell $Shell `
                -ShortcutPath $ShortcutPath `
                -LauncherScript $LauncherScript `
                -WorkingDirectory $InstallRoot
            Write-Host "Created BubbleHub Control Center shortcut: $ShortcutPath"
        }
    } else {
        Write-Host "Control Center shortcuts skipped. Run 'bubblehub app' inside WSL to install it later."
    }

    Write-Host "Windows CLI bridge: $CmdLauncher"
}

function Assert-WslReady {
    if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
        throw "BubbleHub uses WSL on Windows. Install WSL with: wsl --install -d Ubuntu"
    }

    $Distros = @(wsl.exe --list --quiet 2>$null | Where-Object { $_.Trim() })
    if ($Distros.Count -eq 0) {
        if ($env:BUBBLEHUB_INSTALL_WSL -eq "1") {
            Start-Process -FilePath "wsl.exe" -ArgumentList @("--install", "-d", "Ubuntu") -Verb RunAs -Wait
            throw "WSL installation was started. Reboot if prompted, finish Ubuntu setup, then rerun the BubbleHub installer."
        }
        throw "No WSL distro is installed. Run 'wsl --install -d Ubuntu', finish Ubuntu setup, then rerun this installer."
    }

    $Status = wsl.exe --status 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Could not read WSL status. Continuing because at least one distro is registered."
    } elseif ($Status -notmatch "Default Version:\s*2") {
        Write-Warning "WSL default version is not WSL2. BubbleHub recommends: wsl --set-default-version 2"
    }
}

if ($env:OS -eq "Windows_NT") {
    Assert-WslReady

    $QuotedUrl = ConvertTo-BashSingleQuoted $InstallUrl
    $QuotedRepo = ConvertTo-BashSingleQuoted $Repo
    $QuotedVersion = ConvertTo-BashSingleQuoted $Version
    $InstallAppEnv = ""
    if ($env:BUBBLEHUB_INSTALL_APP) {
        $QuotedInstallApp = ConvertTo-BashSingleQuoted $env:BUBBLEHUB_INSTALL_APP
        $InstallAppEnv = "BUBBLEHUB_INSTALL_APP=$QuotedInstallApp "
    }
    $SkipModelSetupEnv = "BUBBLEHUB_SKIP_MODEL_SETUP=1 "
    $Command = "tmp=`$(mktemp) && curl -fsSL $QuotedUrl -o `$tmp && ${SkipModelSetupEnv}${InstallAppEnv}BUBBLEHUB_REPO=$QuotedRepo BUBBLEHUB_VERSION=$QuotedVersion bash `$tmp"
    wsl.exe bash -lc $Command
    if ($LASTEXITCODE -eq 0) {
        wsl.exe bash -lc "command -v bubblehub-control-center >/dev/null 2>&1"
        $DesktopInstalled = ($LASTEXITCODE -eq 0)
        Install-WindowsLaunchers -InstallDesktopShortcut:$DesktopInstalled
    }
    exit $LASTEXITCODE
}

$TempScript = New-TemporaryFile
try {
    Invoke-WebRequest -Uri $InstallUrl -OutFile $TempScript
    $env:BUBBLEHUB_REPO = $Repo
    $env:BUBBLEHUB_VERSION = $Version
    bash $TempScript
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}
finally {
    Remove-Item -Force $TempScript -ErrorAction SilentlyContinue
}
