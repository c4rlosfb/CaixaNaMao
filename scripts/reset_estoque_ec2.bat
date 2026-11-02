@echo off
chcp 65001 >nul
title CaixaNaMao - Reset de Estoque (AWS EC2)

echo ==========================================================
echo   🔄 CaixaNaMão — Reset de Estoque para Demonstração (AWS)
echo ==========================================================
echo.

set KEY_PATH=%USERPROFILE%\Desktop\caixanamao-key.pem
if not exist "%KEY_PATH%" set KEY_PATH=%~dp0caixanamao-key.pem
if not exist "%KEY_PATH%" set KEY_PATH=caixanamao-key.pem

if not exist "%KEY_PATH%" (
    echo [ERRO] Chave SSH não encontrada em:
    echo   %USERPROFILE%\Desktop\caixanamao-key.pem
    echo Por favor, certifique-se de que o arquivo 'caixanamao-key.pem' esta na sua Area de Trabalho.
    echo.
    pause
    exit /b 1
)

set EC2_IP=54.160.220.71
set EC2_USER=ubuntu

echo [1/2] Conectando na AWS EC2 (%EC2_IP%) via SSH...
echo.

ssh -i "%KEY_PATH%" -o StrictHostKeyChecking=no %EC2_USER%@%EC2_IP% "cd ~/CaixaNaMao && bash scripts/reset_estoque.sh"

if %ERRORLEVEL% equ 0 (
    echo.
    echo ==========================================================
    echo   ✅ Sucesso! O estoque da AWS EC2 foi resetado.
    echo   Abra a interface web e clique em 'Atualizar Estoque':
    echo   http://%EC2_IP%:8000/
    echo ==========================================================
) else (
    echo.
    echo [AVISO] Ocorreu uma falha ao conectar ou executar na EC2.
    echo Verifique se a instancia esta ligada e se o IP '%EC2_IP%' esta correto.
)

echo.
pause
