@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

set "VENV_PY=%~dp0.venv\Scripts\python.exe"

if exist "%VENV_PY%" goto venv_ready

where py >nul 2>&1
if errorlevel 1 goto use_python_command
py -3 -m venv "%~dp0.venv"
if errorlevel 1 goto setup_failed
goto venv_ready

:use_python_command
where python >nul 2>&1
if errorlevel 1 goto python_missing
python -m venv "%~dp0.venv"
if errorlevel 1 goto setup_failed

:venv_ready
"%VENV_PY%" -c "import rich, textual" >nul 2>&1
if errorlevel 1 goto install_dependencies
goto launch_app

:install_dependencies
echo Installing Title Search dependencies. This happens only when needed...
"%VENV_PY%" -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 goto dependency_install_failed

:launch_app
echo Starting Title Search...
"%VENV_PY%" "%~dp0title_search_tui.py"
set "APP_EXIT=%ERRORLEVEL%"
if not "%APP_EXIT%"=="0" echo Title Search exited with code %APP_EXIT%.
echo.
pause
exit /b %APP_EXIT%

:python_missing
echo Python 3.10 or newer was not found. Install Python, then double-click this file again.
echo Download Python from https://www.python.org/downloads/windows/
pause
exit /b 1

:setup_failed
echo Could not create the Python environment. Check that Python 3.10 or newer is installed.
pause
exit /b 1

:dependency_install_failed
echo Dependency installation failed. Check your internet connection and try again.
pause
exit /b 1
