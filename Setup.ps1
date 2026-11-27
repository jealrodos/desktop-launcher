param(
    [string]$InstallDirectory = (Join-Path $env:LOCALAPPDATA 'Programs\DesktopLauncher'),
    [string]$Python = 'python',
    [switch]$NoShortcuts
)
$ErrorActionPreference = 'Stop'
$programFiles = @('desktop_dock.pyw', 'windows_app.py', 'Prepare-Icons.ps1', 'atria-wordmark.ttf', 'atria-font-OFL.txt', 'atria-app.ico')
foreach ($name in $programFiles) {
    if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot $name) -PathType Leaf)) { throw ('Missing release file: ' + $name) }
}
$pythonOutput = @(& $Python -c 'import sys, tkinter; assert sys.version_info >= (3, 12); print(sys.executable)')
if ($LASTEXITCODE -ne 0 -or $pythonOutput.Count -ne 1) { throw 'Python 3.12 or newer, including Tcl/Tk, is required.' }
$pythonExe = $pythonOutput[0].Trim()
$pythonw = Join-Path (Split-Path -Parent $pythonExe) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonw -PathType Leaf)) { throw 'This Python installation has no pythonw.exe.' }
$destination = [IO.Path]::GetFullPath($InstallDirectory)
if ($destination.TrimEnd('\') -eq ([IO.Path]::GetFullPath($PSScriptRoot)).TrimEnd('\')) { throw 'Choose an installation directory outside this checkout.' }
$settings = Join-Path $env:APPDATA 'DesktopDock'
New-Item -ItemType Directory -Path $destination -Force | Out-Null
New-Item -ItemType Directory -Path $settings -Force | Out-Null
$existing = @($programFiles | Where-Object { Test-Path -LiteralPath (Join-Path $destination $_) })
if ($existing.Count -gt 0) {
    $backup = Join-Path $settings ('backups\program-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffffffZ'))
    New-Item -ItemType Directory -Path $backup | Out-Null
    foreach ($name in $existing) { Copy-Item -LiteralPath (Join-Path $destination $name) -Destination (Join-Path $backup $name) }
}
foreach ($name in $programFiles) { Copy-Item -LiteralPath (Join-Path $PSScriptRoot $name) -Destination (Join-Path $destination $name) -Force }
$configuration = Join-Path $settings 'config.json'
if (-not (Test-Path -LiteralPath $configuration)) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'config.example.json') -Destination $configuration
}
$brand = Join-Path $settings 'DesktopDock.ico'
if (Test-Path -LiteralPath $brand) {
    $brandBackup = Join-Path $settings ('backups\icon-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffffffZ') + '.ico')
    New-Item -ItemType Directory -Path (Split-Path -Parent $brandBackup) -Force | Out-Null
    Copy-Item -LiteralPath $brand -Destination $brandBackup
}
Copy-Item -LiteralPath (Join-Path $destination 'atria-app.ico') -Destination $brand -Force
& (Join-Path $destination 'Prepare-Icons.ps1')
if (-not $?) { throw 'Shortcut icon preparation failed.' }
$script = Join-Path $destination 'desktop_dock.pyw'
if (-not $NoShortcuts) {
    $programs = [Environment]::GetFolderPath('Programs')
    $shortcut = Join-Path $programs 'Desktop Launcher.lnk'
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut($shortcut)
    $link.TargetPath = $pythonw
    $link.Arguments = '"' + $script + '" --app --show'
    $link.WorkingDirectory = $destination
    $link.IconLocation = $brand + ',0'
    $link.Description = 'Desktop Launcher'
    $link.Save()
    & $pythonExe (Join-Path $destination 'windows_app.py') --shortcut $shortcut --script $script --icon $brand | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not configure the Start menu shortcut.' }
}
Write-Output ('Installed Desktop Launcher at ' + $destination)
Write-Output ('Your configuration is at ' + $configuration)
Write-Output ('Run: "' + $pythonw + '" "' + $script + '" --app')
Write-Output 'Startup is unchanged. Enable Start with Windows in Preferences if wanted.'
