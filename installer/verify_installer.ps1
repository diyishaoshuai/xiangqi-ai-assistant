# Run only on a test account without another installed copy or existing app data.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Installer,
    [string]$PreviousInstaller,
    [string]$ProjectDirectory = (Split-Path -Parent $PSScriptRoot)
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$Installer = (Resolve-Path -LiteralPath $Installer).Path
$ProjectDirectory = (Resolve-Path -LiteralPath $ProjectDirectory).Path
$registryKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{E81D6970-29D6-4A26-9B62-8F0AA83A1260}_is1'
$localData = Join-Path $env:LOCALAPPDATA 'XiangqiAI'
$legacyData = Join-Path $env:USERPROFILE '.xiangqi_ai'
$desktopLink = Join-Path ([Environment]::GetFolderPath('Desktop')) '象棋 AI 助手.lnk'
$startMenuGroup = Join-Path ([Environment]::GetFolderPath('Programs')) '象棋 AI 助手'
foreach ($existing in @($registryKey, $localData, $legacyData, $desktopLink, $startMenuGroup)) {
    if (Test-Path -LiteralPath $existing) { throw "Refusing to overwrite existing installation/data: $existing" }
}
$runId = (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + $PID
$testRoot = Join-Path $ProjectDirectory "build\uninstall-verification\$runId"
$null = New-Item -ItemType Directory -Path $testRoot
$originalHash = (Get-FileHash -LiteralPath $Installer -Algorithm SHA256).Hash
$utf8 = New-Object System.Text.UTF8Encoding($false)
$checks = New-Object 'System.Collections.Generic.List[string]'

Add-Type @'
using System;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;
public static class XiangqiVerifyWindows {
    public delegate bool EnumWindowProc(IntPtr window, IntPtr data);
    [DllImport("user32.dll")] private static extern bool EnumWindows(EnumWindowProc callback, IntPtr data);
    [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(IntPtr window, out uint pid);
    [DllImport("user32.dll")] private static extern bool PostMessage(IntPtr window, uint msg, IntPtr w, IntPtr l);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern SafeFileHandle CreateFile(string path, uint access, uint share, IntPtr security, uint creation, uint flags, IntPtr template);
    public static SafeFileHandle LockDirectory(string path) {
        return CreateFile(path, 0x80000000, 3, IntPtr.Zero, 3, 0x02000000, IntPtr.Zero);
    }
    public static void Close(uint targetPid) {
        EnumWindows((window, data) => {
            uint pid; GetWindowThreadProcessId(window, out pid);
            if (pid == targetPid) PostMessage(window, 0x10, IntPtr.Zero, IntPtr.Zero);
            return true;
        }, IntPtr.Zero);
    }
}
'@

function Assert-True([bool]$Value, [string]$Message) {
    if (-not $Value) { throw "FAILED: $Message (logs: $testRoot)" }
    $checks.Add($Message)
    Write-Output "PASS: $Message"
}
function Write-Fixture([string]$Path, [string]$Contents = 'uninstall verification fixture') {
    $null = New-Item -ItemType Directory -Path (Split-Path -Parent $Path) -Force
    [IO.File]::WriteAllText($Path, $Contents, $utf8)
}
function Invoke-Hidden([string]$Path, [string]$Arguments, [int]$TimeoutSeconds = 90) {
    $process = Start-Process -FilePath $Path -ArgumentList $Arguments -PassThru -WindowStyle Hidden
    if (-not $process.WaitForExit($TimeoutSeconds * 1000)) { throw "Timed out (PID $($process.Id)): $Path" }
    $process.Refresh()
    return $process.ExitCode
}
function Install-TestCopy([string]$Name, [string]$Setup = $Installer) {
    $script:installDir = Join-Path $testRoot "$Name\安装 应用"
    $script:groupName = '象棋 AI 助手'
    $log = Join-Path $testRoot "$Name-install-$([IO.Path]::GetFileName($Setup)).log"
    $arguments = "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- /DIR=`"$installDir`" /GROUP=`"$groupName`" /TASKS=desktopicon /LOG=`"$log`""
    Assert-True ((Invoke-Hidden $Setup $arguments) -eq 0) "$Name setup exits successfully"
    Assert-True (Test-Path -LiteralPath (Join-Path $installDir 'unins000.exe')) "$Name uninstaller exists"
    Assert-True (Test-Path -LiteralPath $registryKey) "$Name uninstall registration exists"
    Assert-True (Test-Path -LiteralPath $desktopLink) "$Name desktop shortcut created"
}
function Uninstall-TestCopy([string]$LogName) {
    $log = Join-Path $testRoot "$LogName-uninstall.log"
    return Invoke-Hidden (Join-Path $installDir 'unins000.exe') "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG=`"$log`""
}
function Assert-Clean([string]$Name, [bool]$AllowUserFiles = $false) {
    # Inno's temporary worker may still be removing its own executable briefly.
    $deadline = [DateTime]::UtcNow.AddSeconds(15)
    while ((Test-Path -LiteralPath (Join-Path $installDir 'unins000.exe')) -and [DateTime]::UtcNow -lt $deadline) {
        Start-Sleep -Milliseconds 100
    }
    Assert-True (-not (Test-Path -LiteralPath $registryKey)) "$Name uninstall registry removed"
    Assert-True (-not (Test-Path -LiteralPath $desktopLink)) "$Name desktop shortcut removed"
    $group = Join-Path ([Environment]::GetFolderPath('Programs')) $groupName
    Assert-True (-not (Test-Path -LiteralPath $group)) "$Name start menu shortcuts removed"
    Assert-True (-not (Test-Path -LiteralPath $localData)) "$Name LocalAppData removed"
    Assert-True (-not (Test-Path -LiteralPath $legacyData)) "$Name legacy profile data removed"
    if (-not $AllowUserFiles) {
        Assert-True (-not (Test-Path -LiteralPath $installDir)) "$Name complete installation directory removed"
    }
    Assert-True ((Get-FileHash -LiteralPath $Installer -Algorithm SHA256).Hash -eq $originalHash) "$Name original installer unchanged"
}
function Start-SmokeSession([string]$Executable, [bool]$Seed = $false) {
    $arguments = '--uninstall-smoke-session'
    if ($Seed) { $arguments += ' --seed-uninstall-data --uninstall-smoke-thinking' }
    $startedAt = Get-Date
    $process = Start-Process -FilePath $Executable -ArgumentList $arguments -PassThru -WindowStyle Hidden
    $log = Join-Path (Split-Path -Parent $Executable) 'logs\xiangqi-ai.log'
    $deadline = [DateTime]::UtcNow.AddSeconds(40)
    while ([DateTime]::UtcNow -lt $deadline) {
        $process.Refresh()
        if ($process.HasExited) { throw "Smoke app exited early ($($process.ExitCode)): $log" }
        if ((Test-Path -LiteralPath $log) -and (Get-Item -LiteralPath $log).LastWriteTime -ge $startedAt -and
            (Select-String -LiteralPath $log -SimpleMatch 'UNINSTALL_SMOKE_READY' -Quiet)) { return $process }
        Start-Sleep -Milliseconds 200
    }
    throw "Smoke app not ready (PID $($process.Id)): $log"
}
function Close-TestSession($Process) {
    $Process.Refresh()
    if (-not $Process.HasExited) {
        [XiangqiVerifyWindows]::Close($Process.Id)
        if (-not $Process.WaitForExit(15000)) { throw "Test app failed to close gracefully: $($Process.Id)" }
    }
}
function Seed-LegacyData {
    Write-Fixture (Join-Path $legacyData 'recognition_templates.json') '{}'
    Write-Fixture (Join-Path $localData 'recognition_templates.json') '{}'
    foreach ($logs in @((Join-Path $localData 'logs'), (Join-Path $installDir 'XiangqiAI\logs'))) {
        Write-Fixture (Join-Path $logs 'xiangqi-ai.log')
        foreach ($number in 1..3) { Write-Fixture (Join-Path $logs "xiangqi-ai.log.$number") }
    }
    Write-Fixture (Join-Path $installDir '_internal\cv2\__pycache__\config.cpython-311.pyc')
    (Get-Item -LiteralPath (Join-Path $localData 'recognition_templates.json')).IsReadOnly = $true
}

Write-Output "Verification artifacts: $testRoot"
# Fresh install, a hidden running app, real NNUE engine, logs and learned template.
Install-TestCopy 'fresh'
$manifest = @(Get-Content -LiteralPath (Join-Path $installDir '.xiangqi-installed-files.txt'))
Assert-True ($manifest.Count -gt 100 -and $manifest -contains 'XiangqiAI.exe') 'manifest records actual installed relative paths'
Assert-True ((Invoke-Hidden (Join-Path $installDir 'XiangqiAI.exe') '--engine-self-test') -eq 0) 'installed engine self-test'
Assert-True (@(Get-ChildItem -LiteralPath $installDir -Recurse -Filter '*.pyc').Count -eq 0) 'runtime hook prevents new bytecode cache files'
$installed = Start-SmokeSession (Join-Path $installDir 'XiangqiAI.exe') $true
Assert-True (Test-Path -LiteralPath (Join-Path $localData 'recognition_templates.json')) 'real recognition template created'
Assert-True (Test-Path -LiteralPath (Join-Path $installDir 'logs\xiangqi-ai.log.3')) 'real log rotation created'
$installedEngines = @(Get-Process -Name 'pikafish-sse41-popcnt' -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq (Join-Path $installDir '_internal\engine\pikafish-sse41-popcnt.exe') })
Assert-True ($installedEngines.Count -gt 0) 'installed engine running before uninstall'
Seed-LegacyData
$portable = Start-SmokeSession (Join-Path $ProjectDirectory 'dist\XiangqiAI\XiangqiAI.exe')
try {
    Assert-True ((Uninstall-TestCopy 'fresh') -eq 0) 'running hidden app uninstall succeeds'
    Assert-True ($installed.WaitForExit(5000)) 'hidden app closed by uninstaller'
    foreach ($engine in $installedEngines) { Assert-True ($engine.WaitForExit(5000)) 'installed engine closed by uninstaller' }
    $portable.Refresh()
    Assert-True (-not $portable.HasExited) 'same-named portable app from another directory is untouched'
    Assert-Clean 'fresh'
} finally { Close-TestSession $portable }

# An unrelated process holds a packaged file without FILE_SHARE_DELETE.
Install-TestCopy 'locked'
$lock = [IO.File]::Open((Join-Path $installDir 'README.md'), [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
try {
    $exitCode = Uninstall-TestCopy 'locked-blocked'
    Assert-True ($exitCode -ne 0) 'external lock reports uninstall failure instead of success'
    Assert-True (Test-Path -LiteralPath (Join-Path $installDir 'unins000.exe')) 'blocked uninstall preserves uninstaller'
    Assert-True (Test-Path -LiteralPath (Join-Path $installDir 'XiangqiAI.exe')) 'blocked uninstall preserves application'
    Assert-True (Test-Path -LiteralPath $registryKey) 'blocked uninstall preserves registry for retry'
} finally { $lock.Dispose() }
$lock = [IO.File]::Open((Join-Path $installDir '.xiangqi-installed-files.txt'), [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
try {
    Assert-True ((Uninstall-TestCopy 'locked-manifest') -ne 0) 'locked manifest blocks deletion and retains retry path'
    Assert-True (Test-Path -LiteralPath (Join-Path $installDir 'XiangqiAI.exe')) 'manifest lock leaves application intact'
} finally { $lock.Dispose() }
$directoryLock = [XiangqiVerifyWindows]::LockDirectory($installDir)
try {
    Assert-True (-not $directoryLock.IsInvalid) 'directory lock fixture holds a real directory handle'
    Assert-True ((Uninstall-TestCopy 'locked-directory') -ne 0) 'directory lock blocks deletion before leaving empty residual folders'
    Assert-True (Test-Path -LiteralPath (Join-Path $installDir 'XiangqiAI.exe')) 'directory lock leaves application intact'
} finally { $directoryLock.Dispose() }
Assert-True ((Uninstall-TestCopy 'locked-retry') -eq 0) 'uninstall succeeds after external lock is released'
Assert-Clean 'locked'

# Orphaned engine holds its stdin open and ignores window messages; force-close fallback.
Install-TestCopy 'orphan'
$startInfo = New-Object Diagnostics.ProcessStartInfo
$startInfo.FileName = Join-Path $installDir '_internal\engine\pikafish-sse41-popcnt.exe'
$startInfo.WorkingDirectory = Split-Path -Parent $startInfo.FileName
$startInfo.UseShellExecute = $false
$startInfo.CreateNoWindow = $true
$startInfo.RedirectStandardInput = $true
$startInfo.RedirectStandardOutput = $true
$orphan = [Diagnostics.Process]::Start($startInfo)
try {
    Assert-True (-not $orphan.HasExited) 'orphan engine running before uninstall'
    $orphanLog = Join-Path $testRoot 'orphan-uninstall.log'
    $uninstaller = Start-Process -FilePath (Join-Path $installDir 'unins000.exe') -ArgumentList "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG=`"$orphanLog`"" -PassThru -WindowStyle Hidden
    $hasher = [Security.Cryptography.SHA256]::Create()
    try { $digest = -join ($hasher.ComputeHash([Text.Encoding]::Unicode.GetBytes($installDir.ToLowerInvariant())) | ForEach-Object { $_.ToString('x2') }) }
    finally { $hasher.Dispose() }
    $eventName = 'Local\XiangqiAI.Maintenance.' + $digest
    $maintenanceEvent = $null
    $deadline = [DateTime]::UtcNow.AddSeconds(10)
    while ([DateTime]::UtcNow -lt $deadline) {
        try { $maintenanceEvent = [Threading.EventWaitHandle]::OpenExisting($eventName); break }
        catch [Threading.WaitHandleCannotBeOpenedException] { Start-Sleep -Milliseconds 50 }
    }
    Assert-True ($null -ne $maintenanceEvent) 'uninstaller publishes matching installation-scoped maintenance event'
    try {
        Assert-True ($maintenanceEvent.WaitOne(0)) 'maintenance event is signaled before deletion'
        Assert-True ((Invoke-Hidden (Join-Path $installDir 'XiangqiAI.exe') '--uninstall-smoke-session' 3) -eq 0) 'application relaunch blocked cleanly during uninstall'
    } finally { $maintenanceEvent.Dispose() }
    Assert-True ($uninstaller.WaitForExit(30000)) 'orphan engine uninstaller completes'
    $uninstaller.Refresh()
    Assert-True ($uninstaller.ExitCode -eq 0) 'orphan engine uninstall succeeds'
    Assert-True ($orphan.WaitForExit(5000)) 'orphan engine terminated'
    Assert-Clean 'orphan'
} finally { $orphan.Dispose() }

# Preserve user content, including a downloaded setup file placed inside the app dir.
Install-TestCopy 'user-content'
$notes = Join-Path $installDir '用户笔记.txt'
Write-Fixture $notes 'user-owned document: must survive'
$embeddedSetup = Join-Path $installDir '保留的安装包.exe'
Copy-Item -LiteralPath $Installer -Destination $embeddedSetup
$junctionTarget = Join-Path $testRoot 'outside-installation'
$sentinel = Join-Path $junctionTarget 'keep.txt'
Write-Fixture $sentinel 'must not traverse this junction'
$junction = Join-Path $installDir '_internal\user-junction'
$null = New-Item -ItemType Junction -Path $junction -Target $junctionTarget
Assert-True ((Uninstall-TestCopy 'user-content') -eq 0) 'uninstall with user content succeeds'
Assert-Clean 'user-content' $true
Assert-True ((Get-Content -LiteralPath $notes -Raw) -eq 'user-owned document: must survive') 'user-added file preserved'
Assert-True ((Get-FileHash -LiteralPath $embeddedSetup -Algorithm SHA256).Hash -eq $originalHash) 'installer inside app directory preserved'
Assert-True ((Get-Content -LiteralPath $sentinel -Raw) -eq 'must not traverse this junction') 'external junction target untouched'
Assert-True (Test-Path -LiteralPath $junction) 'user-added junction preserved'
$remaining = @(Get-ChildItem -LiteralPath $installDir -Force -Recurse -File)
Assert-True ($remaining.Count -eq 2) 'only two intentionally preserved user files remain'

if ($PreviousInstaller) {
    $PreviousInstaller = (Resolve-Path -LiteralPath $PreviousInstaller).Path
    Install-TestCopy 'upgrade' $PreviousInstaller
    Seed-LegacyData
    $oldProcess = Start-Process -FilePath (Join-Path $installDir 'XiangqiAI.exe') -PassThru -WindowStyle Hidden
    Start-Sleep -Seconds 3
    Install-TestCopy 'upgrade'
    Assert-True ($oldProcess.WaitForExit(5000)) 'upgrade closes old version process'
    Assert-True ((Get-Item -LiteralPath (Join-Path $installDir 'unins000.exe')).VersionInfo.ProductVersion -ne '') 'upgraded uninstaller exists'
    $upgraded = Start-SmokeSession (Join-Path $installDir 'XiangqiAI.exe') $true
    Assert-True ((Uninstall-TestCopy 'upgrade') -eq 0) 'old-version upgrade uninstall succeeds'
    Assert-True ($upgraded.WaitForExit(5000)) 'upgraded running app closed'
    Assert-Clean 'upgrade'
}
[IO.File]::WriteAllLines((Join-Path $testRoot 'passed-checks.txt'), $checks, $utf8)
Write-Output "ALL PASSED: $($checks.Count) assertions. Artifacts: $testRoot"
