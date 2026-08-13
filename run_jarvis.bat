@echo off
REM ==========================================================================
REM  run_jarvis.bat  --  Launch Jarvis with the CORRECT Python 3.12 interpreter.
REM
REM  Why this exists: a bare "python" on this machine can resolve to Python 3.11,
REM  which does NOT have Jarvis's dependencies installed, so "python main.py"
REM  fails or runs broken. This launcher always uses 3.12. Never type bare
REM  "python" for this project again -- just run:  run_jarvis
REM
REM  Arguments pass straight through, e.g.:
REM      run_jarvis --check       (diagnose setup)
REM      run_jarvis --no-voice    (text-only)
REM ==========================================================================
setlocal
set "JARVIS_DIR=%~dp0"
set "PY312=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
cd /d "%JARVIS_DIR%"

if exist "%PY312%" (
    "%PY312%" main.py %*
    exit /b %ERRORLEVEL%
)

REM Fallback: the Python launcher, if it ever gets installed.
where py >nul 2>nul
if %ERRORLEVEL%==0 (
    py -3.12 main.py %*
    exit /b %ERRORLEVEL%
)

echo.
echo [Jarvis] Could not find Python 3.12.
echo         Looked for: "%PY312%"
echo         and the "py -3.12" launcher (also not found).
echo         Install Python 3.12 from https://python.org and run this again,
echo         or edit the PY312 line in run_jarvis.bat to point at your python.exe.
exit /b 1
