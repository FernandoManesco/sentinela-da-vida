# ---------------------------------------------------------------------------
# nuvem/fix_nginx.py — ajuste pontual da configuração do nginx no T-140.
# Onde roda: servidor T-140, uma vez, com sudo (edita
#   /etc/nginx/sites-available/sentineladavida e deixa um backup .bak).
# O que faz: move o bloco 'location /app/' do server HTTP para o server HTTPS (listen 443)
#   e tira a barra final do proxy_pass, para que o prefixo /app chegue inteiro ao FastAPI
#   (nuvem/app.py usa APIRouter com prefix='/app').
# Conexões: complementa nginx-app.conf; depois rode 'sudo nginx -t && sudo systemctl reload nginx'.
# ---------------------------------------------------------------------------
"""Move o bloco location /app/ para o server HTTPS (listen 443) e tira a barra final do proxy_pass."""
import re, sys, shutil
# Lê o arquivo e guarda um backup antes de qualquer alteração.
f="/etc/nginx/sites-available/sentineladavida"; s=open(f).read(); shutil.copy(f, f+".bak")
# Localiza o bloco location /app/ junto com o redirect 'location = /app'.
m=re.search(r"\n[ \t]*location /app/ \{.*?\}\n[ \t]*location = /app \{[^\n]*\}\n", s, re.S)
if not m: print("bloco /app não encontrado"); sys.exit(1)
# Corrige o proxy_pass (sem barra final) e remove o bloco do lugar original.
block=m.group(0).replace("proxy_pass http://127.0.0.1:8090/;","proxy_pass http://127.0.0.1:8090;"); s=s.replace(m.group(0),"\n",1)
# Reinsere o bloco logo após 'listen 443 ssl;' e grava.
i=s.find("listen 443 ssl;"); i=s.find("\n",i)+1
s=s[:i]+block.lstrip("\n")+s[i:]; open(f,"w").write(s); print("ok — backup em", f+".bak")
