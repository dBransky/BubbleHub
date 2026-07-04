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

function Assert-ReleaseSmokeAssets {
    param([string]$VersionTag)

    $Version = $VersionTag.TrimStart("v")
    $ReleaseDir = Join-Path $AssetsDir $VersionTag
    $RequiredAssets = @(
        "install.ps1"
        "BubbleHub-$Version-x64.deb"
        "BubbleHub-$Version-x64.exe"
        "BubbleHub-$Version-control-center-x64.exe"
    )

    foreach ($Asset in $RequiredAssets) {
        $Path = Join-Path $ReleaseDir $Asset
        if (-not (Test-Path $Path)) {
            throw "Missing release smoke asset: $Path"
        }
    }
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

    $CandidatePaths = @()
    $ToolRoots = @(
        $env:pythonLocation,
        $env:PYTHON_HOME,
        $env:RUNNER_TOOL_CACHE
    ) | Where-Object { $_ }
    foreach ($WorkspaceRoot in @($env:RUNNER_WORKSPACE, $env:GITHUB_WORKSPACE) | Where-Object { $_ }) {
        $WorkRoot = Split-Path $WorkspaceRoot -Parent
        if ((Split-Path $WorkRoot -Leaf) -ne "_work") {
            $WorkRoot = Split-Path $WorkRoot -Parent
        }
        if ($WorkRoot) {
            $ToolRoots += Join-Path $WorkRoot "_tool"
        }
    }

    foreach ($Root in $ToolRoots) {
        $CandidatePaths += @(
            (Join-Path $Root "python.exe")
            (Join-Path $Root "python3.exe")
            (Join-Path $Root "x64/python.exe")
            (Join-Path $Root "x86/python.exe")
        )
        $PythonToolRoot = Join-Path $Root "Python"
        if (Test-Path $PythonToolRoot) {
            $CandidatePaths += @(
                Get-ChildItem -Path $PythonToolRoot -Filter python.exe -Recurse -File -ErrorAction SilentlyContinue |
                    Sort-Object FullName -Descending |
                    ForEach-Object { $_.FullName }
            )
        }
        if ((Split-Path $Root -Leaf) -eq "Python") {
            $CandidatePaths += @(
                Get-ChildItem -Path $Root -Filter python.exe -Recurse -File -ErrorAction SilentlyContinue |
                    Sort-Object FullName -Descending |
                    ForEach-Object { $_.FullName }
            )
        }
    }
    foreach ($CandidatePath in $CandidatePaths | Where-Object { $_ } | Select-Object -Unique) {
        if (-not (Test-Path $CandidatePath)) {
            continue
        }
        $Candidate = @([string]$CandidatePath)
        if (Test-PythonExecutable $Candidate) {
            Write-Output $Candidate -NoEnumerate
            return
        }
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
Install Python on the self-hosted runner PATH or set BUBBLEHUB_PYTHON to a working python.exe.
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

function Start-WslArtifactServer {
    param([string]$Distro)

    $AssetsPath = (Resolve-Path $AssetsDir).Path
    if ($AssetsPath -match "^([A-Za-z]):\\(.*)$") {
        $Drive = $Matches[1].ToLowerInvariant()
        $Rest = $Matches[2] -replace "\\", "/"
        $WslAssetsPath = "/mnt/$Drive/$Rest"
    } else {
        $WslAssetsPath = (& wsl.exe -d $Distro wslpath -a $AssetsPath).Trim()
    }
    if (-not $WslAssetsPath) {
        throw "Failed to resolve release smoke assets path inside WSL distro '$Distro': $AssetsPath"
    }

    $Arguments = @("-d", $Distro, "--cd", $WslAssetsPath, "--exec", "python3", "-m", "http.server", "$Port", "--bind", "127.0.0.1")
    $Process = Start-Process -FilePath "wsl.exe" -ArgumentList $Arguments -PassThru -WindowStyle Hidden

    $Url = "http://127.0.0.1:$Port/previous/VERSION_TAG"
    for ($i = 0; $i -lt 30; $i++) {
        if ($Process.HasExited) {
            throw "WSL artifact server process exited before becoming ready (exit code $($Process.ExitCode))."
        }
        wsl.exe -d $Distro bash -lc "curl -fsSL --connect-timeout 2 '$Url' >/dev/null 2>&1"
        if ($LASTEXITCODE -eq 0) {
            Write-Host "Started WSL artifact server in '$Distro' at $Url"
            return [pscustomobject]@{
                Distro = $Distro
                Port = $Port
                Process = $Process
            }
        }
        Start-Sleep -Seconds 1
    }

    Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
    wsl.exe -d $Distro bash -lc "pkill -f 'python3 -m http.server $Port' >/dev/null 2>&1 || true" 2>$null | Out-Null
    throw "Timed out waiting for WSL artifact server at $Url"
}

function Stop-WslArtifactServer {
    param([object]$Server)

    if ($Server -and $Server.Distro -and $Server.Port) {
        if ($Server.Process) {
            Stop-Process -Id $Server.Process.Id -Force -ErrorAction SilentlyContinue
        }
        wsl.exe -d $Server.Distro bash -lc "pkill -f 'python3 -m http.server $($Server.Port)' >/dev/null 2>&1 || true" 2>$null | Out-Null
        $global:LASTEXITCODE = 0
    }
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
export DEBIAN_FRONTEND=noninteractive
if dpkg-query -W -f='${Status}' bubblehub 2>/dev/null | grep -q 'install ok installed'; then
  apt-get purge -y bubblehub >/dev/null
fi
dpkg --purge --force-all bubblehub >/dev/null 2>&1 || true
rm -rf /opt/bubblehub
rm -f /usr/bin/bubble /usr/bin/bubblehub /usr/bin/bubblehub-node /usr/bin/bubblehub-sandbox /usr/bin/llama-server
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

function Stop-WindowsProcessByPath {
    param([string]$Path)

    if (-not $Path) {
        return
    }

    $FullPath = [System.IO.Path]::GetFullPath($Path)
    $Processes = @(
        Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
            Where-Object { $_.ExecutablePath -and ([string]::Equals($_.ExecutablePath, $FullPath, [System.StringComparison]::OrdinalIgnoreCase)) }
    )
    foreach ($Process in $Processes) {
        Stop-Process -Id $Process.ProcessId -Force -ErrorAction SilentlyContinue
        try {
            Wait-Process -Id $Process.ProcessId -Timeout 10 -ErrorAction SilentlyContinue
        } catch {
            # The process may already have exited.
        }
    }
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
        Stop-Process -Id $Process.ProcessId -Force -ErrorAction SilentlyContinue
        try {
            Wait-Process -Id $Process.ProcessId -Timeout 10 -ErrorAction SilentlyContinue
        } catch {
            # The process may already have exited.
        }
    }
}

function Stop-BubbleHubApp {
    param(
        [string]$Distro,
        [int]$AppPort = 8010
    )

    $InstallRoot = Join-Path $env:LOCALAPPDATA "BubbleHub"
    Stop-WindowsProcessByCommandLine -Needle (Join-Path $InstallRoot "bubblehub-control-center-server.ps1")
    Stop-WindowsProcessByCommandLine -Needle (Join-Path $InstallRoot "bubblehub-control-center.ps1")

    $WindowsApp = Join-Path $env:LOCALAPPDATA "BubbleHub/BubbleHub.exe"
    Stop-WindowsProcessByPath -Path $WindowsApp

    if ($Distro) {
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
pkill -TERM -f '[b]ubblehub-control-center' >/dev/null 2>&1 || true
pkill -TERM -f '/opt/[b]ubblehub/share/bubblehub/app/bubblehub' >/dev/null 2>&1 || true
sleep 1
ps -eo pid=,args= 2>/dev/null | awk -v port="$port" '$0 ~ "[a]pp --host 127.0.0.1 --port " port { print $1 }' | while read -r pid; do
  kill -KILL "$pid" >/dev/null 2>&1 || true
done
if command -v ss >/dev/null 2>&1; then
  for pid in $(ss -H -ltnp "sport = :$port" 2>/dev/null | sed -n 's/.*pid=\([0-9][0-9]*\).*/\1/p' | sort -u); do
    kill -KILL "$pid" >/dev/null 2>&1 || true
  done
fi
'@.Replace("__APP_PORT__", [string]$AppPort)
        wsl.exe -d $Distro -u root bash -lc $KillCommand 2>$null | Out-Null
    }

    $HealthUrl = "http://127.0.0.1:$AppPort/health"
    for ($i = 0; $i -lt 10; $i++) {
        try {
            Invoke-RestMethod -Uri $HealthUrl -TimeoutSec 1 | Out-Null
            Start-Sleep -Milliseconds 500
        } catch {
            return
        }
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
    $env:BUBBLEHUB_DEB_URL = "$WslBaseUrl/$VersionTag/BubbleHub-$Version-x64.deb"
    $env:BUBBLEHUB_WINDOWS_APP_URL = "$WindowsBaseUrl/$VersionTag/BubbleHub-$Version-control-center-x64.exe"
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
    $InstallerLog = Join-Path $env:TEMP "BubbleHub-$Version-installer.log"
    Write-Host "--- EXE installer smoke: $VersionTag ---"
    Invoke-WebRequest -UseBasicParsing -Uri $ExeUrl -OutFile $ExePath
    Remove-Item -Force $InstallerLog -ErrorAction SilentlyContinue
    $env:BUBBLEHUB_INSTALLER_LOG = $InstallerLog
    $Process = Start-Process -FilePath $ExePath -ArgumentList "/S" -Wait -PassThru
    if ($Process.ExitCode -ne 0) {
        if (Test-Path $InstallerLog) {
            Write-Host "--- BubbleHub EXE installer log ---"
            Get-Content -Path $InstallerLog | ForEach-Object { Write-Host $_ }
            Write-Host "--- End BubbleHub EXE installer log ---"
        }
        throw "BubbleHub EXE installer failed with exit code $($Process.ExitCode)."
    }
}

function Assert-InstalledVersion {
    param(
        [string]$VersionTag,
        [string]$Distro
    )

    $Version = $VersionTag.TrimStart("v")
    $Output = ((& wsl.exe -d $Distro bash -lc "bubble --version" 2>&1) -join "`n").Trim()
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

function Write-DesktopDebugSnapshot {
    param(
        [string]$Distro,
        [string]$ShortcutPath,
        [string]$Reason
    )

    Write-Host "--- BubbleHub desktop debug snapshot: $Reason ---"
    try {
        $Connections = @(Get-NetTCPConnection -LocalPort 8010 -State Listen -ErrorAction SilentlyContinue)
        if ($Connections.Count -eq 0) {
            Write-Host "No Windows TCP listener found on port 8010."
        } else {
            foreach ($Connection in $Connections) {
                Write-Host "Windows listener: $($Connection.LocalAddress):$($Connection.LocalPort) pid=$($Connection.OwningProcess)"
                Get-CimInstance Win32_Process -Filter "ProcessId=$($Connection.OwningProcess)" -ErrorAction SilentlyContinue |
                    Select-Object ProcessId,ParentProcessId,ExecutablePath,CommandLine |
                    Format-List |
                    Out-String |
                    ForEach-Object { Write-Host $_ }
            }
        }
    } catch {
        Write-Host "Could not inspect Windows TCP listener: $($_.Exception.Message)"
    }

    try {
        Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
            Where-Object {
                $_.CommandLine -match "BubbleHubReleaseSmoke|bubblehub-control-center|bubble app|8010|BubbleHub.exe" -or
                $_.ExecutablePath -match "BubbleHub.exe"
            } |
            Select-Object ProcessId,ParentProcessId,ExecutablePath,CommandLine |
            Format-List |
            Out-String |
            ForEach-Object { Write-Host $_ }
    } catch {
        Write-Host "Could not inspect BubbleHub-related Windows processes: $($_.Exception.Message)"
    }

    try {
        if (Test-Path $ShortcutPath) {
            $Shell = New-Object -ComObject WScript.Shell
            $Shortcut = $Shell.CreateShortcut($ShortcutPath)
            Write-Host "Shortcut target: $($Shortcut.TargetPath)"
            Write-Host "Shortcut arguments: $($Shortcut.Arguments)"
            Write-Host "Shortcut working directory: $($Shortcut.WorkingDirectory)"
        } else {
            Write-Host "Shortcut not found: $ShortcutPath"
        }
    } catch {
        Write-Host "Could not inspect shortcut: $($_.Exception.Message)"
    }

    $InstallRoot = Join-Path $env:LOCALAPPDATA "BubbleHub"
    foreach ($Path in @(
        (Join-Path $InstallRoot "bubblehub-control-center.ps1"),
        (Join-Path $InstallRoot "bubblehub-control-center-server.ps1"),
        (Join-Path $InstallRoot "bubblehub-control-center-server.pid")
    )) {
        try {
            if (Test-Path $Path) {
                Write-Host "--- $Path ---"
                Get-Content -Path $Path -ErrorAction Stop | ForEach-Object { Write-Host $_ }
                Write-Host "--- end $Path ---"
            } else {
                Write-Host "Missing expected file: $Path"
            }
        } catch {
            Write-Host "Could not read $Path`: $($_.Exception.Message)"
        }
    }

    if ($Distro) {
        try {
            Write-Host "--- WSL desktop processes in $Distro ---"
            wsl.exe -d $Distro -u root bash -lc "set +e; echo 'ss:'; ss -H -ltnp 'sport = :8010' 2>/dev/null || true; echo 'pid file:'; ls -l /tmp/bubblehub-control-center-8010.pid 2>/dev/null || true; cat /tmp/bubblehub-control-center-8010.pid 2>/dev/null || true; echo; echo 'processes:'; ps -ef | grep -E 'bubble|8010|python' | grep -v grep || true" |
                ForEach-Object { Write-Host $_ }
            Write-Host "--- end WSL desktop processes ---"
        } catch {
            Write-Host "Could not inspect WSL desktop processes: $($_.Exception.Message)"
        }
    }

    Write-Host "--- End BubbleHub desktop debug snapshot ---"
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
    $LastHealth = $null
    try {
        for ($i = 0; $i -lt 60; $i++) {
            try {
                $Health = Invoke-RestMethod -Uri "http://127.0.0.1:8010/health"
                $LastHealth = $Health | ConvertTo-Json -Compress
                if ($Health.service -eq "bubblehub" -and $Health.version -eq $Version) {
                    return
                }
                Write-Host "BubbleHub desktop health returned unexpected version while waiting for $VersionTag`: $LastHealth"
                if ($i -eq 0 -or $i -eq 10 -or $i -eq 30) {
                    Write-DesktopDebugSnapshot -Distro $Distro -ShortcutPath $ShortcutPath -Reason "health version mismatch while waiting for $VersionTag"
                }
                Start-Sleep -Seconds 1
            } catch {
                $LastHealth = $_.Exception.Message
                Start-Sleep -Seconds 1
            }
        }
        throw "Timed out waiting for BubbleHub desktop launch health response for $VersionTag. Last health result: $LastHealth"
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
Assert-ReleaseSmokeAssets -VersionTag $PreviousTag
Assert-ReleaseSmokeAssets -VersionTag $CurrentTag
$Server = $null
$WslServer = $null
$Distro = $null

try {
    Assert-RunnerPrerequisites
    $Server = Start-ArtifactServer
    $Distro = New-DisposableWslDistro
    Clear-PreviousInstall -Distro $Distro.Name
    $WslServer = Start-WslArtifactServer -Distro $Distro.Name
    $WslBaseUrl = "http://127.0.0.1:$Port"

    Install-And-Verify -VersionTag $PreviousTag -Distro $Distro.Name -WslBaseUrl $WslBaseUrl
    Install-And-Verify -VersionTag $CurrentTag -Distro $Distro.Name -WslBaseUrl $WslBaseUrl
} finally {
    if ($WslServer) {
        Stop-WslArtifactServer -Server $WslServer
    }
    if ($Distro) {
        Stop-BubbleHubApp -Distro $Distro.Name
        Remove-DisposableWslDistro -Name $Distro.Name -Root $Distro.Root
    }
    if ($Server) {
        Stop-Process -Id $Server.Id -Force -ErrorAction SilentlyContinue
    }
}
