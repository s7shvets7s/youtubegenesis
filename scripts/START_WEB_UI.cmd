@echo off
cd /d "%~dp0.."
set "PYTHONPATH=%CD%\src"
"%CD%\.venv\Scripts\python.exe" -m ru_en_dubber.web.server --open
pause
