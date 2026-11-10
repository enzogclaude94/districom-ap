# W2-C3 access probe: run under the identity being tested (a service account, another service, a local user).
# For each check it tries the operation for real and records whether Windows allowed it. It never changes the
# content of an existing file: "write" opens it for writing and closes it; "create" makes and removes a new file.
param([Parameter(Mandatory)][string]$Checks, [Parameter(Mandatory)][string]$Out)
$ErrorActionPreference = 'Stop'
$results = [ordered]@{ identity = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value }
foreach ($check in (Get-Content -Raw -LiteralPath $Checks | ConvertFrom-Json)) {
    $outcome = 'allowed'
    try {
        switch ($check.op) {
            'read' {
                if ([IO.Directory]::Exists($check.path)) { [void][IO.Directory]::GetFileSystemEntries($check.path) }
                else { [IO.File]::Open($check.path, 'Open', 'Read', 'ReadWrite').Close() }
            }
            'attributes' { [void][IO.File]::GetAttributes($check.path) }  # what Node.js and the volume checks need
            'write' { [IO.File]::Open($check.path, 'Open', 'Write', 'ReadWrite').Close() }
            'create' {
                $file = Join-Path $check.path ("c3-probe-" + [guid]::NewGuid().ToString('N') + '.tmp')
                [IO.File]::WriteAllText($file, 'probe')
                try { [IO.File]::Delete($file) } catch { }
            }
            default { $outcome = 'unknown_op' }
        }
    } catch [UnauthorizedAccessException] {
        $outcome = 'denied'
    } catch [IO.FileNotFoundException], [IO.DirectoryNotFoundException] {
        $outcome = 'missing'
    } catch {
        $outcome = 'error: ' + $_.Exception.GetType().Name
    }
    $results[$check.id] = $outcome
}
[IO.File]::WriteAllText($Out, ($results | ConvertTo-Json -Compress))
