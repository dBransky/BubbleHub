$ErrorActionPreference = "Stop"

$Repo = if ($env:BUBBLEHUB_REPO) { $env:BUBBLEHUB_REPO } else { "bublhub/bubblehub" }
$Version = if ($env:BUBBLEHUB_VERSION) { $env:BUBBLEHUB_VERSION } else { "latest" }
$ReleaseBaseUrl = if ($env:BUBBLEHUB_RELEASE_BASE_URL) { $env:BUBBLEHUB_RELEASE_BASE_URL.TrimEnd("/") } else { "" }
$WslDistro = if ($env:BUBBLEHUB_WSL_DISTRO) { $env:BUBBLEHUB_WSL_DISTRO } else { "" }

if ($env:BUBBLEHUB_INSTALL_SH_URL) {
    $InstallUrl = $env:BUBBLEHUB_INSTALL_SH_URL
} elseif ($ReleaseBaseUrl) {
    $InstallUrl = "$ReleaseBaseUrl/$Version/install.sh"
} elseif ($Version -eq "latest") {
    $InstallUrl = "https://github.com/$Repo/releases/latest/download/install.sh"
} else {
    $InstallUrl = "https://github.com/$Repo/releases/download/$Version/install.sh"
}

function ConvertTo-BashSingleQuoted {
    param([string]$Value)
    return "'" + $Value.Replace("'", "'\''") + "'"
}

function ConvertTo-PowerShellSingleQuoted {
    param([string]$Value)
    return "'" + $Value.Replace("'", "''") + "'"
}

function Invoke-WslBash {
    param([string]$Command)

    if ($WslDistro) {
        & wsl.exe -d $WslDistro bash -lc $Command
    } else {
        & wsl.exe bash -lc $Command
    }
}

function New-BubbleHubShortcut {
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
    $Shortcut.Description = "Open the BubbleHub desktop app through WSL"
    $Shortcut.Save()
}

function Install-WindowsLaunchers {
    param(
        [bool]$InstallDesktopShortcut,
        [string]$WslDistroName = ""
    )

    $InstallRoot = Join-Path $env:LOCALAPPDATA "BubbleHub"
    $Programs = [Environment]::GetFolderPath("Programs")
    $StartMenuDir = Join-Path $Programs "BubbleHub"
    New-Item -ItemType Directory -Force -Path $InstallRoot | Out-Null
    New-Item -ItemType Directory -Force -Path $StartMenuDir | Out-Null

    if ($InstallDesktopShortcut) {
        $LauncherScript = Join-Path $InstallRoot "bubblehub.ps1"
        $QuotedWslDistro = ConvertTo-PowerShellSingleQuoted $WslDistroName
        @"
`$ErrorActionPreference = "Stop"
`$Port = if (`$env:BUBBLEHUB_APP_PORT) { `$env:BUBBLEHUB_APP_PORT } else { "8010" }
`$Command = "BUBBLEHUB_WINDOWS_APP=1 bubblehub --host 127.0.0.1 --port `$Port"
`$WslDistro = $QuotedWslDistro
if (`$WslDistro) {
    & wsl.exe -d `$WslDistro bash -lc `$Command
} else {
    & wsl.exe bash -lc `$Command
}
"@ | Set-Content -Path $LauncherScript -Encoding UTF8
    }

    $CmdLauncher = Join-Path $InstallRoot "bubble.cmd"
    if ($WslDistroName) {
        $EscapedDistro = $WslDistroName.Replace('"', '\"')
        @"
@echo off
wsl.exe -d "$EscapedDistro" bash -lc "bubble %*"
"@ | Set-Content -Path $CmdLauncher -Encoding ASCII
    } else {
        @'
@echo off
wsl.exe bash -lc "bubble %*"
'@ | Set-Content -Path $CmdLauncher -Encoding ASCII
    }

    if ($InstallDesktopShortcut) {
        $Shell = New-Object -ComObject WScript.Shell
        $ShortcutPaths = @(
            (Join-Path $StartMenuDir "BubbleHub.lnk"),
            (Join-Path ([Environment]::GetFolderPath("Desktop")) "BubbleHub.lnk")
        )
        foreach ($ShortcutPath in $ShortcutPaths) {
            New-BubbleHubShortcut `
                -Shell $Shell `
                -ShortcutPath $ShortcutPath `
                -LauncherScript $LauncherScript `
                -WorkingDirectory $InstallRoot
            Write-Host "Created BubbleHub shortcut: $ShortcutPath"
        }
    } else {
        Write-Host "BubbleHub shortcuts skipped. Run 'bubblehub' inside WSL to install it later."
    }

    Write-Host "Windows CLI bridge: $CmdLauncher"
}

function Assert-WslReady {
    if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
        throw "BubbleHub uses WSL on Windows. Install WSL with: wsl --install -d Ubuntu"
    }

    if ($WslDistro) {
        wsl.exe -d $WslDistro true
        if ($LASTEXITCODE -ne 0) {
            throw "WSL distro '$WslDistro' is not available. Install or import it before running the BubbleHub installer."
        }
        return
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
    $ReleaseBaseEnv = ""
    if ($ReleaseBaseUrl) {
        $QuotedReleaseBase = ConvertTo-BashSingleQuoted $ReleaseBaseUrl
        $ReleaseBaseEnv = "BUBBLEHUB_RELEASE_BASE_URL=$QuotedReleaseBase "
    }
    $AssetNameEnv = ""
    if ($env:BUBBLEHUB_ASSET_NAME) {
        $QuotedAssetName = ConvertTo-BashSingleQuoted $env:BUBBLEHUB_ASSET_NAME
        $AssetNameEnv = "BUBBLEHUB_ASSET_NAME=$QuotedAssetName "
    }
    $InstallAppEnv = ""
    if ($env:BUBBLEHUB_INSTALL_APP) {
        $QuotedInstallApp = ConvertTo-BashSingleQuoted $env:BUBBLEHUB_INSTALL_APP
        $InstallAppEnv = "BUBBLEHUB_INSTALL_APP=$QuotedInstallApp "
    }
    $AptEnv = ""
    if ($env:DEBIAN_FRONTEND) {
        $QuotedDebianFrontend = ConvertTo-BashSingleQuoted $env:DEBIAN_FRONTEND
        $AptEnv += "DEBIAN_FRONTEND=$QuotedDebianFrontend "
    }
    if ($env:TZ) {
        $QuotedTimezone = ConvertTo-BashSingleQuoted $env:TZ
        $AptEnv += "TZ=$QuotedTimezone "
    }
    $SkipModelSetupEnv = "BUBBLEHUB_SKIP_MODEL_SETUP=1 "
    $Command = "tmp=`$(mktemp) && curl -fsSL $QuotedUrl -o `$tmp && ${AptEnv}${SkipModelSetupEnv}${InstallAppEnv}${ReleaseBaseEnv}${AssetNameEnv}BUBBLEHUB_REPO=$QuotedRepo BUBBLEHUB_VERSION=$QuotedVersion bash `$tmp"
    Invoke-WslBash $Command
    if ($LASTEXITCODE -eq 0) {
        Invoke-WslBash "test -x /opt/bubblehub/share/bubblehub/app/bubblehub"
        $DesktopInstalled = ($LASTEXITCODE -eq 0)
        Install-WindowsLaunchers -InstallDesktopShortcut:$DesktopInstalled -WslDistroName $WslDistro
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
