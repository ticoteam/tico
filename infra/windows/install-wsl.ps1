<#
Sets this Windows PC up as a Tico computer: WSL 2 and Ubuntu if they are missing, then the Linux runner
(scripts/install.sh --runner) inside Ubuntu. Bots run in Docker inside WSL, exactly as on a Linux server.

Run it in PowerShell as administrator, with the line Settings > Computers > Add computer > Windows shows:

  & ([scriptblock]::Create((irm https://github.com/ticoteam/tico/releases/download/vX.Y.Z/install-wsl.ps1))) `
    -Url https://tico.example.com -Code <code> -Label 'Build PC'

What it changes on Windows: installs WSL and the Ubuntu distribution when they are missing (the first time can need a
restart), turns on systemd in that distribution (/etc/wsl.conf), adds a logon task that keeps the distribution running
(WSL stops an idle distribution, and the runner with it), and turns off sleep on mains power unless -AllowSleep.
Bots run while this Windows user is signed in. Safe to run again. A problem stops it with one red line that says what
to do; it never closes the PowerShell window.
#>
param(
  [Parameter(Mandatory = $true)][string]$Url,
  [Parameter(Mandatory = $true)][string]$Code,
  [string]$Label = $env:COMPUTERNAME,
  [string]$Name = '',
  [string]$Distro = 'Ubuntu',
  [string]$Version = '',
  [switch]$AllowSleep
)

$ErrorActionPreference = 'Stop'
# The release workflow replaces the placeholder with the tag (scripts/build_install_bundle.py); from a checkout it stays,
# and the newest release's installer is used.
$Baked = '@TICO_VERSION@'
$VersionPattern = '^v[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.]+)?$'
$TaskName = 'Tico runner keep-alive'
# wsl.exe writes UTF-16 unless told otherwise, which PowerShell would read with a NUL between letters.
$env:WSL_UTF8 = '1'

function Say([string]$Text) { Write-Host $Text }
function Step([string]$Text) { Write-Host ''; Write-Host "==> $Text" }
# `exit` would close the window the person pasted into (the script runs as a script block), so a stop is an error.
function Die([string]$Text) { throw $Text }

# A value inside single quotes for sh: the only character to handle is the quote itself.
function ShQuote([string]$Value) { "'" + ($Value -replace "'", "'\''") + "'" }

# Runs a sh script as root in the distribution. The script goes in on standard input, so nothing in it passes through
# Windows command-line quoting; carriage returns PowerShell adds are dropped before sh reads it.
function Invoke-Wsl([string]$Script) {
  $OutputEncoding = New-Object System.Text.UTF8Encoding $false
  ($Script -replace "`r", '') + "`n# end`n" | & wsl.exe -d $Distro -u root --exec sh -c "tr -d '\r' | sh -s" | Out-Host
  return $LASTEXITCODE
}

# Windows PowerShell 5.1 turns a native command's redirected stderr into errors, which 'Stop' would make fatal; wsl.exe
# writes its "no distribution" messages there. Native calls whose stderr is redirected go through these two.
function Get-WslText([string[]]$Arguments) {
  $ErrorActionPreference = 'Continue'
  $text = & wsl.exe @Arguments 2>&1 | Out-String
  return ($text -replace "`0", '')
}

function Invoke-WslQuiet([string[]]$Arguments) {
  $ErrorActionPreference = 'Continue'
  & wsl.exe @Arguments *> $null
  return $LASTEXITCODE
}

function Test-Distro {
  $names = (Get-WslText @('--list', '--quiet')) -split "`r?`n" | ForEach-Object { $_.Trim() }
  return $names -contains $Distro
}

# ------------------------------------------------------------------------------------------- checks

if ($Url -notmatch '^https?://[A-Za-z0-9.-]+(:[0-9]+)?$') { Die '-Url is your Tico server, such as https://tico.example.com (no path).' }
if ($Code -notmatch '^[A-Za-z0-9_-]+$') { Die '-Code is the one-time code from Settings > Computers > Add computer.' }
if ($Label -notmatch '^[^"$`\\]{1,100}$') { Die '-Label is 1 to 100 characters without quotes, dollar signs, backticks or backslashes.' }
if ($Name -and $Name -notmatch '^[a-z0-9]([a-z0-9-]*[a-z0-9])?$') { Die '-Name needs lowercase letters, digits or dashes, such as -Name build.' }
if ($Distro -notmatch '^[A-Za-z0-9._-]+$') { Die '-Distro is a WSL distribution name, such as Ubuntu.' }
if (-not $Version) { $Version = if ($Baked -match $VersionPattern) { $Baked } else { '' } }
if ($Version -and $Version -notmatch $VersionPattern) { Die '-Version is a release tag, such as v1.2.3.' }
# The floor (backend/release_floor.py): v0.6.0 or later by semver, before anything is downloaded; v0.6.0-rc.1 is below it.
if ($Version -match '^v0\.([0-9]+)\.([0-9]+)(-.*)?$' -and
    ([int]$Matches[1] -lt 6 -or ([int]$Matches[1] -eq 6 -and [int]$Matches[2] -eq 0 -and $Matches[3]))) {
  Die "$Version is older than v0.6.0, and no release before v0.6.0 is supported. Pass -Version v0.6.0 or later."
}
$Installer = if ($Version) { "https://github.com/ticoteam/tico/releases/download/$Version/install.sh" }
             else { 'https://github.com/ticoteam/tico/releases/latest/download/install.sh' }

Step 'Checking this PC'
$principal = New-Object Security.Principal.WindowsPrincipal ([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
  Die 'Run this in PowerShell as administrator (right-click PowerShell, Run as administrator).'
}
if ([Environment]::OSVersion.Version.Build -lt 19041) {
  Die 'WSL 2 needs Windows 10 version 2004 (build 19041) or later, or Windows 11. Update Windows and run this again.'
}
if (-not [Environment]::Is64BitOperatingSystem) { Die 'Tico needs 64-bit Windows.' }
Say "ok: Windows build $([Environment]::OSVersion.Version.Build), signed in as $env:USERNAME"

# ------------------------------------------------------------------------------------------- WSL and Ubuntu

Step "WSL 2 and $Distro"
$wsl = Get-Command wsl.exe -ErrorAction SilentlyContinue
$ready = $false
if ($wsl) {
  $ready = ((Invoke-WslQuiet @('--status')) -eq 0) -and (Test-Distro)
}
if (-not $ready) {
  Say "Installing WSL and $Distro (a few minutes) ..."
  & wsl.exe --install -d $Distro --no-launch
  if ($LASTEXITCODE -ne 0) {
    Die "WSL asks for a restart before it can finish. Restart Windows, then get a new command from Settings > Computers > Add computer (codes last 15 minutes) and run it again."
  }
  Invoke-WslQuiet @('--set-default-version', '2') | Out-Null
}
# The runner needs no Linux user of its own: it runs as root in the distribution and as its own users in Docker. So the
# first start must not stop at Ubuntu's "create a default Unix user" prompt, which waits for typing and would hang here.
# - An older store install registers the distribution on its first start; its launcher's `install --root` registers it
#   with root as the default user and asks nothing.
# - A current `wsl --install` registers a tar-based distribution at once, and runs its first-start setup (the user
#   prompt) the first time a shell opens. WSL marks that pending with RunOOBE on the distribution's Lxss key; clearing
#   it before the first start skips the prompt, and every start below is `-u root --exec`, never a login shell.
function Skip-FirstStartPrompt {
  $lxss = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss'
  if (-not (Test-Path $lxss)) { return }
  Get-ChildItem $lxss -ErrorAction SilentlyContinue | ForEach-Object {
    $key = Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue
    if ($key -and $key.DistributionName -eq $Distro -and $null -ne $key.RunOOBE -and $key.RunOOBE -ne 0) {
      Set-ItemProperty $_.PSPath -Name RunOOBE -Value 0
      Say "ok: skipped $Distro's first-start user prompt (the runner runs as root)"
    }
  }
}
if (-not (Test-Distro)) {
  $launcher = Get-Command (($Distro -replace '[^A-Za-z0-9]', '').ToLower() + '.exe') -ErrorAction SilentlyContinue
  if ($launcher) { & $launcher.Source install --root }
}
Skip-FirstStartPrompt
if ((Invoke-WslQuiet @('-d', $Distro, '-u', 'root', '--exec', 'true')) -ne 0) {
  Die "$Distro does not start yet. Restart Windows, then get a new command from Settings > Computers > Add computer and run it again."
}
Say "ok: $Distro is installed"

# Docker needs WSL 2; a distribution made under WSL 1 is converted once.
$kernel = Get-WslText @('-d', $Distro, '-u', 'root', '--exec', 'uname', '-r')
if ($kernel -notmatch 'WSL2|microsoft-standard') {
  Say "Converting $Distro to WSL 2 (a few minutes) ..."
  & wsl.exe --set-version $Distro 2
  if ($LASTEXITCODE -ne 0) { Die "Could not convert $Distro to WSL 2. Run: wsl --set-version $Distro 2" }
}

# Docker and the runner's updater are systemd services inside the distribution.
$init = (Get-WslText @('-d', $Distro, '-u', 'root', '--exec', 'cat', '/proc/1/comm')).Trim()
if ($init -ne 'systemd') {
  Say "Turning on systemd in $Distro ..."
  $status = Invoke-Wsl @'
set -e
touch /etc/wsl.conf
sed -i '/^[[:space:]]*systemd[[:space:]]*=/d' /etc/wsl.conf
if grep -q '^\[boot\]' /etc/wsl.conf; then sed -i '/^\[boot\]/a systemd=true' /etc/wsl.conf; else printf '\n[boot]\nsystemd=true\n' >> /etc/wsl.conf; fi
'@
  if ($status -ne 0) { Die "Could not write /etc/wsl.conf in $Distro." }
  Invoke-WslQuiet @('--terminate', $Distro) | Out-Null
  Start-Sleep -Seconds 8
  $init = (Get-WslText @('-d', $Distro, '-u', 'root', '--exec', 'cat', '/proc/1/comm')).Trim()
  if ($init -ne 'systemd') { Die "systemd did not start in $Distro. Update WSL (wsl --update), restart Windows, and run this again." }
}
Say 'ok: systemd is running'

# ------------------------------------------------------------------------------------------- keep it running

Step 'Keeping the computer running'
# WSL stops a distribution a little after its last Windows process exits, and Docker with it. One idle process started
# at logon holds it open.
$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
  -Argument "-NoProfile -WindowStyle Hidden -Command wsl.exe -d $Distro -u root --exec sleep infinity"
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -Hidden
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Say "ok: the '$TaskName' task keeps $Distro running while you are signed in (to undo: Unregister-ScheduledTask '$TaskName')"
if (-not $AllowSleep) {
  & powercfg.exe /change standby-timeout-ac 0
  Say 'ok: this PC no longer sleeps on mains power (to undo: powercfg /change standby-timeout-ac 30; -AllowSleep skips this)'
}

# ------------------------------------------------------------------------------------------- the runner

Step 'Installing the runner in WSL'
$flags = "--runner --url $(ShQuote $Url) --code $(ShQuote $Code) --label $(ShQuote $Label)"
if ($Name) { $flags += " --name $(ShQuote $Name)" }
$status = Invoke-Wsl @"
set -e
# Nothing here may read standard input: it is the rest of this script.
command -v curl >/dev/null 2>&1 || { apt-get update -qq </dev/null && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq curl ca-certificates </dev/null; }
curl -fsSL $(ShQuote $Installer) -o /tmp/tico-install.sh
sh /tmp/tico-install.sh $flags </dev/null
rm -f /tmp/tico-install.sh
"@
if ($status -ne 0) { Die "The Linux installer stopped (exit $status). Its message is above; fix that and run this again with a new code." }

Step 'Done'
Say "$Label shows in Settings > Computers within a minute. Sign it in to Claude Code or Codex there."
