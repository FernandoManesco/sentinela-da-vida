@echo off
rem =========================================================================
rem iniciar.bat - inicia o sistema Sentinela da Vida na casa com um duplo clique.
rem Onde roda: notebook da casa (Windows), na pasta C:\Users\ferna\websentinela.
rem O que faz: (1) esta janela sobe o webSentinela (run.py, HTTP 8080, escutando os
rem   nos ESP32 em UDP 5005); (2) 4 s depois abre o Monitor no navegador; (3) 6 s
rem   depois abre, minimizada, a janela do cloud_push.py, que envia o estado para a
rem   nuvem (T-140). Fechar esta janela derruba o servidor.
rem Requisitos: .venv criado com os pacotes de requirements.txt e banco criado pelo
rem   init_db.py. Comentarios sem acentos de proposito: o cmd.exe nao le UTF-8.
rem =========================================================================
title Sentinela da Vida - webSentinela
rem Garante a pasta do projeto como diretorio atual (funciona a partir de qualquer atalho).
cd /d C:\Users\ferna\websentinela

rem Mensagem de boas-vindas com os enderecos uteis.
echo ============================================
echo   Sentinela da Vida - subindo o webSentinela
echo   Painel local:   http://localhost:8080/painel
echo   Monitor local:  http://localhost:8080/monitor
echo   Web:            https://www.sentineladavida.com.br/app/
echo   Feche esta janela para parar o sistema.
echo ============================================
echo.

rem abre o monitor no navegador 4 segundos depois (tempo do servidor subir)
start "" cmd /c "timeout /t 4 /nobreak >nul & start http://localhost:8080/monitor"
rem envio para a nuvem (janela separada, minimizada)
start /min "Sentinela - envio para a nuvem" cmd /c "timeout /t 6 /nobreak >nul & .\.venv\Scripts\python cloud_push.py"

rem Servidor principal (fica em primeiro plano ate ser fechado).
.\.venv\Scripts\python run.py

rem So chega aqui quando o run.py termina (erro ou Ctrl+C): mostra aviso e espera uma tecla.
echo.
echo webSentinela encerrado.
pause
