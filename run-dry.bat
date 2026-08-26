@echo off
chcp 936 >nul
setlocal
cd /d "%~dp0"

set "PLAYWRIGHT_BROWSERS_PATH=%~dp0runtime\ms-playwright"
set "PY=%~dp0runtime\python\python.exe"

if not exist "%PY%" (
  echo [ERROR] 未找到便携 Python，请重新解压完整文件夹。
  pause
  exit /b 1
)

"%PY%" run.py --dry-run
pause
