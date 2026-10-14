# ---------------------------------------------------------------------------
# app/auth.py — senhas (bcrypt), chave do cookie de sessão e usuário logado.
# Onde roda: notebook da casa, dentro do webSentinela (importado por app/main.py e
#   init_db.py).
# Conexões: lê a tabela USUARIO via app/db.py; a chave de assinatura vem de
#   WEBSENTINELA_SECRET ou do arquivo .secret_key (fora do git) na raiz. O cookie em si
#   é gerado pelo SessionMiddleware configurado em app/main.py.
# ---------------------------------------------------------------------------
"""Senhas bcrypt e helpers de sessão."""
import os
import secrets

import bcrypt
from fastapi import Request

from .db import BASE_DIR, conexao

# Arquivo onde a chave de assinatura do cookie fica guardada entre reinícios.
SECRET_FILE = BASE_DIR / ".secret_key"


# Gera o hash bcrypt (salt embutido, custo padrão 12) para gravar em senha_hash.
def hash_senha(senha: str) -> str:
    return bcrypt.hashpw(senha.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


# Compara a senha digitada com o hash. ValueError = hash corrompido -> trata como errada.
def confere_senha(senha: str, senha_hash: str) -> bool:
    try:
        return bcrypt.checkpw(senha.encode("utf-8"), senha_hash.encode("ascii"))
    except ValueError:
        return False


# Chave secreta do cookie: ambiente > arquivo > gera uma nova (permissão 600).
# Trocar a chave invalida todas as sessões abertas.
def chave_sessao() -> str:
    """Chave de assinatura do cookie: env WEBSENTINELA_SECRET ou arquivo local gerado uma vez."""
    env = os.environ.get("WEBSENTINELA_SECRET")
    if env:
        return env
    if SECRET_FILE.exists():
        return SECRET_FILE.read_text().strip()
    chave = secrets.token_urlsafe(48)
    SECRET_FILE.write_text(chave)
    os.chmod(SECRET_FILE, 0o600)
    return chave


# Login: busca o usuário ativo e confere a senha. Se o login não existe, faz um
# bcrypt 'de mentira' para gastar o mesmo tempo (não revela quais logins existem).
def autenticar(login: str, senha: str):
    with conexao() as conn:
        u = conn.execute(
            "SELECT * FROM USUARIO WHERE login = ? AND ativo = 1", (login,)
        ).fetchone()
    if u is None:
        # Custo equivalente ao de um login válido, para não revelar quais logins existem.
        bcrypt.checkpw(b"x", bcrypt.hashpw(b"y", bcrypt.gensalt()))
        return None
    return u if confere_senha(senha, u["senha_hash"]) else None


# Converte o cookie (que só guarda o uid) no usuário atual, relendo o banco a cada
# requisição: desativar alguém derruba a sessão dele na hora.
def usuario_da_sessao(session: dict):
    """Recarrega o usuário a cada requisição: desativar alguém derruba a sessão dele."""
    uid = session.get("uid")
    if uid is None:
        return None
    with conexao() as conn:
        return conn.execute(
            "SELECT id, nome, login, perfil FROM USUARIO WHERE id = ? AND ativo = 1", (uid,)
        ).fetchone()


# Atalho usado pelas rotas HTTP (o WebSocket chama usuario_da_sessao direto).
def usuario_atual(request: Request):
    return usuario_da_sessao(request.session)
