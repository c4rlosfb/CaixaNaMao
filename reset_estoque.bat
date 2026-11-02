@echo off
chcp 65001 >nul
title CaixaNaMao - Reset de Estoque (AWS EC2)

echo ==========================================================
echo   🔄 CaixaNaMão — Reset de Estoque para Demonstração (AWS)
echo ==========================================================
echo.

set KEY_PATH=%USERPROFILE%\Desktop\caixanamao-key.pem
if not exist "%KEY_PATH%" set KEY_PATH=%~dp0caixanamao-key.pem

if not exist "%KEY_PATH%" (
    echo [ERRO] Chave SSH 'caixanamao-key.pem' não encontrada na Area de Trabalho.
    echo.
    pause
    exit /b 1
)

set EC2_IP=54.160.220.71
set EC2_USER=ubuntu

echo Conectando na AWS EC2 (%EC2_IP%) e restaurando saldos do estoque...
echo.

ssh -i "%KEY_PATH%" -o StrictHostKeyChecking=no %EC2_USER%@%EC2_IP% "cd ~/CaixaNaMao && bash scripts/reset_estoque.sh"

if %ERRORLEVEL% equ 0 (
    echo.
    echo ==========================================================
    echo   ✅ Sucesso! O estoque da AWS EC2 foi restaurado.
    echo   Abra a interface web e clique em 'Atualizar Estoque':
    echo   http://%EC2_IP%:8000/
    echo ==========================================================
) else (
    echo.
    echo [AVISO] Falha ao executar o reset na EC2.
    echo Verifique se a instancia esta ativa.
)

echo.
pause
