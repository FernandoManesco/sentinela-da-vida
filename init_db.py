# ---------------------------------------------------------------------------
# init_db.py — cria o banco SQLite websentinela.db e grava o seed inicial.
# Onde roda: notebook da casa, uma vez, antes do primeiro run.py (pode ser repetido:
#   é idempotente, não apaga dados nem troca a senha do admin).
# O que faz: cria as tabelas USUARIO, DISPOSITIVO e EVENTO (esquema em app/db.py),
#   cadastra node-01..03 e cria o usuário admin com senha provisória impressa no console.
# Conexões: usa app/db.py (conexão e esquema) e app/auth.py (hash bcrypt). O banco
#   gerado é lido por app/main.py e app/listener.py.
# ---------------------------------------------------------------------------
"""Cria o banco websentinela.db e grava o seed inicial.

Uso:
    python init_db.py                 # cria tabelas + seed (idempotente)
    python init_db.py --reset-admin   # gera nova senha provisória para o login 'admin'
"""
import argparse
import secrets
import sys

from app.auth import hash_senha
from app.db import DB_PATH, conexao, criar_esquema

# Nós ESP32-C6 cadastrados pelo seed: (node_id, MAC). O número do node_id
# (node-01 -> 1) precisa bater com o node_id provisionado na NVS do ESP32.
DISPOSITIVOS_SEED = [
    ("node-01", "e8:f6:0a:f8:71:fc"),
    ("node-02", "9c:cc:01:40:64:a0"),
    ("node-03", "e8:f6:0a:f8:c9:88"),
]


# Fluxo principal: cria o esquema, insere o seed (INSERT OR IGNORE, não duplica)
# e cria ou redefine o admin conforme o argumento --reset-admin.
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reset-admin", action="store_true", help="redefine a senha do usuário admin")
    args = parser.parse_args()

    # Tudo dentro de uma única transação: commit ao sair sem erro.
    with conexao() as conn:
        criar_esquema(conn)

        # Seed dos dispositivos; os que já existem (node_id/MAC UNIQUE) são ignorados.
        for node_id, mac in DISPOSITIVOS_SEED:
            conn.execute(
                "INSERT OR IGNORE INTO DISPOSITIVO (node_id, mac, comodo, ativo) VALUES (?, ?, '', 1)",
                (node_id, mac),
            )

        # Admin: só é criado se não existir; --reset-admin gera nova senha e reativa.
        admin = conn.execute("SELECT id FROM USUARIO WHERE login = 'admin'").fetchone()
        senha = None
        if admin is None:
            senha = secrets.token_urlsafe(9)
            conn.execute(
                "INSERT INTO USUARIO (nome, login, senha_hash, perfil, ativo) "
                "VALUES ('Administrador', 'admin', ?, 'ADM', 1)",
                (hash_senha(senha),),
            )
        elif args.reset_admin:
            senha = secrets.token_urlsafe(9)
            conn.execute(
                "UPDATE USUARIO SET senha_hash = ?, ativo = 1 WHERE id = ?",
                (hash_senha(senha), admin["id"]),
            )

    # Resumo no console. A senha provisória só aparece aqui, uma vez: anote-a.
    print(f"Banco pronto: {DB_PATH}")
    print(f"Dispositivos seed: {', '.join(n for n, _ in DISPOSITIVOS_SEED)}")
    if senha:
        print()
        print("  login: admin")
        print(f"  senha provisória: {senha}")
        print()
        print("Troque essa senha em /usuarios depois do primeiro acesso.")
    else:
        print("Usuário admin já existe; senha mantida (use --reset-admin para gerar outra).")
    return 0


# Executado só quando chamado diretamente (python init_db.py).
if __name__ == "__main__":
    sys.exit(main())
