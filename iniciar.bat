@echo off
title SpotyOFO
cd /d "%~dp0"
powershell.exe -NoExit -NoProfile -ExecutionPolicy Bypass -Command "Set-Location -LiteralPath (Get-Location).Path; if (-not (Get-Command python -ErrorAction SilentlyContinue)) { Write-Host 'No encuentro Python en el PATH.'; exit 1 }; if (-not (Test-Path -LiteralPath '.env')) { Write-Host 'Falta el archivo .env en esta carpeta.'; exit 1 }; Write-Host 'Iniciando SpotyOFO en http://127.0.0.1:5050'; python app.py"
