# W2-C4: apply, verify or remove the Windows Firewall rules of DistriCom (thin wrapper; the logic is
# districom_ops.firewall). Run as administrator, on the real equipment from a Remote Desktop session that is
# already open. Only rules of the group "DistriCom" are ever created, changed or removed: never the rules of
# Store Sys, of Remote Desktop or of any other program, nor the firewall profiles. "apply" is idempotent;
# "remove" takes every DistriCom rule away. Exit codes: 0 ok, 1 problems found, 2 refused.
param(
    [ValidateSet('apply', 'verify', 'remove')][string]$Action = 'verify',
    [string]$Config = 'C:\DistriCom\config\districom.env'
)
$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $identity.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error 'firewall.ps1 must run as administrator'
    exit 2
}
$python = Join-Path $PSScriptRoot '..\.venv\Scripts\python.exe'
& $python -m districom_ops --config $Config firewall $Action
exit $LASTEXITCODE
