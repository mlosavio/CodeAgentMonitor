@echo off
rem Wrapper: lancia claude-monitor da qualunque cartella.
rem Aggiungi questa cartella al PATH per usarlo come "claude-monitor ...".
python "%~dp0claude_monitor.py" %*
