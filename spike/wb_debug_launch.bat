@echo off
setlocal
title WorkBuddy debug relaunch (port 9444)
echo.
echo  ====================================================
echo   Restart WorkBuddy with remote debugging on 9444
echo  ====================================================
echo   * The current WorkBuddy window WILL CLOSE.
echo   * Chat history is kept - reopen WorkBuddy after.
echo  ====================================================
echo.
pause

echo [1/3] stopping WorkBuddy ...
taskkill /IM WorkBuddy.exe /F >nul 2>&1
timeout /t 4 /nobreak >nul

echo [2/3] launching with --remote-debugging-port=9444 ...
start "" "D:\chatAI\AI agent\WorkBuddy\WorkBuddy.exe" --remote-debugging-port=9444
timeout /t 15 /nobreak >nul

echo [3/3] checking port 9444 ...
curl -s -m 6 --noproxy "*" -o nul -w "HTTP %{http_code}\n" http://127.0.0.1:9444/json/version
if errorlevel 1 echo   -> NOT READY (debug port did not open)
echo.
echo  If you see HTTP 200: reopen WorkBuddy, go back to the chat, tell the agent.
echo  If NOT READY: the app ignored the flag; report it back.
pause
