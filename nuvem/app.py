# ---------------------------------------------------------------------------
# nuvem/app.py — aplicação FastAPI da nuvem: login, Sentinela Monitor, Painel e API de estado.
# Onde roda: servidor T-140 (/opt/sentinela-cloud), como serviço systemd
#   (sentinela-cloud.service) em 127.0.0.1:8090, atrás do nginx em
#   https://www.sentineladavida.com.br/app/ (nginx-app.conf).
# Conexões: recebe POST /app/api/state do cloud_push.py (notebook da casa) com o header
#   X-Sentinela-Token; serve GET /app/api/state e /app/api/rooms ao navegador da família
#   (static/monitor.html e templates/painel.html). Dados em data/ (cloud.db de usuários,
#   state.json, rooms.json, secret.key, push.token), fora do git.
# Linha de comando: python app.py adduser <usuario> <senha> [nome]  |  python app.py token
# ---------------------------------------------------------------------------
"""
Sentinela da Vida — nuvem (T-140)
Login + Sentinela Monitor + API de estado.
A casa (webSentinela) envia o estado dos nós via POST /api/state com token;
o Monitor (navegador da família) lê via GET /api/state.
"""
import os, json, time, hmac, hashlib, secrets, sqlite3, threading
from pathlib import Path
from fastapi import FastAPI, APIRouter, Request, Form, Header, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

# Pastas e arquivos de dados (data/ é criada na hora e fica fora do git).
BASE = Path(__file__).parent
DATA = BASE / "data"; DATA.mkdir(exist_ok=True)
DB = DATA / "cloud.db"
STATE_FILE = DATA / "state.json"
ROOMS_FILE = DATA / "rooms.json"

# Segredo do cookie de sessão e token de envio da casa: ambiente > arquivo em data/ >
# gerado na primeira execução (e gravado para sobreviver a reinícios).
SECRET = os.environ.get("SENTINELA_SECRET") or ((DATA / "secret.key").read_text().strip() if (DATA / "secret.key").exists() else None)
if not SECRET:
    SECRET = secrets.token_hex(32); (DATA / "secret.key").write_text(SECRET)
PUSH_TOKEN = os.environ.get("SENTINELA_PUSH_TOKEN") or ((DATA / "push.token").read_text().strip() if (DATA / "push.token").exists() else None)
if not PUSH_TOKEN:
    PUSH_TOKEN = secrets.token_urlsafe(32); (DATA / "push.token").write_text(PUSH_TOKEN)

# Sessão de 12 h assinada com itsdangerous; todas as rotas ficam sob o prefixo /app.
SESSION_MAX = 12 * 3600
signer = URLSafeTimedSerializer(SECRET, salt="sentinela-session")
app = FastAPI(title="Sentinela da Vida")
r = APIRouter(prefix="/app")

# ---------- usuários ----------
# Usuários da família em SQLite (data/cloud.db): username, salt, hash PBKDF2, nome.
def db():
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row; return c
with db() as c:
    c.execute("CREATE TABLE IF NOT EXISTS users(username TEXT PRIMARY KEY, salt TEXT, hash TEXT, name TEXT, created REAL)")

# Senhas: PBKDF2-SHA256 com 200 mil iterações e salt por usuário; comparação em tempo constante.
def hash_pw(pw, salt): return hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 200_000).hex()
def create_user(username, password, name=""):
    salt = secrets.token_hex(16)
    with db() as c:
        c.execute("INSERT OR REPLACE INTO users VALUES(?,?,?,?,?)", (username, salt, hash_pw(password, salt), name, time.time()))
def check_user(username, password):
    with db() as c:
        row = c.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    return bool(row) and hmac.compare_digest(row["hash"], hash_pw(password, row["salt"]))

# Usuário logado a partir do cookie 'sentinela' (None se ausente, inválido ou expirado).
def current_user(request: Request):
    tok = request.cookies.get("sentinela")
    if not tok: return None
    try: return signer.loads(tok, max_age=SESSION_MAX)["u"]
    except (BadSignature, SignatureExpired): return None

# Guarda das rotas de API: sem sessão -> 401 (o navegador redireciona ao login).
def require(request: Request):
    u = current_user(request)
    if not u: raise HTTPException(401, "login")
    return u

# ---------- estado vindo da casa ----------
# Último estado recebido da casa, em memória e espelhado em data/state.json
# (sobrevive a reinícios do serviço). O lock protege leitura/escrita concorrente.
_lock = threading.Lock()
state = {"ts": 0, "nodes": {}, "company": False}
if STATE_FILE.exists():
    try: state = json.loads(STATE_FILE.read_text())
    except Exception: pass

# Entrada da casa: cloud_push.py envia {home, company, nodes} a cada 2 s com o token.
@r.post("/api/state")
async def post_state(request: Request, x_sentinela_token: str = Header(default="")):
    if not hmac.compare_digest(x_sentinela_token, PUSH_TOKEN): raise HTTPException(403, "token")
    body = await request.json()
    with _lock:
        state["ts"] = time.time()
        state["nodes"] = body.get("nodes", {})
        state["company"] = bool(body.get("company", False))
        state["home"] = body.get("home", "")
        STATE_FILE.write_text(json.dumps(state))
    return {"ok": True}

# Saída para o navegador: mesmo JSON + age (segundos desde o último envio) e now.
@r.get("/api/state")
def get_state(request: Request):
    require(request)
    with _lock:
        s = dict(state); s["age"] = round(time.time() - (s.get("ts") or 0), 1); s["now"] = time.time()
    return s

# ---------- cômodos (planta editada no Monitor) ----------
# Planta da casa editada no Monitor da nuvem, guardada em data/rooms.json.
@r.get("/api/rooms")
def get_rooms(request: Request):
    require(request)
    return JSONResponse(json.loads(ROOMS_FILE.read_text()) if ROOMS_FILE.exists() else [])

# Salva a planta enviada pelo editor.
@r.put("/api/rooms")
async def put_rooms(request: Request):
    require(request)
    ROOMS_FILE.write_text(json.dumps(await request.json(), ensure_ascii=False))
    return {"ok": True}

# ---------- páginas ----------
# Páginas: a raiz redireciona conforme a sessão.
@r.get("/", response_class=HTMLResponse)
def root(request: Request):
    return RedirectResponse(url="/app/monitor" if current_user(request) else "/app/login")

# Tela de login (templates/login.html com o marcador de erro substituído por str.replace).
@r.get("/login", response_class=HTMLResponse)
def login_page(error: str = ""):
    html = (BASE / "templates" / "login.html").read_text()
    return html.replace("{{error}}", '<p class="err">Usuário ou senha incorretos.</p>' if error else "")

# Processa o login e grava o cookie (httponly, secure, 12 h).
@r.post("/login")
def login(username: str = Form(...), password: str = Form(...)):
    if not check_user(username.strip().lower(), password):
        return RedirectResponse(url="/app/login?error=1", status_code=303)
    r = RedirectResponse(url="/app/monitor", status_code=303)
    r.set_cookie("sentinela", signer.dumps({"u": username.strip().lower()}), max_age=SESSION_MAX, httponly=True, samesite="lax", secure=True)
    return r

# Apaga o cookie e volta ao login.
@r.get("/logout")
def logout():
    r = RedirectResponse(url="/app/login", status_code=303); r.delete_cookie("sentinela"); return r

# Painel dos sensores (templates/painel.html) e Sentinela Monitor (static/monitor.html,
# gerado por build_monitor.py): ambos exigem login.
@r.get("/painel", response_class=HTMLResponse)
def painel(request: Request):
    if not current_user(request): return RedirectResponse(url="/app/login")
    return FileResponse(BASE / "templates" / "painel.html", media_type="text/html")

@r.get("/monitor", response_class=HTMLResponse)
def monitor(request: Request):
    if not current_user(request): return RedirectResponse(url="/app/login")
    return FileResponse(BASE / "static" / "monitor.html", media_type="text/html")

# Registra as rotas /app/... e redireciona a raiz do serviço para /app/.
app.include_router(r)

@app.get("/")
def _root(): return RedirectResponse(url="/app/")

# Utilitários de linha de comando (o serviço sobe via uvicorn, não por aqui).
if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 4 and sys.argv[1] == "adduser":
        create_user(sys.argv[2].lower(), sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else ""); print("usuário criado:", sys.argv[2])
    elif len(sys.argv) >= 2 and sys.argv[1] == "token":
        print(PUSH_TOKEN)
    else:
        print("uso: python app.py adduser <usuario> <senha> [nome]  |  python app.py token")
