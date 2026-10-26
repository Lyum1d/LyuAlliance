@echo off
chcp 65001 >nul
title AgentMesh Launcher
cd /d "%~dp0"

echo [1/2] 启动后端 :8787 ...
start "AgentMesh backend" cmd /k "cd /d %~dp0backend && .venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8787"

echo [2/2] 启动前端 :5173 ...
start "AgentMesh frontend" cmd /k "cd /d %~dp0frontend && npm run dev"

echo 等待服务就绪 ...
timeout /t 6 >nul
start http://localhost:5173

echo.
echo 后端 http://127.0.0.1:8787/api/health
echo 前端 http://localhost:5173
echo 关闭这两个黑窗口即可停止服务。
pause
