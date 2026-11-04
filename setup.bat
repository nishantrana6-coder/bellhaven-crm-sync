@echo off
cd /d "%~dp0"
python -m venv .venv || (echo Python 3.10+ is required: https://www.python.org/downloads/ & exit /b 1)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
echo.
echo Setup done. Next: run_pipeline.bat  then  run_app.bat  and open http://127.0.0.1:5000
