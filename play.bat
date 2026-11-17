@echo off
rem Starts the Statecraft command-line prototype. Extra arguments pass through, e.g. play.bat --file test.json
cd /d "%~dp0"
py -m statecraft %*
