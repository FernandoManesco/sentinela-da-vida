# ---------------------------------------------------------------------------
# run.py — ponto de entrada do webSentinela (servidor local da casa).
# Onde roda: notebook da casa (Windows via iniciar.bat, ou Linux/WSL no terminal).
# O que faz: lê os argumentos de linha de comando (--simulado, --porta-udp, --host-udp,
#   --host, --porta), exporta-os como variáveis de ambiente WEBSENTINELA_* e sobe o
#   Uvicorn com a aplicação FastAPI de app/main.py na porta HTTP 8080.
# Conexões: app/main.py lê as variáveis de ambiente definidas aqui; o navegador acessa
#   http://localhost:8080 (painel e monitor); cloud_push.py lê /api/state deste
#   servidor e repassa o estado para a nuvem (T-140).
# ---------------------------------------------------------------------------
"""Sobe o webSentinela na porta 8080.

    python run.py               # escuta os nós em UDP 0.0.0.0:5005
    python run.py --simulado    # dados sintéticos, sem UDP
    python run.py --porta-udp 5006
"""
import argparse
import os

import uvicorn


# Monta o parser de argumentos (a ajuda usa o docstring acima), exporta a
# configuração no ambiente e sobe o servidor. 'app.main:app' só é importado
# pelo Uvicorn depois disso, por isso as variáveis precisam existir antes.
def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--simulado", action="store_true", help="gera dados SYNTHETIC em vez de escutar UDP")
    p.add_argument("--porta-udp", type=int, default=5005)
    p.add_argument("--host-udp", default="0.0.0.0")
    p.add_argument("--host", default="0.0.0.0", help="interface HTTP (padrão 0.0.0.0)")
    p.add_argument("--porta", type=int, default=8080)
    a = p.parse_args()

    # Definido antes de importar app.main, que lê a configuração do ambiente.
    os.environ["WEBSENTINELA_SIMULADO"] = "1" if a.simulado else "0"
    os.environ["WEBSENTINELA_UDP_PORTA"] = str(a.porta_udp)
    os.environ["WEBSENTINELA_UDP_HOST"] = a.host_udp
    uvicorn.run("app.main:app", host=a.host, port=a.porta)


# Executado só quando chamado diretamente (python run.py), nunca ao importar.
if __name__ == "__main__":
    main()
