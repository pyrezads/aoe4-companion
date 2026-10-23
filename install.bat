@echo off
cd /d "%~dp0"
echo Installing the analyst's libraries...
python -m pip install -r requirements.txt
echo.
echo Checking the screen reader on your screenshots...
python main.py --test calib\s02.png calib\s07.png
echo.
echo Done. Start the analyst with start.bat
pause
