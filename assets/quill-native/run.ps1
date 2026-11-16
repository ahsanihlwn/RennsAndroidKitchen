$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
    throw "Install Rust stable and the MSVC C++ build tools first. See README.md."
}
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    py -3 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Could not create Python environment" }
}
$ready = & .venv\Scripts\python.exe -c "import importlib.util; print(int(importlib.util.find_spec('quill_native') is not None and __import__('quill_native').__version__ == '0.3.0'))"
if ($ready -ne "1") {
    & .venv\Scripts\python.exe -m pip install .
    if ($LASTEXITCODE -ne 0) { throw "Build failed; see output above" }
}
& .venv\Scripts\python.exe -m quill_native @args
exit $LASTEXITCODE
