@echo off
setlocal
title WorkBuddy CLI Login
echo.
echo  ==========================================
echo   WorkBuddy CLI login  (full build 2.176.0)
echo  ==========================================
echo   1. Wait for the TUI to appear
echo   2. Type:  /login    then press Enter
echo   3. Browser opens - sign in and approve
echo   4. Back here, type:  /exit
echo  ==========================================
echo.
cd /d "%TEMP%"
set "NODE=C:\Users\Lianaxber\.workbuddy\binaries\node\versions\22.22.2-6\node.exe"
set "CLI=C:\Users\Lianaxber\WorkBuddy\2026-10-26-16-43-26\agentmesh\tools\node_modules\@tencent-ai\codebuddy-code\bin\codebuddy"

if exist "%NODE%" goto havenode
  for %%I in (node.exe) do set "NODE=%%~$PATH:I"
:havenode
if not exist "%NODE%" (
  echo [ERROR] node.exe not found.
  pause
  exit /b 1
)
if not exist "%CLI%" (
  echo [ERROR] codebuddy not found:
  echo         %CLI%
  echo   Run:  cd agentmesh\tools  ^&^&  npm i @tencent-ai/codebuddy-code
  pause
  exit /b 1
)

echo  node: %NODE%
echo  cli : %CLI%
echo.
echo  ---- launch ----
"%NODE%" "%CLI%" --port 0
set "RC=%ERRORLEVEL%"
echo.
echo  ---- exited (code %RC%) ----
pause
