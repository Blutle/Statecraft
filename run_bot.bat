@echo off
rem Starts the Statecraft Discord bot (reads settings from .env)
cd /d "%~dp0"
py -m statecraft.discord_bot
