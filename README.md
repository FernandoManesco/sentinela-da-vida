# Sentinela da Vida — webSentinela

Monitoramento de idosos em casa por **sensoriamento WiFi (CSI)**, sem câmera e
sem pulseira. Trabalho de Conclusão de Curso construído sobre os nós ESP32-C6 e
o firmware do projeto [RuView](https://github.com/ruvnet/ruview).

Este repositório contém a parte de software que roda **na casa** (webSentinela)
e **na nuvem** (pasta `nuvem/`, servidor T-140). Todos os arquivos de código
têm um cabeçalho dizendo o que são, onde rodam e como se ligam aos outros.

> Detalhes de arquitetura, banco, segurança e limitações: [PROJETO.md](PROJETO.md).
> Passo a passo do servidor: [nuvem/LEIA-ME.md](nuvem/LEIA-ME.md).

## 1. Objetivo

Perceber, só pelo WiFi, se a pessoa idosa está em casa, em qual cômodo, se está
se movendo, parada há muito tempo ou caiu, e mostrar isso para a família de
forma simples (uma planta da casa com uma figura neutra) e para o grupo de
forma técnica (um painel por sensor). Presença, respiração, frequência cardíaca
e queda são **estimativas** do firmware; não substituem equipamento médico nem
alarme certificado.

## 2. Arquitetura

```
 nós ESP32-C6 (node-01, node-02, node-03) — firmware RuView
      │  pacotes UDP para a porta 5005 (CSI bruto, vitais, features)
      ▼
 notebook da casa ── webSentinela (FastAPI, porta 8080)
      │   app/listener.py  recebe UDP, decodifica, mantém o estado em memória
      │   app/db.py        SQLite: usuários, dispositivos, eventos (só transições)
      │   app/main.py      login, painel (/painel + WebSocket), CRUD, /api/state
      │   app/static/monitor.html   Sentinela Monitor local (/monitor)
      │
      │  cloud_push.py lê http://localhost:8080/api/state a cada 2 s
      │  e faz POST em https://www.sentineladavida.com.br/app/api/state
      ▼
 servidor T-140 ── nginx (HTTPS) ──► nuvem/app.py (uvicorn 127.0.0.1:8090, prefixo /app)
      │   guarda o último estado em nuvem/data/state.json
      ▼
 navegador da família
      /app/monitor   Sentinela Monitor (planta da casa ao vivo)
      /app/painel    Painel dos sensores (um card por nó)
      /              site público (nuvem/site-index.html)
```

| Camada | Arquivos | Onde roda |
|---|---|---|
| Entrada e banco | `run.py`, `init_db.py`, `app/db.py`, `app/auth.py` | notebook da casa |
| Recepção dos nós | `app/listener.py` | notebook da casa |
| Web local | `app/main.py`, `app/templates/*.html`, `app/static/painel.js`, `app/static/style.css` | notebook (servidor) / navegador |
| Monitor local | `app/static/monitor.html` | navegador (servido pelo notebook) |
| Ponte para a nuvem | `cloud_push.py`, `cloud.json` (não versionado) | notebook da casa |
| Inicialização | `iniciar.bat` | notebook da casa (Windows) |
| Nuvem | `nuvem/app.py`, `nuvem/templates/*.html`, `nuvem/static/monitor.html` (gerado) | T-140 / navegador |
| Infra da nuvem | `nuvem/sentinela-cloud.service`, `nuvem/nginx-app.conf`, `nuvem/fix_nginx.py` | T-140 |
| Site público | `nuvem/site-index.html` | nginx do T-140 |
| Build do Monitor da nuvem | `nuvem/build_monitor.py` | máquina de desenvolvimento |

### Pacotes lidos pelo listener

| Magic | Conteúdo | Uso |
|---|---|---|
| `0xC5110001` | frame CSI bruto (ADR-018) | nó online + RSSI |
| `0xC5110002` | vitais, 32 bytes | presença, queda, pessoas, respiração, cardíaco |
| `0xC5110003` | features, 48 bytes (ADR-069) | energia de movimento → movimento por software |
| `0xC5110004` | vitais fundidos (CSI + mmWave) | mesmos 32 bytes iniciais |

O byte `node_id` do pacote é ligado ao cadastro pelo número (`node-01` ↔ 1).
Pacotes de nós não cadastrados ou inativos são descartados. Um nó fica
offline após 10 s sem pacotes. Eventos vão para o banco **só em transições**
(online, offline, presença, ausência, movimento, queda).

## 3. Como rodar em casa

### Primeira vez

```bat
cd C:\Users\ferna\websentinela
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python init_db.py
```

O `init_db.py` cria `websentinela.db` com os três nós e o usuário `admin`, e
**imprime a senha provisória uma única vez**. Troque-a em `/usuarios` no
primeiro acesso. Rodar de novo não apaga nada; `init_db.py --reset-admin` gera
outra senha.

### Dia a dia: `iniciar.bat`

Duplo clique em `iniciar.bat`. Ele:

1. sobe o webSentinela (`run.py`) na porta 8080, escutando os nós em UDP 5005;
2. abre `http://localhost:8080/monitor` no navegador após 4 s;
3. abre, minimizada, a janela do `cloud_push.py`, que envia o estado para a
   nuvem a cada 2 s (precisa do token em `cloud.json`; o arquivo é criado na
   primeira execução para você preencher).

Fechar a janela principal derruba o sistema.

Endereços locais: `/login` → `/painel` (dispositivos e eventos, exige login),
`/usuarios` e `/dispositivos` (perfil ADM), `/monitor` (planta da casa, sem
login: só na rede local).

### Sem os nós (modo simulado)

```bash
python run.py --simulado      # gera pacotes sintéticos no formato real do firmware
python run.py --porta-udp 5006   # se o sensing server do RuView já usa a 5005
```

No WSL2 em modo NAT o tráfego da LAN não chega direto: use o `udp_relay.py`
do RuView no Windows apontando para o IP do WSL (`hostname -I`).

## 4. Como implantar o T-140 (resumo de `nuvem/LEIA-ME.md`)

No servidor (usuário `deploy`):

```bash
sudo mkdir -p /opt/sentinela-cloud && sudo chown deploy /opt/sentinela-cloud
cd /opt/sentinela-cloud && unzip -o /tmp/sentinela-cloud.zip     # conteúdo de nuvem/
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python app.py adduser fernando SUA_SENHA "Fernando"   # usuário da família
.venv/bin/python app.py token          # token: vai no cloud.json do notebook
sudo cp sentinela-cloud.service /etc/systemd/system/ && sudo systemctl daemon-reload
sudo systemctl enable --now sentinela-cloud
# nginx: cole nginx-app.conf dentro do server HTTPS de sites-available/sentineladavida
sudo nginx -t && sudo systemctl reload nginx
cp site-index.html /var/www/sentineladavida/html/index.html
```

`nuvem/static/monitor.html` é gerado por `nuvem/build_monitor.py` a partir do
artefato de design e precisa ser incluído no pacote enviado. No notebook,
coloque o token em `cloud.json` e rode `iniciar.bat`.

URLs: `https://www.sentineladavida.com.br/app/` (login),
`/app/monitor` (Sentinela Monitor), `/app/painel` (painel dos sensores).

## 5. Formato do JSON de `/api/state`

Gerado por `estado_monitor()` em `app/main.py`, lido pelo Monitor local a
cada 2 s e enviado pelo `cloud_push.py` ao T-140.

```json
{
  "ts": 1760450000.123,
  "now": 1760450001.456,
  "age": 1.333,
  "company": false,
  "nodes": {
    "node-01": {
      "presence": 1,
      "motion": 0,
      "fall": 0,
      "still_s": 42,
      "last_seen": 1760450000.123,
      "energy": 0.07,
      "baseline": 0.051,
      "people": 1
    },
    "node-02": { "presence": 0, "motion": 0, "fall": 0, "still_s": 0,
                 "last_seen": 0, "energy": 0.0, "baseline": 0.0, "people": 0 }
  }
}
```

| Campo | Significado |
|---|---|
| `ts` | epoch do último pacote recebido de qualquer nó (0 se nunca) |
| `now` | epoch do servidor no momento da resposta |
| `age` | `now - ts` em segundos (`null` se nunca chegou pacote) |
| `company` | modo acompanhada forçado (hoje sempre `false` no servidor local) |
| `nodes.<node_id>.presence` | bit 0 do flags do pacote de vitais (0/1) |
| `motion` | movimento calculado por software: `energy > max(3 × baseline, baseline + 0,02)` |
| `fall` | bit de queda com **latch de 30 s** (0/1) |
| `still_s` | segundos desde o último movimento (0 se em movimento) |
| `last_seen` | epoch do último pacote do nó (0 se nunca enviou) |
| `energy` | energia de movimento normalizada (`features[1]`), 2 casas |
| `baseline` | percentil 20 de `energy` nos últimos 60 s, 3 casas |
| `people` | `n_persons` do pacote de vitais |

Nós cadastrados que nunca enviaram nada aparecem com tudo zerado.

**Na nuvem:** o `cloud_push.py` envia `POST /app/api/state` com
`{"home": "...", "company": false, "nodes": {...}}` e o header
`X-Sentinela-Token`. O `GET /app/api/state` devolve o mesmo `nodes` mais `ts`
(hora em que a nuvem recebeu), `now`, `age`, `company` e `home`.

## 6. Glossário

- **CSI (Channel State Information)** — informação de estado do canal WiFi:
  amplitude e fase de cada subportadora entre o nó e o roteador. Corpos se
  movendo (e até o tórax respirando) alteram esse canal; é daí que o firmware
  extrai presença, movimento, respiração e queda. Nenhuma imagem é capturada.
- **Presença** — bit 0 do `flags` do pacote de vitais, calculado pelo firmware
  a partir do `presence_score`. No painel aparece com o número de pessoas.
- **Energia × ruído (baseline)** — `energy` é a energia de movimento
  normalizada (0–1) do pacote de features. `baseline` é o percentil 20 da
  energia dos últimos 60 s, ou seja, o "ruído" de fundo daquele nó. O servidor
  declara movimento quando a energia supera `max(3 × baseline, baseline + 0,02)`;
  o Monitor usa `energy > baseline + 0,12`. O bit de movimento do firmware é
  ignorado porque dispara com ruído.
- **Latch de queda** — o bit de queda do firmware fica ligado por um instante
  só. O servidor mantém `fall = 1` por 30 s após o último bit visto, para que o
  Monitor (que lê a cada 2 s) e a nuvem não percam o evento.
- **Modo Sozinha / Acompanhada / Ninguém em casa** — regra do Sentinela
  Monitor. *Sozinha*: uma pessoa detectada, vigilância ativa, alertas de
  imobilidade e queda. *Acompanhada*: `company = true` ou mais de uma pessoa
  detectada com movimento; os eventos são apenas registrados, sem alarme.
  *Ninguém em casa*: nenhum movimento em nenhum cômodo há 30 min.
- **Sem sinal** — cômodo cujo(s) sensor(es) não envia(m) pacote há mais de
  2 min (tomada, cabo ou WiFi).
- **Imóvel há muito tempo (atenção)** — pessoa parada há mais de 12 min fora
  da cama. Na cama, parada há mais de 3 min é tratada como "descansando".

## 7. Arquivos não versionados

`.venv/`, `websentinela.db`, `.secret_key` (chave do cookie), `cloud.json`
(token da nuvem), `rooms.json` (planta editada no Monitor), `nuvem/data/`
(usuários, estado e segredos da nuvem) e logs. Veja `.gitignore`.
