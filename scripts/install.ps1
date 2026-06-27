$ErrorActionPreference = "Stop"

$Repo = if ($env:AGEOS_REPO) { $env:AGEOS_REPO } else { "ageos-labs/ageos-runtime" }
$Version = if ($env:AGEOS_VERSION) { $env:AGEOS_VERSION } else { "latest" }

if ($Version -eq "latest") {
    $InstallUrl = "https://github.com/$Repo/releases/latest/download/install.sh"
} else {
    $InstallUrl = "https://github.com/$Repo/releases/download/$Version/install.sh"
}

function ConvertTo-BashSingleQuoted {
    param([string]$Value)
    return "'" + $Value.Replace("'", "'\''") + "'"
}

function New-AgeOSControlCenterShortcut {
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
    $Shortcut.Description = "Open the AgeOS Control Center through WSL"
    $Shortcut.Save()
}

function Install-AgeOSWindowsLaunchers {
    param([bool]$InstallDesktopShortcut)

    $InstallRoot = Join-Path $env:LOCALAPPDATA "AgeOS"
    $Programs = [Environment]::GetFolderPath("Programs")
    $StartMenuDir = Join-Path $Programs "AgeOS"
    New-Item -ItemType Directory -Force -Path $InstallRoot | Out-Null
    New-Item -ItemType Directory -Force -Path $StartMenuDir | Out-Null

    if ($InstallDesktopShortcut) {
        $LauncherScript = Join-Path $InstallRoot "ageos-control-center.ps1"
        @'
$ErrorActionPreference = "Stop"
$Port = if ($env:AGEOS_APP_PORT) { $env:AGEOS_APP_PORT } else { "8010" }
$Command = "ageos app --host 127.0.0.1 --port $Port"
wsl.exe bash -lc $Command
'@ | Set-Content -Path $LauncherScript -Encoding UTF8
    }

    $CmdLauncher = Join-Path $InstallRoot "ageos.cmd"
    @'
@echo off
wsl.exe bash -lc "ageos %*"
'@ | Set-Content -Path $CmdLauncher -Encoding ASCII

    if ($InstallDesktopShortcut) {
        $Shell = New-Object -ComObject WScript.Shell
        $ShortcutPaths = @(
            (Join-Path $StartMenuDir "AgeOS Control Center.lnk"),
            (Join-Path ([Environment]::GetFolderPath("Desktop")) "AgeOS Control Center.lnk")
        )
        foreach ($ShortcutPath in $ShortcutPaths) {
            New-AgeOSControlCenterShortcut `
                -Shell $Shell `
                -ShortcutPath $ShortcutPath `
                -LauncherScript $LauncherScript `
                -WorkingDirectory $InstallRoot
            Write-Host "Created AgeOS Control Center shortcut: $ShortcutPath"
        }
    } else {
        Write-Host "Control Center shortcuts skipped. Run 'ageos app' inside WSL to install it later."
    }

    Write-Host "Windows CLI bridge: $CmdLauncher"
}

function Assert-AgeOSWslReady {
    if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
        throw "AgeOS uses WSL on Windows. Install WSL with: wsl --install -d Ubuntu"
    }

    $Distros = @(wsl.exe --list --quiet 2>$null | Where-Object { $_.Trim() })
    if ($Distros.Count -eq 0) {
        if ($env:AGEOS_INSTALL_WSL -eq "1") {
            Start-Process -FilePath "wsl.exe" -ArgumentList @("--install", "-d", "Ubuntu") -Verb RunAs -Wait
            throw "WSL installation was started. Reboot if prompted, finish Ubuntu setup, then rerun the AgeOS installer."
        }
        throw "No WSL distro is installed. Run 'wsl --install -d Ubuntu', finish Ubuntu setup, then rerun this installer."
    }

    $Status = wsl.exe --status 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Could not read WSL status. Continuing because at least one distro is registered."
    } elseif ($Status -notmatch "Default Version:\s*2") {
        Write-Warning "WSL default version is not WSL2. AgeOS recommends: wsl --set-default-version 2"
    }
}

if ($env:OS -eq "Windows_NT") {
    Assert-AgeOSWslReady

    $QuotedUrl = ConvertTo-BashSingleQuoted $InstallUrl
    $QuotedRepo = ConvertTo-BashSingleQuoted $Repo
    $QuotedVersion = ConvertTo-BashSingleQuoted $Version
    $InstallAppEnv = ""
    if ($env:AGEOS_INSTALL_APP) {
        $QuotedInstallApp = ConvertTo-BashSingleQuoted $env:AGEOS_INSTALL_APP
        $InstallAppEnv = "AGEOS_INSTALL_APP=$QuotedInstallApp "
    }
    $Command = "tmp=`$(mktemp) && curl -fsSL $QuotedUrl -o `$tmp && ${InstallAppEnv}AGEOS_REPO=$QuotedRepo AGEOS_VERSION=$QuotedVersion bash `$tmp"
    wsl.exe bash -lc $Command
    if ($LASTEXITCODE -eq 0) {
        wsl.exe bash -lc "command -v ageos-control-center >/dev/null 2>&1"
        $DesktopInstalled = ($LASTEXITCODE -eq 0)
        Install-AgeOSWindowsLaunchers -InstallDesktopShortcut:$DesktopInstalled
    }
    exit $LASTEXITCODE
}

$TempScript = New-TemporaryFile
try {
    Invoke-WebRequest -Uri $InstallUrl -OutFile $TempScript
    $env:AGEOS_REPO = $Repo
    $env:AGEOS_VERSION = $Version
    bash $TempScript
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}
finally {
    Remove-Item -Force $TempScript -ErrorAction SilentlyContinue
}
