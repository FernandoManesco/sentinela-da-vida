# ---------------------------------------------------------------------------
# app/listener.py — recepção UDP dos nós ESP32-C6, parser dos pacotes, estado em memória
#   e gravação de eventos. Também contém o simulador (modo --simulado).
# Onde roda: notebook da casa, dentro do processo do webSentinela (tarefa asyncio criada
#   no lifespan de app/main.py).
# Conexões: escuta UDP 0.0.0.0:5005 (ou o que o udp_relay.py do Windows encaminha ao WSL);
#   grava EVENTO via app/db.py; expõe Monitor.estados, que app/main.py lê para montar o
#   snapshot do painel e o JSON de /api/state. Os formatos de pacote vêm do firmware
#   RuView (firmware/esp32-csi-node/main/).
# ---------------------------------------------------------------------------
"""Recebe os frames UDP dos nós ESP32 e mantém status/eventos.

Formatos lidos (definidos em firmware/esp32-csi-node/main/):
  0xC5110001  frame CSI bruto, ADR-018 (csi_collector.h): node_id no byte 4, RSSI (i8) no byte 16
  0xC5110002  pacote de vitais, 32 bytes (edge_processing.h, edge_vitals_pkt_t)
  0xC5110003  vetor de features, 48 bytes (edge_processing.h, edge_feature_pkt_t, ADR-069)
  0xC5110004  vitais fundidos CSI+mmWave; os primeiros 32 bytes têm o mesmo layout
Demais magics (estado, mesh, WASM...) só contam como "nó vivo".

O node_id do pacote é um inteiro (u8); ele é casado com DISPOSITIVO.node_id
"node-NN" pelo número (node-01 <-> 1).
"""
import asyncio
import logging
import random
import re
import struct
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime

from .db import conexao

# Logger próprio do listener (aparece no console do run.py).
log = logging.getLogger("websentinela.listener")

# Magics (4 primeiros bytes) dos pacotes do firmware; o prefixo 0xC511 identifica
# qualquer pacote RuView, mesmo os que o painel não decodifica.
CSI_MAGIC = 0xC5110001
VITALS_MAGIC = 0xC5110002
FEATURES_MAGIC = 0xC5110003
FUSED_MAGIC = 0xC5110004
KNOWN_MAGIC_PREFIX = 0xC511

# edge_vitals_pkt_t: magic u32, node_id u8, flags u8, breathing u16 (BPM*100),
# heartrate u32 (BPM*10000), rssi i8, n_persons u8, reserved[2],
# motion_energy f32, presence_score f32, timestamp_ms u32, reserved2 u32
# Layout binário do pacote de vitais (little-endian, 32 bytes), conferido no assert.
VITALS = struct.Struct("<IBBHIbB2xffII")
assert VITALS.size == 32

# Bits do campo flags do pacote de vitais.
FLAG_PRESENCE = 0x01
FLAG_FALL = 0x02
FLAG_MOTION = 0x04

# edge_feature_pkt_t: magic u32, node_id u8, reserved u8, seq u16, timestamp_us i64,
# features f32[8] normalizadas 0-1: [0] presence_score, [1] motion_energy,
# [5] n_persons/4, [6] queda (0/1).
# Layout do pacote de features (48 bytes); só features[1] (energia) é usada aqui.
FEATURES = struct.Struct("<IBBHq8f")
assert FEATURES.size == 48
FEAT_ENERGIA = 1

# Movimento por software a partir da energia das features: o bit 2 do flags é só
# "energia > 0,01" e dispara com ruído. baseline = percentil 20 da energia dos
# últimos 60 s; movimento = energia > max(3 x baseline, baseline + 0,02).
JANELA_ENERGIA_S = 60.0
MOV_FATOR = 3.0
MOV_DEGRAU = 0.02

# Nó sem pacotes por este tempo vira 'offline' (evento gravado).
OFFLINE_APOS_S = 10.0


# Estado de um nó em memória. Nada disto vai ao banco a cada pacote; só as
# transições viram EVENTO. Os campos *_epoch alimentam o /api/state do Monitor.
@dataclass
class EstadoNo:
    online: bool = False
    visto_em: float = 0.0
    rssi: int | None = None
    presenca: bool | None = None
    movimento: bool | None = None
    pessoas: int | None = None
    respiracao_bpm: float | None = None
    cardiaco_bpm: float | None = None
    score: float | None = None
    leitura_em: str | None = None
    extra: dict = field(default_factory=dict)
    # Epoch (time.time()) para o /api/state do monitor: último pacote, último com
    # movimento e último com queda (o bit de queda do firmware é transitório).
    primeiro_epoch: float = 0.0
    visto_epoch: float = 0.0
    movimento_epoch: float = 0.0
    queda_epoch: float = 0.0
    # Pacote de features: energia atual e baseline da janela. `movimento` (painel,
    # evento e /api/state) é calculado daqui, não do bit 2 do flags.
    energia: float | None = None
    baseline: float | None = None
    historico_energia: deque = field(default_factory=deque)  # (epoch, energia), últimos 60 s


# Data/hora local em ISO 8601 sem microssegundos (formato gravado em EVENTO).
def agora_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


# 'node-01' -> 1. Liga o cadastro (texto) ao byte node_id do pacote (inteiro).
def numero_do_node(node_id: str) -> int | None:
    m = re.fullmatch(r"node-0*(\d{1,3})", node_id.strip())
    return int(m.group(1)) if m else None


# Percentil simples (sem numpy) usado para estimar o ruído de fundo (baseline).
def percentil(valores: list[float], p: float) -> float:
    """Percentil p (0-100) por interpolação linear; valores já ordenados."""
    if not valores:
        return 0.0
    pos = (len(valores) - 1) * p / 100.0
    i = int(pos)
    frac = pos - i
    if i + 1 < len(valores):
        return valores[i] + (valores[i + 1] - valores[i]) * frac
    return valores[i]


# Núcleo do listener: recebe bytes, atualiza o EstadoNo de cada nó e grava eventos.
# ao_mudar() avisa o Hub (app/main.py) que há um snapshot novo a difundir.
class Monitor:
    """Estado em memória + gravação de eventos em transições (não a cada pacote)."""

    # estados: número do nó -> EstadoNo; _mapa: número do nó -> id em DISPOSITIVO.
    def __init__(self, ao_mudar):
        self.estados: dict[int, EstadoNo] = {}
        self.ao_mudar = ao_mudar          # callback sem argumentos: há algo novo para o painel
        self.pacotes = 0
        self.descartados = 0
        self._mapa: dict[int, int] = {}
        self._mapa_em = float("-inf")

    # Devolve o id do dispositivo cadastrado e ativo para o número, com cache de 5 s
    # (CSI chega a dezenas de frames/s; não dá para consultar o banco a cada um).
    def _dispositivo(self, numero: int):
        # CSI chega a dezenas de frames/s por nó: relê o cadastro no máximo a cada 5 s.
        if time.monotonic() - self._mapa_em > 5.0:
            self.recarregar_dispositivos()
        return self._mapa.get(numero)

    # Relê o cadastro (pelo cache e logo após edições em /dispositivos).
    # Nós que saíram do cadastro perdem o estado em memória.
    def recarregar_dispositivos(self) -> None:
        with conexao() as conn:
            linhas = conn.execute("SELECT id, node_id FROM DISPOSITIVO WHERE ativo = 1").fetchall()
        self._mapa = {n: d["id"] for d in linhas if (n := numero_do_node(d["node_id"])) is not None}
        self._mapa_em = time.monotonic()
        for numero in list(self.estados):
            if numero not in self._mapa:
                del self.estados[numero]

    # Grava uma linha em EVENTO (online, offline, presenca, ausencia, movimento, queda).
    def _evento(self, numero: int, tipo: str, valor: str | None = None) -> None:
        disp_id = self._dispositivo(numero)
        if disp_id is None:
            return
        with conexao() as conn:
            conn.execute(
                "INSERT INTO EVENTO (dispositivo_id, tipo, valor, data_hora) VALUES (?, ?, ?, ?)",
                (disp_id, tipo, valor, agora_iso()),
            )

    # Entrada de todo pacote (UDP real ou simulado): valida tamanho, magic e cadastro,
    # marca o nó como online e despacha para o decodificador do tipo certo.
    def processar(self, dados: bytes, origem: str = "") -> None:
        if len(dados) < 5:
            self.descartados += 1
            return
        magic = struct.unpack_from("<I", dados, 0)[0]
        if magic >> 16 != KNOWN_MAGIC_PREFIX:
            self.descartados += 1
            return
        numero = dados[4]
        if self._dispositivo(numero) is None:
            self.descartados += 1
            return
        self.pacotes += 1

        st = self.estados.setdefault(numero, EstadoNo())
        st.visto_em = time.monotonic()
        st.visto_epoch = time.time()
        if not st.primeiro_epoch:
            st.primeiro_epoch = st.visto_epoch
        if not st.online:
            st.online = True
            self._evento(numero, "online", origem or None)

        if magic == CSI_MAGIC and len(dados) >= 20:
            st.rssi = struct.unpack_from("<b", dados, 16)[0]
        elif magic in (VITALS_MAGIC, FUSED_MAGIC) and len(dados) >= VITALS.size:
            self._vitais(numero, st, dados)
        elif magic == FEATURES_MAGIC and len(dados) >= FEATURES.size:
            self._features(numero, st, dados)
        self.ao_mudar()

    # Pacote de features: atualiza a energia, recalcula o baseline (percentil 20 dos
    # últimos 60 s) e decide movimento por software; grava evento só no início dele.
    def _features(self, numero: int, st: EstadoNo, dados: bytes) -> None:
        feats = FEATURES.unpack_from(dados, 0)[5:]
        agora = time.time()
        energia = float(feats[FEAT_ENERGIA])
        st.energia = energia
        hist = st.historico_energia
        hist.append((agora, energia))
        limite = agora - JANELA_ENERGIA_S
        while hist and hist[0][0] < limite:
            hist.popleft()
        st.baseline = percentil(sorted(e for _, e in hist), 20)
        mov = energia > max(st.baseline * MOV_FATOR, st.baseline + MOV_DEGRAU)
        if mov and not st.movimento:
            self._evento(numero, "movimento", f"energia={energia:.2f} baseline={st.baseline:.3f}")
        st.movimento = mov
        if mov:
            st.movimento_epoch = agora

    # Pacote de vitais: presença (bit 0), queda (bit 1), pessoas, RSSI, respiração e
    # cardíaco. Eventos de presença/ausência só nas transições; queda sempre.
    def _vitais(self, numero: int, st: EstadoNo, dados: bytes) -> None:
        # O bit 2 (FLAG_MOTION) é ignorado de propósito: é só "energia > 0,01" e dispara
        # com ruído. Movimento vem de _features().
        (_, _, flags, resp, card, rssi, pessoas, _energia, score, _ts, _) = VITALS.unpack_from(dados, 0)
        presenca = bool(flags & FLAG_PRESENCE)

        if st.presenca is not None and presenca != st.presenca:
            self._evento(numero, "presenca" if presenca else "ausencia", f"score={score:.2f}")
        if flags & FLAG_FALL:
            st.queda_epoch = time.time()
            self._evento(numero, "queda", f"score={score:.2f}")
        if st.presenca is None:
            self._evento(numero, "presenca" if presenca else "ausencia", f"score={score:.2f}")

        st.presenca = presenca
        st.pessoas = pessoas
        st.rssi = rssi
        st.score = score
        st.respiracao_bpm = resp / 100.0 if resp else None
        st.cardiaco_bpm = card / 10000.0 if card else None
        st.leitura_em = agora_iso()

    # Chamado 1x/s pelo ciclo do app: nós calados há 10 s ficam offline.
    def verificar_offline(self) -> None:
        limite = time.monotonic() - OFFLINE_APOS_S
        for numero, st in self.estados.items():
            if st.online and st.visto_em < limite:
                st.online = False
                st.presenca = None
                st.movimento = None
                self._evento(numero, "offline", f"sem pacotes há {OFFLINE_APOS_S:.0f}s")
                self.ao_mudar()


# Adaptador asyncio: cada datagrama recebido vira uma chamada a Monitor.processar.
class _Protocolo(asyncio.DatagramProtocol):
    def __init__(self, monitor: Monitor):
        self.monitor = monitor

    # addr[0] = IP de origem (fica registrado no evento 'online').
    def datagram_received(self, data, addr):
        try:
            self.monitor.processar(data, addr[0])
        except Exception:  # um pacote ruim não pode derrubar o listener
            log.exception("erro processando pacote de %s", addr[0])


# Abre o socket UDP no loop asyncio; devolve o transport para fechar no shutdown.
async def iniciar_udp(monitor: Monitor, host: str, porta: int):
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: _Protocolo(monitor), local_addr=(host, porta)
    )
    log.info("listener UDP em %s:%d", host, porta)
    return transport


# Monta um pacote de vitais no layout exato do firmware (usado pelo simulador e
# por testes). É o inverso de _vitais().
def pacote_vitais(numero: int, presenca: bool, movimento: bool, queda: bool,
                  pessoas: int, rssi: int, energia: float, score: float,
                  resp_bpm: float, card_bpm: float, ts_ms: int, magic: int = VITALS_MAGIC) -> bytes:
    flags = (FLAG_PRESENCE if presenca else 0) | (FLAG_MOTION if movimento else 0) | (FLAG_FALL if queda else 0)
    return VITALS.pack(magic, numero, flags, int(resp_bpm * 100), int(card_bpm * 10000),
                       rssi, pessoas, energia, score, ts_ms, 0)


# Monta um pacote de features no layout do firmware (inverso de _features()).
def pacote_features(numero: int, score: float, energia: float, pessoas: int, queda: bool,
                    seq: int = 0, ts_us: int = 0) -> bytes:
    feats = [score, energia, 0.0, 0.0, 0.0, pessoas / 4.0, 1.0 if queda else 0.0, 0.0]
    return FEATURES.pack(FEATURES_MAGIC, numero, 0, seq & 0xFFFF, ts_us, *feats)


# Modo --simulado: gera pacotes sintéticos para node-01..03 a 1 Hz e os passa
# pelo mesmo parser, exercitando todo o caminho sem hardware.
async def simular(monitor: Monitor) -> None:
    """Gera pacotes no formato real do firmware (SYNTHETIC) e passa pelo mesmo parser.

    node-03 fica mudo por ~20 s a cada 90 s para exercitar o estado offline.
    """
    log.warning("MODO SIMULADO: dados SYNTHETIC, nenhum nó real envolvido")
    inicio = time.monotonic()
    presenca = {1: False, 2: True, 3: False}
    seq = 0
    while True:
        t = time.monotonic() - inicio
        seq += 1
        for numero in (1, 2, 3):
            if numero == 3 and (t % 90) > 70:
                continue
            if random.random() < 0.05:
                presenca[numero] = not presenca[numero]
            p = presenca[numero]
            mov = p and random.random() < 0.3
            queda = p and random.random() < 0.003
            energia = random.uniform(0.3, 1.0) if mov else random.uniform(0.0, 0.1)
            score = random.uniform(0.6, 0.95) if p else random.uniform(0.0, 0.2)
            monitor.processar(
                pacote_vitais(
                    numero, p, mov, queda,
                    pessoas=1 if p else 0,
                    rssi=random.randint(-72, -45),
                    energia=energia,
                    score=score,
                    resp_bpm=random.uniform(12, 18) if p else 0,
                    card_bpm=random.uniform(60, 85) if p else 0,
                    ts_ms=int(t * 1000),
                ),
                "simulado",
            )
            monitor.processar(
                pacote_features(numero, score, energia, 1 if p else 0, queda, seq, int(t * 1_000_000)),
                "simulado",
            )
        await asyncio.sleep(1.0)
