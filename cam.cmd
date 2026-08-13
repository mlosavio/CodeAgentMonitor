@echo off
rem Wrapper: lancia CodeAgentMonitor da qualunque cartella.
rem Aggiungi questa cartella al PATH per usarlo come "cam ...".
python "%~dp0cam.py" %*
