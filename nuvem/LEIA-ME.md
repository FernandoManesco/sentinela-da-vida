# Sentinela da Vida — nuvem (T-140) + casa

## T-140 (ssh deploy@...)
    sudo mkdir -p /opt/sentinela-cloud && sudo chown deploy /opt/sentinela-cloud
    cd /opt/sentinela-cloud && unzip -o /tmp/sentinela-cloud.zip
    python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
    .venv/bin/python app.py adduser fernando SUA_SENHA "Fernando"
    .venv/bin/python app.py token            # guarde: vai no cloud.json do notebook
    sudo cp sentinela-cloud.service /etc/systemd/system/ && sudo systemctl daemon-reload
    sudo systemctl enable --now sentinela-cloud && systemctl status sentinela-cloud --no-pager
    # nginx: cole nginx-app.conf dentro do server HTTPS de sites-available/sentineladavida
    sudo nginx -t && sudo systemctl reload nginx
    cp index.html /var/www/sentineladavida/html/index.html

## Notebook (PowerShell)
    copy casa\monitor.html  C:\Users\ferna\websentinela\app\static\monitor.html
    copy casa\cloud_push.py C:\Users\ferna\websentinela\cloud_push.py
    copy casa\iniciar.bat   C:\Users\ferna\websentinela\iniciar.bat
    notepad C:\Users\ferna\websentinela\cloud.json   # (criado na 1a execução) -> token
    cd C:\Users\ferna\websentinela ; .\iniciar.bat

## URLs
    https://www.sentineladavida.com.br/app/         login
    https://www.sentineladavida.com.br/app/monitor  Sentinela Monitor
    https://www.sentineladavida.com.br/app/painel   Painel dos dispositivos
