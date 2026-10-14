# ---------------------------------------------------------------------------
# cloud_push.py — ponte entre a casa e a nuvem.
# Onde roda: notebook da casa, em janela própria aberta pelo iniciar.bat (ou à mão:
#   python cloud_push.py).
# O que faz: a cada 2 s lê http://localhost:8080/api/state (webSentinela, app/main.py)
#   e faz POST do mesmo JSON para https://www.sentineladavida.com.br/app/api/state
#   (nuvem/app.py no T-140), autenticado pelo header X-Sentinela-Token.
# Conexões: cloud.json (mesma pasta, fora do git) guarda url, token e nome da casa;
#   o token é gerado no T-140 com 'python app.py token'.
# ---------------------------------------------------------------------------
"""
cloud_push.py — roda no notebook da casa, ao lado do webSentinela.
Lê http://localhost:8080/api/state (o mesmo JSON do Monitor local) e envia para a nuvem
a cada 2 s:  POST https://www.sentineladavida.com.br/app/api/state  (header X-Sentinela-Token)

Configuração em cloud.json (mesma pasta):
  {"url": "https://www.sentineladavida.com.br/app/api/state", "token": "COLE_O_TOKEN", "home": "casa-fernando"}
"""
import json, time, urllib.request, pathlib, sys

# Caminhos e constantes: arquivo de configuração, URL local do webSentinela e
# intervalo entre envios (segundos).
CFG = pathlib.Path(__file__).with_name("cloud.json")
LOCAL = "http://localhost:8080/api/state"
INTERVAL = 2.0

# Lê cloud.json; se não existir, cria um modelo com token vazio e encerra para
# o usuário preencher (nunca envia dados sem token).
def load_cfg():
    if not CFG.exists():
        CFG.write_text(json.dumps({"url": "https://www.sentineladavida.com.br/app/api/state", "token": "COLE_O_TOKEN", "home": "casa-fernando"}, indent=2))
        print("criei cloud.json — edite e coloque o token (python app.py token no T-140)"); sys.exit(1)
    return json.loads(CFG.read_text())

# Laço infinito: lê o estado local, monta o corpo {home, company, nodes} e envia
# à nuvem. Erros são contados e impressos só quando a mensagem muda (sem spam).
def main():
    cfg = load_cfg(); ok = fail = 0; last_err = ""
    print(f"[cloud_push] {LOCAL} -> {cfg['url']}  (Ctrl+C para parar)")
    # Cada volta = uma leitura local + um POST na nuvem; Ctrl+C encerra.
    while True:
        try:
            with urllib.request.urlopen(LOCAL, timeout=3) as r: st = json.loads(r.read())
            body = json.dumps({"home": cfg.get("home", ""), "company": st.get("company", False), "nodes": st.get("nodes", {})}).encode()
            req = urllib.request.Request(cfg["url"], data=body, method="POST",
                    headers={"Content-Type": "application/json", "X-Sentinela-Token": cfg["token"]})
            with urllib.request.urlopen(req, timeout=5) as r: r.read()
            ok += 1
            if ok % 30 == 1: print(time.strftime("%H:%M:%S"), f"enviado ok={ok} falhas={fail}")
        except Exception as e:
            fail += 1
            if str(e) != last_err: print(time.strftime("%H:%M:%S"), "falha:", e); last_err = str(e)
        time.sleep(INTERVAL)

# Executado só quando chamado diretamente.
if __name__ == "__main__":
    main()
