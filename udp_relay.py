#!/usr/bin/env python3
"""Encaminha UDP recebido no Windows para o servidor rodando dentro do WSL2.

O ESP32 manda os frames CSI para o IP do notebook na LAN (192.168.137.1).
O WSL2 em modo NAT nao enxerga esse trafego, entao este relay faz a ponte:

    ESP32 --> 192.168.137.1:5005 (Windows) --> 172.29.x.x:5005 (WSL)

Uso:
    python udp_relay.py <ip_do_wsl> [porta]

O IP do WSL muda a cada `wsl --shutdown`. Descubra com:
    wsl hostname -I
"""
import socket
import sys
import time

PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 5005

if len(sys.argv) < 2:
    sys.exit(f"uso: {sys.argv[0]} <ip_do_wsl> [porta={PORT}]")

target = (sys.argv[1], PORT)

rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
rx.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
rx.bind(("0.0.0.0", PORT))
tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

print(f"relay ativo: 0.0.0.0:{PORT} -> {target[0]}:{target[1]}")
print("Ctrl+C para parar")

n = 0
started = time.time()
last_report = started
sources = {}

try:
    while True:
        data, addr = rx.recvfrom(65535)
        tx.sendto(data, target)
        n += 1
        sources[addr[0]] = sources.get(addr[0], 0) + 1
        now = time.time()
        if now - last_report >= 5.0:
            pps = n / (now - started)
            srcs = ", ".join(f"{ip} ({c})" for ip, c in sources.items())
            print(f"{n} pacotes, {pps:.1f} pps | origens: {srcs}", flush=True)
            last_report = now
except KeyboardInterrupt:
    print(f"\nparado. {n} pacotes encaminhados.")
