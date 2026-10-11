@echo off
setlocal
set "PYTHONPATH=%~dp0;%PYTHONPATH%"
python "%~dp0app.py" --rak-cli %*
exit /b %ERRORLEVEL%
