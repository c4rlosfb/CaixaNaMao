@echo off
chcp 65001 >nul
title CaixaNaMao - Reset de Estoque (AWS EC2)

echo ==========================================================
echo   CaixaNaMao - Reset de Estoque para Demonstracao (AWS)
echo ==========================================================
echo.

set "KEY_PATH=%USERPROFILE%\Desktop\caixanamao-key.pem"
if not exist "%KEY_PATH%" set "KEY_PATH=%~dp0..\caixanamao-key.pem"
if not exist "%KEY_PATH%" set "KEY_PATH=caixanamao-key.pem"

if not exist "%KEY_PATH%" (
    echo [ERRO] Chave SSH 'caixanamao-key.pem' nao encontrada.
    echo.
    pause
    exit /b 1
)

set "EC2_IP=%~1"
if "%EC2_IP%"=="" set "EC2_IP=caixanamao.duckdns.org"
set "EC2_USER=ubuntu"

echo Conectando na AWS EC2 (%EC2_IP%) e restaurando saldos do estoque...
echo.

ssh -i "%KEY_PATH%" -o StrictHostKeyChecking=no %EC2_USER%@%EC2_IP% "cd ~/CaixaNaMao && bash scripts/reset_estoque.sh"

echo.
pause
