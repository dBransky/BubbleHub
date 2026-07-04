$ErrorActionPreference = "Stop"

$Repo = if ($env:BUBBLEHUB_REPO) { $env:BUBBLEHUB_REPO } else { "bublhub/bubblehub" }
$Version = if ($env:BUBBLEHUB_VERSION) { $env:BUBBLEHUB_VERSION } else { "latest" }
$ReleaseBaseUrl = if ($env:BUBBLEHUB_RELEASE_BASE_URL) { $env:BUBBLEHUB_RELEASE_BASE_URL.TrimEnd("/") } else { "" }
$WslDistro = if ($env:BUBBLEHUB_WSL_DISTRO) { $env:BUBBLEHUB_WSL_DISTRO } else { "" }
$SilentInstall = $env:BUBBLEHUB_INSTALLER_SILENT -eq "1" -or $env:CI -eq "true" -or $env:GITHUB_ACTIONS -eq "true"

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

function Get-BashEnvAssignment {
    param(
        [string]$Name,
        [string]$Value
    )

    if (-not $Value) {
        return ""
    }
    return "$Name=$(ConvertTo-BashSingleQuoted $Value) "
}

function Invoke-WslBash {
    param(
        [string]$Command,
        [switch]$AsRoot
    )

    $Command = $Command -replace "`r`n", "`n" -replace "`r", "`n"
    $Args = @()
    if ($WslDistro) {
        $Args += @("-d", $WslDistro)
    }
    if ($AsRoot) {
        $Args += @("-u", "root")
    }
    $Args += @("bash", "-lc", $Command)
    & wsl.exe @Args
}

function Invoke-WslBashChecked {
    param(
        [string]$Command,
        [switch]$AsRoot
    )

    Invoke-WslBash -Command $Command -AsRoot:$AsRoot
    if ($LASTEXITCODE -ne 0) {
        if ($WslDistro) {
            throw "WSL command failed in '$WslDistro': $Command"
        } else {
            throw "WSL command failed: $Command"
        }
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
        [string]$WorkingDirectory,
        [string]$IconPath = ""
    )

    $Shortcut = $Shell.CreateShortcut($ShortcutPath)
    $Shortcut.TargetPath = "powershell.exe"
    $Shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$LauncherScript`""
    $Shortcut.WorkingDirectory = $WorkingDirectory
    $Shortcut.Description = "Open the BubbleHub desktop app through WSL"
    if ($IconPath) {
        $Shortcut.IconLocation = "$IconPath,0"
    }
    $Shortcut.Save()
}

function Install-WindowsLaunchers {
    param(
        [bool]$InstallDesktopShortcut,
        [string]$WslDistroName = "",
        [string]$WindowsAppPath = "",
        [string]$ExpectedVersion = ""
    )

    $InstallRoot = Join-Path $env:LOCALAPPDATA "BubbleHub"
    $Programs = [Environment]::GetFolderPath("Programs")
    $StartMenuDir = Join-Path $Programs "BubbleHub"
    New-Item -ItemType Directory -Force -Path $InstallRoot | Out-Null
    New-Item -ItemType Directory -Force -Path $StartMenuDir | Out-Null

    if ($InstallDesktopShortcut) {
        $LauncherScript = Join-Path $InstallRoot "bubblehub-control-center.ps1"
        $ServerScript = Join-Path $InstallRoot "bubblehub-control-center-server.ps1"
        $ServerPidFile = Join-Path $InstallRoot "bubblehub-control-center-server.pid"
        $QuotedWslDistro = ConvertTo-PowerShellSingleQuoted $WslDistroName
        $ControlApiKillCommandTemplate = @'
set +e
port="__APP_PORT__"
case "$port" in
  ''|*[!0-9]*) port=8010 ;;
esac
pid_file="/tmp/bubblehub-control-center-$port.pid"
if [ -f "$pid_file" ]; then
  pid="$(cat "$pid_file" 2>/dev/null || true)"
  case "$pid" in
    ''|*[!0-9]*) ;;
    *)
      kill -TERM "$pid" >/dev/null 2>&1 || true
      sleep 1
      kill -KILL "$pid" >/dev/null 2>&1 || true
      ;;
  esac
  rm -f "$pid_file"
fi
if command -v ss >/dev/null 2>&1; then
  for pid in $(ss -H -ltnp "sport = :$port" 2>/dev/null | sed -n 's/.*pid=\([0-9][0-9]*\).*/\1/p' | sort -u); do
    kill -TERM "$pid" >/dev/null 2>&1 || true
  done
fi
ps -eo pid=,args= 2>/dev/null | awk -v port="$port" '$0 ~ "[a]pp --host 127.0.0.1 --port " port { print $1 }' | while read -r pid; do
  kill -TERM "$pid" >/dev/null 2>&1 || true
done
for inode in $(awk -v port="$port" 'BEGIN { p=sprintf("%04X", port + 0) } $4 == "0A" { split($2, a, ":"); if (toupper(a[2]) == p) print $10 }' /proc/net/tcp /proc/net/tcp6 2>/dev/null | sort -u); do
  for fd in /proc/[0-9]*/fd/*; do
    target="$(readlink "$fd" 2>/dev/null || true)"
    if [ "$target" = "socket:[$inode]" ]; then
      pid="${fd#/proc/}"
      pid="${pid%%/*}"
      kill -TERM "$pid" >/dev/null 2>&1 || true
    fi
  done
done
pkill -TERM -f "[a]pp --host 127.0.0.1 --port $port" >/dev/null 2>&1 || true
sleep 1
ps -eo pid=,args= 2>/dev/null | awk -v port="$port" '$0 ~ "[a]pp --host 127.0.0.1 --port " port { print $1 }' | while read -r pid; do
  kill -KILL "$pid" >/dev/null 2>&1 || true
done
'@
        $QuotedControlApiKillCommandTemplate = ConvertTo-PowerShellSingleQuoted $ControlApiKillCommandTemplate
        @"
`$ErrorActionPreference = "Stop"
`$Port = if (`$env:BUBBLEHUB_APP_PORT) { `$env:BUBBLEHUB_APP_PORT } else { "8010" }
`$WslDistro = $QuotedWslDistro
`$KillCommand = $QuotedControlApiKillCommandTemplate.Replace("__APP_PORT__", [string]`$Port)
try {
    if (`$WslDistro) {
        & wsl.exe -d `$WslDistro -u root bash -lc `$KillCommand 2>`$null | Out-Null
    } else {
        & wsl.exe -u root bash -lc `$KillCommand 2>`$null | Out-Null
    }
} catch {
}
Start-Sleep -Seconds 1
`$Command = 'set -e; rm -f "/tmp/bubblehub-control-center-' + [string]`$Port + '.pid"; echo $$ > "/tmp/bubblehub-control-center-' + [string]`$Port + '.pid"; BUBBLEHUB_WINDOWS_APP=1 exec bubble app --host 127.0.0.1 --port ' + [string]`$Port + ' --server-only'
if (`$WslDistro) {
    & wsl.exe -d `$WslDistro bash -lc `$Command
} else {
    & wsl.exe bash -lc `$Command
}
"@ | Set-Content -Path $ServerScript -Encoding UTF8

        $QuotedServerScript = ConvertTo-PowerShellSingleQuoted $ServerScript
        $QuotedWindowsAppPath = ConvertTo-PowerShellSingleQuoted $WindowsAppPath
        $QuotedExpectedVersion = ConvertTo-PowerShellSingleQuoted $ExpectedVersion
        $QuotedServerPidFile = ConvertTo-PowerShellSingleQuoted $ServerPidFile
        @"
`$ErrorActionPreference = "Stop"
`$Port = if (`$env:BUBBLEHUB_APP_PORT) { `$env:BUBBLEHUB_APP_PORT } else { "8010" }
`$Url = "http://127.0.0.1:`$Port/"
`$ServerScript = $QuotedServerScript
`$WindowsApp = $QuotedWindowsAppPath
`$ExpectedVersion = $QuotedExpectedVersion
`$ServerPidFile = $QuotedServerPidFile
if (Test-Path `$ServerPidFile) {
    `$OldServerPid = (Get-Content -Raw -Path `$ServerPidFile -ErrorAction SilentlyContinue).Trim()
    if (`$OldServerPid -match "^\d+`$") {
        Stop-Process -Id ([int]`$OldServerPid) -Force -ErrorAction SilentlyContinue
    }
    Remove-Item -Force `$ServerPidFile -ErrorAction SilentlyContinue
}
`$ServerProcess = Start-Process -FilePath "powershell.exe" -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", `$ServerScript) -WindowStyle Hidden -PassThru
Set-Content -Path `$ServerPidFile -Value `$ServerProcess.Id -Encoding ASCII
for (`$i = 0; `$i -lt 60; `$i++) {
    try {
        `$Health = Invoke-RestMethod -Uri "`$(`$Url)health" -TimeoutSec 2
        if (-not `$ExpectedVersion -or `$Health.version -eq `$ExpectedVersion) {
            break
        }
    } catch {
    }
    Start-Sleep -Seconds 1
}
if (`$WindowsApp -and (Test-Path `$WindowsApp)) {
    Start-Process -FilePath `$WindowsApp -ArgumentList @(`$Url)
} else {
    throw "BubbleHub Windows Control Center was not installed at `$WindowsApp."
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
                -WorkingDirectory $InstallRoot `
                -IconPath $WindowsAppPath
            Write-Host "Created BubbleHub shortcut: $ShortcutPath"
        }
        Remove-Item -Force `
            (Join-Path $StartMenuDir "BubbleHub Control Center.lnk"), `
            (Join-Path ([Environment]::GetFolderPath("Desktop")) "BubbleHub Control Center.lnk") `
            -ErrorAction SilentlyContinue
    } else {
        Write-Host "BubbleHub shortcuts skipped. Run 'bubble' inside WSL to start it later."
    }

    Write-Host "Windows CLI bridge: $CmdLauncher"
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

function Select-WslDistro {
    param([object]$Distros)

    if ($Distros -is [string]) {
        return $Distros
    }
    if (-not $Distros -or $Distros.Count -eq 0) {
        return ""
    }
    $Ubuntu = @($Distros | Where-Object { $_ -match "^Ubuntu" } | Select-Object -First 1)
    if ($Ubuntu.Count -gt 0) {
        return [string]$Ubuntu[0]
    }
    return [string]$Distros[0]
}

function Start-WslInstall {
    if ($SilentInstall) {
        throw "BubbleHub uses WSL on Windows. Install Ubuntu WSL first with: wsl --install -d Ubuntu"
    }

    Write-Host "BubbleHub uses WSL on Windows. Starting Ubuntu WSL installation..."
    Start-Process -FilePath "wsl.exe" -ArgumentList @("--install", "-d", "Ubuntu") -Verb RunAs -Wait
    throw "WSL installation was started. Reboot if prompted, finish Ubuntu setup, then rerun the BubbleHub installer."
}

function Assert-WslReady {
    if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
        Start-WslInstall
    }

    if ($WslDistro) {
        wsl.exe -d $WslDistro bash -lc "true"
        if ($LASTEXITCODE -ne 0) {
            throw "WSL distro '$WslDistro' is not available. Install or import it before running the BubbleHub installer."
        }
        return
    }

    $Distros = Get-WslDistros
    $SelectedDistro = Select-WslDistro $Distros
    if (-not $SelectedDistro) {
        Start-WslInstall
    }

    $script:WslDistro = $SelectedDistro
    wsl.exe -d $script:WslDistro bash -lc "true"
    if ($LASTEXITCODE -ne 0) {
        throw "WSL distro '$script:WslDistro' is not ready. Finish its first-run setup, then rerun the BubbleHub installer."
    }

    $Status = (wsl.exe --status 2>$null) -join "`n"
    $Status = $Status -replace "`0", ""
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Could not read WSL status. Continuing because at least one distro is registered."
    } elseif ($Status -notmatch "Default Version:\s*2") {
        Write-Warning "WSL default version is not WSL2. BubbleHub recommends: wsl --set-default-version 2"
    }
}

function Resolve-VersionTag {
    if ($Version -ne "latest") {
        return $Version
    }
    if ($ReleaseBaseUrl) {
        throw "BUBBLEHUB_VERSION=latest cannot be used with BUBBLEHUB_RELEASE_BASE_URL. Set BUBBLEHUB_VERSION to an explicit release tag."
    }

    $Latest = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/releases/latest"
    if (-not $Latest.tag_name) {
        throw "Could not resolve the latest BubbleHub release tag for $Repo."
    }
    return [string]$Latest.tag_name
}

function Resolve-DebUrl {
    param([string]$VersionTag)

    if ($env:BUBBLEHUB_DEB_URL) {
        return $env:BUBBLEHUB_DEB_URL
    }

    $PackageVersion = $VersionTag.TrimStart("v")
    $DebName = "BubbleHub-$PackageVersion-x64.deb"
    if ($ReleaseBaseUrl) {
        return "$ReleaseBaseUrl/$VersionTag/$DebName"
    }
    if ($Version -eq "latest") {
        return "https://github.com/$Repo/releases/latest/download/$DebName"
    }
    return "https://github.com/$Repo/releases/download/$VersionTag/$DebName"
}

function Resolve-WindowsAppUrl {
    param([string]$VersionTag)

    if ($env:BUBBLEHUB_WINDOWS_APP_URL) {
        return $env:BUBBLEHUB_WINDOWS_APP_URL
    }

    $PackageVersion = $VersionTag.TrimStart("v")
    $AppName = "BubbleHub-$PackageVersion-control-center-x64.exe"
    if ($ReleaseBaseUrl) {
        return "$ReleaseBaseUrl/$VersionTag/$AppName"
    }
    if ($Version -eq "latest") {
        return "https://github.com/$Repo/releases/latest/download/$AppName"
    }
    return "https://github.com/$Repo/releases/download/$VersionTag/$AppName"
}

function Stop-WindowsProcessByCommandLine {
    param([string]$Needle)

    if (-not $Needle) {
        return
    }

    $Processes = @(
        Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandLine -and $_.CommandLine.IndexOf($Needle, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 }
    )
    foreach ($Process in $Processes) {
        Write-Host "Stopping BubbleHub helper process $($Process.ProcessId)..."
        Stop-Process -Id $Process.ProcessId -Force -ErrorAction SilentlyContinue
        try {
            Wait-Process -Id $Process.ProcessId -Timeout 10 -ErrorAction SilentlyContinue
        } catch {
            # The process may already have exited.
        }
    }
}

function Stop-WindowsProcessByPidFile {
    param([string]$PidFile)

    if (-not $PidFile -or -not (Test-Path $PidFile)) {
        return
    }

    $ProcessIdText = (Get-Content -Raw -Path $PidFile -ErrorAction SilentlyContinue).Trim()
    if ($ProcessIdText -match "^\d+$") {
        Write-Host "Stopping BubbleHub helper process $ProcessIdText from $PidFile..."
        Stop-Process -Id ([int]$ProcessIdText) -Force -ErrorAction SilentlyContinue
        try {
            Wait-Process -Id ([int]$ProcessIdText) -Timeout 10 -ErrorAction SilentlyContinue
        } catch {
            # The process may already have exited.
        }
    }
    Remove-Item -Force $PidFile -ErrorAction SilentlyContinue
}

function Stop-WslControlApi {
    param([int]$Port)

    $KillCommand = @'
set +e
port="__APP_PORT__"
case "$port" in
  ''|*[!0-9]*) port=8010 ;;
esac
pid_file="/tmp/bubblehub-control-center-$port.pid"
if [ -f "$pid_file" ]; then
  pid="$(cat "$pid_file" 2>/dev/null || true)"
  case "$pid" in
    ''|*[!0-9]*) ;;
    *)
      kill -TERM "$pid" >/dev/null 2>&1 || true
      sleep 1
      kill -KILL "$pid" >/dev/null 2>&1 || true
      ;;
  esac
  rm -f "$pid_file"
fi
if command -v ss >/dev/null 2>&1; then
  for pid in $(ss -H -ltnp "sport = :$port" 2>/dev/null | sed -n 's/.*pid=\([0-9][0-9]*\).*/\1/p' | sort -u); do
    kill -TERM "$pid" >/dev/null 2>&1 || true
  done
fi
ps -eo pid=,args= 2>/dev/null | awk -v port="$port" '$0 ~ "[a]pp --host 127.0.0.1 --port " port { print $1 }' | while read -r pid; do
  kill -TERM "$pid" >/dev/null 2>&1 || true
done
for inode in $(awk -v port="$port" 'BEGIN { p=sprintf("%04X", port + 0) } $4 == "0A" { split($2, a, ":"); if (toupper(a[2]) == p) print $10 }' /proc/net/tcp /proc/net/tcp6 2>/dev/null | sort -u); do
  for fd in /proc/[0-9]*/fd/*; do
    target="$(readlink "$fd" 2>/dev/null || true)"
    if [ "$target" = "socket:[$inode]" ]; then
      pid="${fd#/proc/}"
      pid="${pid%%/*}"
      kill -TERM "$pid" >/dev/null 2>&1 || true
    fi
  done
done
pkill -TERM -f "[a]pp --host 127.0.0.1 --port $port" >/dev/null 2>&1 || true
sleep 1
ps -eo pid=,args= 2>/dev/null | awk -v port="$port" '$0 ~ "[a]pp --host 127.0.0.1 --port " port { print $1 }' | while read -r pid; do
  kill -KILL "$pid" >/dev/null 2>&1 || true
done
if command -v ss >/dev/null 2>&1; then
  for pid in $(ss -H -ltnp "sport = :$port" 2>/dev/null | sed -n 's/.*pid=\([0-9][0-9]*\).*/\1/p' | sort -u); do
    kill -KILL "$pid" >/dev/null 2>&1 || true
  done
fi
'@.Replace("__APP_PORT__", [string]$Port)

    try {
        Invoke-WslBash -AsRoot -Command $KillCommand 2>$null | Out-Null
    } catch {
        Write-Warning "Could not stop existing BubbleHub WSL Control API. Continuing install: $($_.Exception.Message)"
    }
}

function Stop-WindowsControlCenter {
    param(
        [string]$AppPath,
        [string]$InstallRoot,
        [int]$Port
    )

    if ($InstallRoot) {
        Stop-WindowsProcessByPidFile -PidFile (Join-Path $InstallRoot "bubblehub-control-center-server.pid")
        Stop-WindowsProcessByCommandLine -Needle (Join-Path $InstallRoot "bubblehub-control-center-server.ps1")
        Stop-WindowsProcessByCommandLine -Needle (Join-Path $InstallRoot "bubblehub-control-center.ps1")
    }

    if ($Port -gt 0) {
        Stop-WslControlApi -Port $Port
    }

    if ($AppPath) {
        $FullAppPath = [System.IO.Path]::GetFullPath($AppPath)
        $Processes = @(
            Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
                Where-Object { $_.ExecutablePath -and ([string]::Equals($_.ExecutablePath, $FullAppPath, [System.StringComparison]::OrdinalIgnoreCase)) }
        )
        foreach ($Process in $Processes) {
            Write-Host "Stopping existing BubbleHub Windows Control Center process $($Process.ProcessId)..."
            Stop-Process -Id $Process.ProcessId -Force -ErrorAction SilentlyContinue
            try {
                Wait-Process -Id $Process.ProcessId -Timeout 10 -ErrorAction SilentlyContinue
            } catch {
                # The process may already have exited.
            }
        }
    }
}

function Install-WindowsControlCenter {
    param([string]$VersionTag)

    $InstallRoot = Join-Path $env:LOCALAPPDATA "BubbleHub"
    New-Item -ItemType Directory -Force -Path $InstallRoot | Out-Null
    $AppPath = Join-Path $InstallRoot "BubbleHub.exe"
    $TempAppPath = "$AppPath.download"
    $AppPort = if ($env:BUBBLEHUB_APP_PORT) { [int]$env:BUBBLEHUB_APP_PORT } else { 8010 }
    Stop-WindowsControlCenter -AppPath $AppPath -InstallRoot $InstallRoot -Port $AppPort
    Remove-Item -Force $TempAppPath -ErrorAction SilentlyContinue
    if ($env:BUBBLEHUB_WINDOWS_APP_LOCAL_PATH) {
        Write-Host "Installing BubbleHub Windows Control Center from $($env:BUBBLEHUB_WINDOWS_APP_LOCAL_PATH)..."
        Copy-Item -Force $env:BUBBLEHUB_WINDOWS_APP_LOCAL_PATH $TempAppPath
        Move-Item -Force $TempAppPath $AppPath
        return $AppPath
    }
    $AppUrl = Resolve-WindowsAppUrl $VersionTag
    Write-Host "Installing BubbleHub Windows Control Center from $AppUrl..."
    Invoke-WebRequest -UseBasicParsing -Uri $AppUrl -OutFile $TempAppPath -TimeoutSec 300
    Move-Item -Force $TempAppPath $AppPath
    return $AppPath
}

function Install-DebInWsl {
    param(
        [string]$VersionTag,
        [string]$DebUrl
    )

    $PackageVersion = $VersionTag.TrimStart("v")
    $DebName = "BubbleHub-$PackageVersion-x64.deb"
    $QuotedDebUrl = ConvertTo-BashSingleQuoted $DebUrl
    $AptEnv = ""
    $AptEnv += Get-BashEnvAssignment "DEBIAN_FRONTEND" $(if ($env:DEBIAN_FRONTEND) { $env:DEBIAN_FRONTEND } else { "noninteractive" })
    if ($env:TZ) {
        $AptEnv += Get-BashEnvAssignment "TZ" $env:TZ
    }
    $InstallCommand = @"
set -euo pipefail
deb_path="/tmp/$DebName"
cleanup() { rm -f "$DebName" "/tmp/$DebName"; }
trap cleanup EXIT
env ${AptEnv}apt-get update
env ${AptEnv}apt-get install -y --no-install-recommends ca-certificates curl sudo
curl -fsSL $QuotedDebUrl -o /tmp/$DebName
env ${AptEnv}apt-get install -y /tmp/$DebName
"@

    Invoke-WslBashChecked -AsRoot -Command $InstallCommand
}

if ($env:OS -eq "Windows_NT") {
    Assert-WslReady

    $ResolvedVersion = Resolve-VersionTag
    $DebUrl = Resolve-DebUrl $ResolvedVersion
    Write-Host "Installing BubbleHub $ResolvedVersion into WSL distro '$WslDistro' from $DebUrl..."
    Install-DebInWsl -VersionTag $ResolvedVersion -DebUrl $DebUrl
    if ($LASTEXITCODE -eq 0) {
        Invoke-WslBash "command -v bubble >/dev/null 2>&1"
        $DesktopInstalled = ($LASTEXITCODE -eq 0)
        $WindowsAppPath = Install-WindowsControlCenter -VersionTag $ResolvedVersion
        Install-WindowsLaunchers -InstallDesktopShortcut:$DesktopInstalled -WslDistroName $WslDistro -WindowsAppPath $WindowsAppPath -ExpectedVersion $ResolvedVersion.TrimStart("v")
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
