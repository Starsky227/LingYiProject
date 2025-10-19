@echo off
chcp 65001 >nul
title TestAI Mode
cd /d %~dp0
call .venv\Scripts\activate
rem ----------------------------------------------
python main.py
pause