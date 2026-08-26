@echo off
chcp 936 >nul
setlocal
cd /d "%~dp0"

rem 便携版：所有运行环境都在本文件夹内，不依赖系统 Python
set "PLAYWRIGHT_BROWSERS_PATH=%~dp0runtime\ms-playwright"
set "PY=%~dp0runtime\python\python.exe"

if not exist "%PY%" (
  echo [ERROR] 未找到便携 Python（runtime\python\python.exe）。
  echo 请确认你解压的是完整文件夹，不要只拷贝部分文件。
  pause
  exit /b 1
)

start "" "http://127.0.0.1:6161/"
"%PY%" -X utf8 -m web.server
pause
