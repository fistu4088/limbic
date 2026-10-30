$ErrorActionPreference = 'Stop'
$repo = 'https://github.com/fistu4088/limbic.git'
$work = Join-Path $env:TEMP ('limbic-install-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work | Out-Null

try {
    $source = Join-Path $work 'limbic'
    $git = Get-Command git -ErrorAction SilentlyContinue
    if ($git) {
        Write-Host 'Downloading limbic with Git…'
        & $git.Source clone --depth 1 --quiet $repo $source
        if ($LASTEXITCODE -ne 0) { throw 'Git clone failed.' }
    }
    else {
        Write-Host 'Git was not found; downloading the source archive…'
        $zip = Join-Path $work 'limbic.zip'
        Invoke-WebRequest -Uri 'https://github.com/fistu4088/limbic/archive/refs/heads/main.zip' -OutFile $zip
        Expand-Archive -Path $zip -DestinationPath $work
        $source = Join-Path $work 'limbic-main'
    }

    Push-Location $source
    try {
        if (Get-Command py -ErrorAction SilentlyContinue) {
            & py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
            if ($LASTEXITCODE -ne 0) { throw 'Python 3.10+ is required. Upgrade Python and run this again.' }
            & py -3 -m limbic.installer --source $source
        }
        elseif (Get-Command python -ErrorAction SilentlyContinue) {
            & python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
            if ($LASTEXITCODE -ne 0) { throw 'Python 3.10+ is required. Upgrade Python and run this again.' }
            & python -m limbic.installer --source $source
        }
        else {
            throw 'Python 3.10+ is required. Install Python from python.org and run this again.'
        }
        if ($LASTEXITCODE -ne 0) { throw 'The limbic installer did not complete successfully.' }
    }
    finally {
        Pop-Location
    }
}
finally {
    Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
}
