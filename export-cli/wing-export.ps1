$Entry = Join-Path $PSScriptRoot "backend/ziniao_exports.py"
if (-not (Test-Path $Entry)) { $Entry = Join-Path $PSScriptRoot "../backend/ziniao_exports.py" }
& python $Entry @args
exit $LASTEXITCODE
