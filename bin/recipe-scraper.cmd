@echo off
rem Windows shim for PowerShell and cmd; prefers the py launcher, falls back to python.
setlocal
set "LAUNCH=%~dp0..\scripts\launch.py"
py -3 -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>nul
if %ERRORLEVEL%==0 (
    py -3 "%LAUNCH%" %*
) else (
    python "%LAUNCH%" %*
)
exit /b %ERRORLEVEL%
