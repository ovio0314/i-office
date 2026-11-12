@echo off
chcp 65001 >NUL
cd /d "%~dp0"
where py >NUL 2>NUL
if %errorlevel%==0 (py ai_office.py) else (python ai_office.py)
pause
