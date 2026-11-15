@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 goto use_python
py -3 main.py --no-pause
goto finished
:use_python
where python >nul 2>nul
if errorlevel 1 goto missing_python
python main.py --no-pause
goto finished
:missing_python
echo Python was not found. Install Python 3.8 or later.
:finished
if not errorlevel 1 goto done
echo.
echo Review the results above. Press any key to close this window.
pause >nul
:done
endlocal
