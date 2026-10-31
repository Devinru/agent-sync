# Portable manual entry. Uses the caller's existing Python and CODEX_HOME.
$bridgeScript = Join-Path $PSScriptRoot 'cli.py'
& python -X utf8 $bridgeScript @args
exit $LASTEXITCODE
