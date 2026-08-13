@echo off
REM ==========================================================================
REM  run_kareem_simple.bat  --  Simple mode: Kareem opens in your browser and
REM  quits when you close that browser tab.
REM
REM  Double-click this (or the "Kareem" Desktop shortcut). A small window
REM  appears, the web UI opens automatically, and when you close the browser
REM  tab Kareem shuts itself down. This is the easy way to use Kareem for a
REM  single sitting.
REM
REM  Difference from the always-on modes:
REM    run_kareem.bat          -> text chat in this window (no auto-quit)
REM    run_kareem_silent.vbs   -> invisible always-on listener (never auto-quits)
REM    run_kareem_simple.bat   -> browser opens; close the tab to quit  <-- this
REM
REM  KAREEM_SIMPLE_MODE=1 is what turns on "close the tab to quit" (and the
REM  auto-open). --background = serve the web UI and stay alive without a
REM  console "You:" prompt, since the browser is the interface here. The
REM  interpreter selection (Python 3.12) is reused from run_kareem.bat.
REM ==========================================================================
setlocal
set "KAREEM_SIMPLE_MODE=1"
call "%~dp0run_kareem.bat" --background
exit /b %ERRORLEVEL%
