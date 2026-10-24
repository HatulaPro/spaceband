@echo off
rem spaceband: download ListenBrainz similarity data, build the map, open the explorer.
rem Safe to re-run: the download resumes where it stopped and skips what it already has.
cd /d "%~dp0"

if not exist .venv (
  echo Creating Python environment...
  python -m venv .venv || goto :nopython
)
call .venv\Scripts\activate.bat
python -m pip install -q -r requirements.txt || goto :error

python pipeline\crawl.py %* || goto :error
python pipeline\build.py || goto :error
python serve.py
goto :eof

:nopython
echo Python 3.10+ is needed: https://www.python.org/downloads/  (tick "Add python.exe to PATH")
pause
goto :eof

:error
echo Something failed above. Fix it and run this again; finished downloads are kept.
pause
