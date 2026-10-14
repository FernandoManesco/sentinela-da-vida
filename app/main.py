# ---------------------------------------------------------------------------
# app/main.py — aplicação FastAPI do webSentinela: rotas HTTP, login, CRUD de usuários
#   e dispositivos, WebSocket do painel, ciclo de 1 s e API do Sentinela Monitor.
# Onde roda: notebook da casa, no processo do Uvicorn aberto por run.py (porta 8080).
# Conexões: app/listener.py (recebe UDP e mantém o estado dos nós), app/db.py (SQLite),
#   app/auth.py (sessão); serve templates/ e static/ ao navegador; /api/state e
#   /api/rooms são consumidos por static/monitor.html e por cloud_push.py, que repassa
#   o estado ao T-140 (nuvem/app.py).
# ---------------------------------------------------------------------------
"""webSentinela: painel FastAPI dos nós RuView.

Configuração por ambiente (run.py preenche a partir dos argumentos):
  WEBSENTINELA_SIMULADO=1      gera dados sintéticos em vez de abrir a porta UDP
  WEBSENTINELA_UDP_HOST        padrão 0.0.0.0
  WEBSENTINELA_UDP_PORTA       padrão 5005
"""
import asyncio
import json
import logging
import os
import re
import sqlite3
import time
from contextlib import asynccontextmanager
from datetime import datetime
from urllib.parse import urlparse

from fastapi import FastAPI, Form, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from .auth import autenticar, chave_sessao, hash_senha, usuario_atual, usuario_da_sessao
from .db import BASE_DIR, conexao, criar_esquema
from .listener import Monitor, iniciar_udp, numero_do_node, simular

# Log no console (Uvicorn, listener e app).
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("websentinela")

# Pastas de templates (Jinja2) e de arquivos estáticos.
APP_DIR = BASE_DIR / "app"
templates = Jinja2Templates(directory=APP_DIR / "templates")

# Configuração vinda do ambiente (preenchido por run.py a partir dos argumentos).
SIMULADO = os.environ.get("WEBSENTINELA_SIMULADO") == "1"
UDP_HOST = os.environ.get("WEBSENTINELA_UDP_HOST", "0.0.0.0")
UDP_PORTA = int(os.environ.get("WEBSENTINELA_UDP_PORTA", "5005"))

# Regras de validação dos formulários (checadas no servidor, não só no HTML).
RE_LOGIN = re.compile(r"[a-z0-9._-]{3,32}")
RE_NODE = re.compile(r"node-\d{1,3}")
RE_MAC = re.compile(r"[0-9a-f]{2}(:[0-9a-f]{2}){5}")
PERFIS = ("ADM", "APOIO")
SENHA_MIN = 8

# Sentinela Monitor (app/static/monitor.html): planta da casa e latch do bit de queda.
ROOMS_PATH = BASE_DIR / "rooms.json"
QUEDA_LATCH_S = 30.0


# ---------------------------------------------------------------- tempo real

# Mantém os WebSockets abertos do painel e envia o snapshot a todos quando o
# listener sinaliza mudança (flag 'sujo'), no máximo uma vez por segundo.
class Hub:
    """Clientes WebSocket do painel; o snapshot sai no máximo 1x/s quando algo muda."""

    # clientes conectados; sujo=True força um envio no próximo ciclo.
    def __init__(self):
        self.clientes: set[WebSocket] = set()
        self.sujo = True

    # Callback entregue ao Monitor: 'há algo novo para o painel'.
    def marcar(self):
        self.sujo = True

    # Envia o snapshot completo a um único cliente (ao conectar).
    async def enviar(self, ws: WebSocket):
        await ws.send_json(snapshot())

    # Envia o snapshot a todos; cliente que falhar é removido.
    async def difundir(self):
        if not self.clientes:
            return
        dados = snapshot()
        for ws in list(self.clientes):
            try:
                await ws.send_json(dados)
            except Exception:
                self.clientes.discard(ws)


# Instâncias globais: um hub e um monitor para todo o processo.
hub = Hub()
monitor = Monitor(hub.marcar)


# Monta o JSON do painel: um card por dispositivo ativo (estado em memória do
# listener) + últimos 25 eventos do banco. Vai no HTML inicial e pelo WebSocket.
def snapshot() -> dict:
    with conexao() as conn:
        disps = conn.execute(
            "SELECT id, node_id, mac, comodo FROM DISPOSITIVO WHERE ativo = 1 ORDER BY node_id"
        ).fetchall()
        eventos = conn.execute(
            "SELECT e.tipo, e.valor, e.data_hora, d.node_id, d.comodo "
            "FROM EVENTO e JOIN DISPOSITIVO d ON d.id = e.dispositivo_id "
            "ORDER BY e.id DESC LIMIT 25"
        ).fetchall()
    cards = []
    for d in disps:
        st = monitor.estados.get(numero_do_node(d["node_id"]))
        cards.append({
            "node_id": d["node_id"],
            "comodo": d["comodo"],
            "mac": d["mac"],
            "online": bool(st and st.online),
            "rssi": st.rssi if st else None,
            "presenca": st.presenca if st else None,
            "movimento": st.movimento if st else None,
            "pessoas": st.pessoas if st else None,
            "respiracao_bpm": st.respiracao_bpm if st else None,
            "cardiaco_bpm": st.cardiaco_bpm if st else None,
            "leitura_em": st.leitura_em if st else None,
        })
    return {
        "simulado": SIMULADO,
        "gerado_em": datetime.now().isoformat(timespec="seconds"),
        "dispositivos": cards,
        "eventos": [dict(e) for e in eventos],
    }


# Tarefa de fundo (1 s): detecta nós offline e difunde o snapshot se algo mudou.
async def _ciclo():
    while True:
        await asyncio.sleep(1.0)
        try:
            monitor.verificar_offline()
            if hub.sujo:
                hub.sujo = False
                await hub.difundir()
        except Exception:
            log.exception("erro no ciclo do painel")


# Startup/shutdown do app: garante o esquema do banco, inicia o ciclo e o listener
# UDP (ou o simulador); no fim cancela as tarefas e fecha o socket.
@asynccontextmanager
async def lifespan(app: FastAPI):
    with conexao() as conn:
        criar_esquema(conn)
    tarefas = [asyncio.create_task(_ciclo())]
    transport = None
    if SIMULADO:
        tarefas.append(asyncio.create_task(simular(monitor)))
    else:
        transport = await iniciar_udp(monitor, UDP_HOST, UDP_PORTA)
    yield
    for t in tarefas:
        t.cancel()
    if transport:
        transport.close()


# Aplicação FastAPI. Docs automáticas (/docs, /redoc) desligadas de propósito.
app = FastAPI(title="webSentinela", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
# Cookie de sessão assinado 'websentinela': 8 h de validade, SameSite=Strict.
app.add_middleware(
    SessionMiddleware,
    secret_key=chave_sessao(),
    session_cookie="websentinela",
    max_age=8 * 3600,
    same_site="strict",
)
# /static serve painel.js, style.css e monitor.html.
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")


# ---------------------------------------------------------------- helpers

# Redirecionamento 303 (padrão após POST de formulário).
def ir(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


# Renderiza um template com o contexto comum (usuário logado, faixa de simulado).
def render(request: Request, nome: str, usuario, status_code: int = 200, **ctx) -> HTMLResponse:
    return templates.TemplateResponse(
        request, nome, {"usuario": usuario, "simulado": SIMULADO, **ctx}, status_code=status_code
    )


# Guarda das rotas: sem sessão -> /login; perfil errado -> 403. Usada em todas as
# rotas protegidas, inclusive nos POSTs.
def exigir(request: Request, perfil: str | None = None):
    """Devolve (usuario, None) ou (None, resposta de redirecionamento/403)."""
    u = usuario_atual(request)
    if u is None:
        return None, ir("/login")
    if perfil and u["perfil"] != perfil:
        return None, render(request, "erro.html", u, status_code=403,
                            mensagem="Acesso restrito ao perfil ADM.")
    return u, None


# Conta ADMs ativos (opcionalmente ignorando um id) para proteger o último ADM.
def ativos_adm(conn, excluindo: int | None = None) -> int:
    q = "SELECT COUNT(*) FROM USUARIO WHERE perfil = 'ADM' AND ativo = 1"
    args: tuple = ()
    if excluindo is not None:
        q += " AND id <> ?"
        args = (excluindo,)
    return conn.execute(q, args).fetchone()[0]


# ---------------------------------------------------------------- login

# Raiz: manda para o painel se logado, senão para o login.
@app.get("/")
def raiz(request: Request):
    return ir("/painel" if usuario_atual(request) else "/login")


# Tela de login (GET). Quem já está logado é levado ao painel.
@app.get("/login")
def login_form(request: Request):
    if usuario_atual(request):
        return ir("/painel")
    return render(request, "login.html", None)


# Processa o login: o bcrypt roda em thread para não travar o loop; em falha,
# espera 0,5 s e devolve 401 com mensagem genérica.
@app.post("/login")
async def login(request: Request, login: str = Form(...), senha: str = Form(...)):
    u = await asyncio.to_thread(autenticar, login.strip().lower(), senha)
    if u is None:
        await asyncio.sleep(0.5)
        return render(request, "login.html", None, status_code=401,
                      erro="Login ou senha inválidos.", login=login)
    request.session.clear()
    request.session["uid"] = u["id"]
    return ir("/painel")


# Encerra a sessão (POST, para não ser disparado por um simples link).
@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return ir("/login")


# ---------------------------------------------------------------- painel

# Painel dos dispositivos: o HTML já vem com o snapshot inicial embutido;
# painel.js assume e atualiza via WebSocket.
@app.get("/painel")
def painel(request: Request):
    u, resp = exigir(request)
    if resp:
        return resp
    return render(request, "painel.html", u, inicial=snapshot())


# Canal ao vivo do painel. Exige mesma origem e sessão válida; depois só envia
# snapshots (o cliente não manda nada útil).
@app.websocket("/ws")
async def ws_painel(ws: WebSocket):
    # Mesma origem apenas (evita sequestro de WebSocket por outro site) e sessão válida.
    origem = ws.headers.get("origin")
    if origem and urlparse(origem).netloc != ws.headers.get("host"):
        await ws.close(code=1008)
        return
    if usuario_da_sessao(ws.session) is None:
        await ws.close(code=1008)
        return
    await ws.accept()
    hub.clientes.add(ws)
    try:
        await hub.enviar(ws)
        while True:
            await ws.receive_text()  # o cliente não manda nada útil; só detecta desconexão
    except WebSocketDisconnect:
        pass
    finally:
        hub.clientes.discard(ws)


# ---------------------------------------------------------------- usuários (ADM)

# Lista de usuários para a tela de administração.
def _lista_usuarios():
    with conexao() as conn:
        return conn.execute("SELECT id, nome, login, perfil, ativo FROM USUARIO ORDER BY nome").fetchall()


# Renderiza usuarios.html com mensagens de erro/sucesso.
def _tela_usuarios(request, u, erro=None, ok=None, status_code=200):
    return render(request, "usuarios.html", u, status_code=status_code,
                  usuarios=_lista_usuarios(), perfis=PERFIS, erro=erro, ok=ok)


# Tela de usuários (ADM).
@app.get("/usuarios")
def usuarios(request: Request):
    u, resp = exigir(request, "ADM")
    if resp:
        return resp
    return _tela_usuarios(request, u)


# Cria usuário (ADM): valida nome, login, senha mínima e perfil; login é UNIQUE.
@app.post("/usuarios")
def usuario_criar(request: Request, nome: str = Form(...), login: str = Form(...),
                  senha: str = Form(...), perfil: str = Form(...)):
    u, resp = exigir(request, "ADM")
    if resp:
        return resp
    nome, login = nome.strip(), login.strip().lower()
    if not nome or len(nome) > 80:
        return _tela_usuarios(request, u, erro="Nome obrigatório (até 80 caracteres).", status_code=400)
    if not RE_LOGIN.fullmatch(login):
        return _tela_usuarios(request, u, erro="Login: 3 a 32 caracteres, a-z 0-9 . _ -", status_code=400)
    if len(senha) < SENHA_MIN:
        return _tela_usuarios(request, u, erro=f"Senha com pelo menos {SENHA_MIN} caracteres.", status_code=400)
    if perfil not in PERFIS:
        return _tela_usuarios(request, u, erro="Perfil inválido.", status_code=400)
    try:
        with conexao() as conn:
            conn.execute(
                "INSERT INTO USUARIO (nome, login, senha_hash, perfil, ativo) VALUES (?, ?, ?, ?, 1)",
                (nome, login, hash_senha(senha), perfil),
            )
    except sqlite3.IntegrityError:
        return _tela_usuarios(request, u, erro=f"Login '{login}' já existe.", status_code=400)
    return _tela_usuarios(request, u, ok=f"Usuário '{login}' criado.")


# Edita usuário (ADM): nome, perfil, ativo e senha opcional. Impede remover o
# último ADM ativo; se o ADM rebaixar a si mesmo, cai para o painel.
@app.post("/usuarios/{uid}")
def usuario_editar(request: Request, uid: int, nome: str = Form(...), perfil: str = Form(...),
                   ativo: str | None = Form(None), senha: str = Form("")):
    u, resp = exigir(request, "ADM")
    if resp:
        return resp
    nome = nome.strip()
    ativo_i = 1 if ativo else 0
    if not nome or len(nome) > 80 or perfil not in PERFIS:
        return _tela_usuarios(request, u, erro="Nome ou perfil inválido.", status_code=400)
    if senha and len(senha) < SENHA_MIN:
        return _tela_usuarios(request, u, erro=f"Senha com pelo menos {SENHA_MIN} caracteres.", status_code=400)
    with conexao() as conn:
        alvo = conn.execute("SELECT id FROM USUARIO WHERE id = ?", (uid,)).fetchone()
        if alvo is None:
            return _tela_usuarios(request, u, erro="Usuário não encontrado.", status_code=404)
        if (perfil != "ADM" or not ativo_i) and ativos_adm(conn, excluindo=uid) == 0:
            return _tela_usuarios(request, u, erro="Precisa sobrar pelo menos um ADM ativo.", status_code=400)
        conn.execute("UPDATE USUARIO SET nome = ?, perfil = ?, ativo = ? WHERE id = ?",
                     (nome, perfil, ativo_i, uid))
        if senha:
            conn.execute("UPDATE USUARIO SET senha_hash = ? WHERE id = ?", (hash_senha(senha), uid))
    if uid == u["id"] and (perfil != "ADM" or not ativo_i):
        return ir("/painel")
    return _tela_usuarios(request, u, ok="Usuário atualizado.")


# Exclui usuário (ADM), exceto o próprio e o último ADM ativo.
@app.post("/usuarios/{uid}/excluir")
def usuario_excluir(request: Request, uid: int):
    u, resp = exigir(request, "ADM")
    if resp:
        return resp
    if uid == u["id"]:
        return _tela_usuarios(request, u, erro="Você não pode excluir o próprio usuário.", status_code=400)
    with conexao() as conn:
        if ativos_adm(conn, excluindo=uid) == 0:
            return _tela_usuarios(request, u, erro="Precisa sobrar pelo menos um ADM ativo.", status_code=400)
        conn.execute("DELETE FROM USUARIO WHERE id = ?", (uid,))
    return _tela_usuarios(request, u, ok="Usuário excluído.")


# ---------------------------------------------------------------- dispositivos (ADM)

# Lista de dispositivos para a tela de administração.
def _lista_disps():
    with conexao() as conn:
        return conn.execute("SELECT id, node_id, mac, comodo, ativo FROM DISPOSITIVO ORDER BY node_id").fetchall()


# Renderiza dispositivos.html com mensagens de erro/sucesso.
def _tela_disps(request, u, erro=None, ok=None, status_code=200):
    return render(request, "dispositivos.html", u, status_code=status_code,
                  dispositivos=_lista_disps(), erro=erro, ok=ok)


# Valida node-NN, MAC e tamanho do cômodo; devolve a mensagem de erro ou None.
def _valida_disp(node_id: str, mac: str, comodo: str):
    if not RE_NODE.fullmatch(node_id):
        return "node_id no formato node-NN (ex.: node-04)."
    if not RE_MAC.fullmatch(mac):
        return "MAC no formato aa:bb:cc:dd:ee:ff."
    if len(comodo) > 60:
        return "Cômodo com até 60 caracteres."
    return None


# Após qualquer alteração no cadastro: o listener relê o mapa e o painel atualiza.
def _apos_mudar_disp():
    monitor.recarregar_dispositivos()
    hub.marcar()


# Tela de dispositivos (ADM).
@app.get("/dispositivos")
def dispositivos(request: Request):
    u, resp = exigir(request, "ADM")
    if resp:
        return resp
    return _tela_disps(request, u)


# Cria dispositivo (ADM).
@app.post("/dispositivos")
def disp_criar(request: Request, node_id: str = Form(...), mac: str = Form(...), comodo: str = Form("")):
    u, resp = exigir(request, "ADM")
    if resp:
        return resp
    node_id, mac, comodo = node_id.strip().lower(), mac.strip().lower(), comodo.strip()
    if erro := _valida_disp(node_id, mac, comodo):
        return _tela_disps(request, u, erro=erro, status_code=400)
    try:
        with conexao() as conn:
            conn.execute("INSERT INTO DISPOSITIVO (node_id, mac, comodo, ativo) VALUES (?, ?, ?, 1)",
                         (node_id, mac, comodo))
    except sqlite3.IntegrityError:
        return _tela_disps(request, u, erro="node_id ou MAC já cadastrado.", status_code=400)
    _apos_mudar_disp()
    return _tela_disps(request, u, ok=f"Dispositivo '{node_id}' criado.")


# Edita dispositivo (ADM): node_id, MAC, cômodo e ativo.
@app.post("/dispositivos/{did}")
def disp_editar(request: Request, did: int, node_id: str = Form(...), mac: str = Form(...),
                comodo: str = Form(""), ativo: str | None = Form(None)):
    u, resp = exigir(request, "ADM")
    if resp:
        return resp
    node_id, mac, comodo = node_id.strip().lower(), mac.strip().lower(), comodo.strip()
    if erro := _valida_disp(node_id, mac, comodo):
        return _tela_disps(request, u, erro=erro, status_code=400)
    try:
        with conexao() as conn:
            cur = conn.execute("UPDATE DISPOSITIVO SET node_id = ?, mac = ?, comodo = ?, ativo = ? WHERE id = ?",
                               (node_id, mac, comodo, 1 if ativo else 0, did))
            if cur.rowcount == 0:
                return _tela_disps(request, u, erro="Dispositivo não encontrado.", status_code=404)
    except sqlite3.IntegrityError:
        return _tela_disps(request, u, erro="node_id ou MAC já cadastrado.", status_code=400)
    _apos_mudar_disp()
    return _tela_disps(request, u, ok="Dispositivo atualizado.")


# Exclui dispositivo (ADM); os eventos vão junto (ON DELETE CASCADE).
@app.post("/dispositivos/{did}/excluir")
def disp_excluir(request: Request, did: int):
    u, resp = exigir(request, "ADM")
    if resp:
        return resp
    with conexao() as conn:
        conn.execute("DELETE FROM DISPOSITIVO WHERE id = ?", (did,))
    _apos_mudar_disp()
    return _tela_disps(request, u, ok="Dispositivo e seus eventos excluídos.")


# ---------------------------------------------------------------- monitor (rede local, sem login)

# JSON do Sentinela Monitor (formato documentado no README): um item por nó com
# presence, motion, fall (latch de 30 s), still_s, last_seen, energy, baseline e
# people. É o mesmo JSON que cloud_push.py envia ao T-140.
def estado_monitor() -> dict:
    """Formato que o monitor.html consome a cada 2 s (ver poll()/applyLive() na página)."""
    agora = time.time()
    with conexao() as conn:
        disps = conn.execute("SELECT node_id FROM DISPOSITIVO WHERE ativo = 1 ORDER BY node_id").fetchall()
    nodes = {}
    ts = 0.0
    for d in disps:
        st = monitor.estados.get(numero_do_node(d["node_id"]))
        if st is None or not st.visto_epoch:
            nodes[d["node_id"]] = {"presence": 0, "motion": 0, "fall": 0, "still_s": 0, "last_seen": 0,
                                   "energy": 0.0, "baseline": 0.0, "people": 0}
            continue
        ts = max(ts, st.visto_epoch)
        # motion vem do software (energia das features vs. baseline), não do bit 2 do flags.
        movimento = bool(st.movimento)
        parado_desde = st.movimento_epoch or st.primeiro_epoch  # nunca se moveu: desde o 1º pacote
        nodes[d["node_id"]] = {
            "presence": int(bool(st.presenca)),
            "motion": int(movimento),
            "fall": int(agora - st.queda_epoch <= QUEDA_LATCH_S),
            "still_s": 0 if movimento else round(agora - parado_desde),
            "last_seen": round(st.visto_epoch, 3),
            "energy": round(st.energia or 0.0, 2),
            "baseline": round(st.baseline or 0.0, 3),
            "people": int(st.pessoas or 0),
        }
    return {
        "ts": round(ts, 3),
        "now": round(agora, 3),
        "age": round(agora - ts, 3) if ts else None,
        "company": False,
        "nodes": nodes,
    }


# Página do Sentinela Monitor (arquivo estático, sem login: só na rede local).
@app.get("/monitor")
def monitor_pagina():
    return FileResponse(APP_DIR / "static" / "monitor.html", media_type="text/html")


# Estado dos nós para o Monitor (polling a cada 2 s) e para o cloud_push.py.
@app.get("/api/state")
def api_state():
    return JSONResponse(estado_monitor(), headers={"Cache-Control": "no-store"})


# Planta da casa editada no Monitor: lê rooms.json da raiz ([] se não existir).
@app.get("/api/rooms")
def api_rooms():
    if not ROOMS_PATH.exists():
        return []
    try:
        dados = json.loads(ROOMS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        log.exception("rooms.json ilegível; devolvendo lista vazia")
        return []
    return dados if isinstance(dados, list) else []


# Salva a planta enviada pelo editor do Monitor em rooms.json (escrita atômica).
@app.put("/api/rooms")
async def api_rooms_salvar(request: Request):
    try:
        dados = await request.json()
    except ValueError:
        raise HTTPException(400, "corpo não é JSON válido")
    if not isinstance(dados, list):
        raise HTTPException(400, "esperada uma lista de cômodos")
    # Grava em arquivo temporário e troca: o monitor nunca lê um rooms.json pela metade.
    tmp = ROOMS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, ROOMS_PATH)
    return {"ok": True, "comodos": len(dados)}
