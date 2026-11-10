# W2-C3: apply or verify the NTFS permissions of DistriCom (thin wrapper; the logic is districom_ops.permissions).
# Run as administrator. "apply" is idempotent and refuses, without changing anything, while a link below the
# installation points outside it. "verify" only reads. Exit codes: 0 ok, 1 problems found, 2 refused.
param(
    [ValidateSet('apply', 'verify')][string]$Action = 'verify',
    [string]$Config = 'C:\DistriCom\config\districom.env'
)
$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $identity.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error 'acl.ps1 must run as administrator'
    exit 2
}
$python = Join-Path $PSScriptRoot '..\.venv\Scripts\python.exe'
& $python -m districom_ops --config $Config permissions $Action
exit $LASTEXITCODE
