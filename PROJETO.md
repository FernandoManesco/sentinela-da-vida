# webSentinela — documentação do projeto

Painel web para monitorar os nós ESP32-C6 do RuView instalados na casa:
status online/offline, presença, movimento, queda e sinais vitais estimados
por WiFi, com histórico de eventos e cadastro de usuários e dispositivos.

> Guia rápido de instalação e execução: [README.md](README.md).
> Este documento descreve arquitetura, dados, segurança e limitações.

---

## 1. Visão geral

```
 ESP32-C6 (node-01..03)                       navegador
   │  UDP 5005                                    ▲
   │  frames ADR-018 / vitais                     │ HTTP 8080 + WebSocket /ws
   ▼                                              │
 [udp_relay.py no Windows] ──► WSL2 ──► webSentinela (FastAPI)
                                          ├─ listener UDP  → estado em memória
                                          ├─ eventos       → SQLite (websentinela.db)
                                          └─ hub WebSocket → painel ao vivo
```

| Item | Escolha |
|---|---|
| Backend | FastAPI 0.115.6 + Uvicorn 0.34.0 |
| Templates | Jinja2 3.1.5 (autoescape ligado) |
| Banco | SQLite, arquivo `websentinela.db` |
| Autenticação | cookie de sessão assinado (Starlette `SessionMiddleware`) + senha bcrypt |
| Tempo real | WebSocket `/ws`, snapshot no máximo 1×/s quando há mudança |
| Porta HTTP | 8080 |
| Porta UDP | 5005 (configurável) |

O projeto é independente do código do RuView: não importa nenhum módulo dele e
fica inteiro na pasta `websentinela/`.

## 2. Estrutura

```
websentinela/
├── init_db.py            # cria o banco e o seed (admin + 3 dispositivos)
├── run.py                # sobe o servidor; --simulado, --porta-udp, --porta
├── requirements.txt      # dependências com versão fixa
├── README.md             # como rodar
├── PROJETO.md            # este documento
├── .gitignore            # .venv, websentinela.db, .secret_key, __pycache__
└── app/
    ├── db.py             # conexão SQLite e esquema
    ├── auth.py           # bcrypt, chave de sessão, usuário da sessão
    ├── listener.py       # parser UDP, estado dos nós, eventos, simulador
    ├── main.py           # rotas, CRUD, WebSocket, ciclo de atualização
    ├── templates/        # base, login, painel, usuarios, dispositivos, erro
    └── static/           # painel.js, style.css (tema claro/escuro)
```

## 3. Banco de dados

Criado por `init_db.py` (script Python, sem SQL solto). Rodá-lo de novo não
apaga dados nem troca a senha do admin.

**USUARIO**

| campo | tipo | regra |
|---|---|---|
| id | INTEGER PK | autoincremento |
| nome | TEXT | obrigatório, até 80 caracteres |
| login | TEXT UNIQUE | `[a-z0-9._-]{3,32}` |
| senha_hash | TEXT | bcrypt (custo 12) |
| perfil | TEXT | `ADM` ou `APOIO` (CHECK) |
| ativo | INTEGER | 0 ou 1 |

**DISPOSITIVO**

| campo | tipo | regra |
|---|---|---|
| id | INTEGER PK | autoincremento |
| node_id | TEXT UNIQUE | `node-NN` |
| mac | TEXT UNIQUE | `aa:bb:cc:dd:ee:ff` |
| comodo | TEXT | até 60 caracteres |
| ativo | INTEGER | 0 ou 1 |

**EVENTO**

| campo | tipo | regra |
|---|---|---|
| id | INTEGER PK | autoincremento |
| dispositivo_id | INTEGER FK | `ON DELETE CASCADE` |
| tipo | TEXT | online, offline, presenca, ausencia, movimento, queda |
| valor | TEXT | detalhe (score, energia, IP de origem…) |
| data_hora | TEXT | ISO 8601, hora local |

**Seed inicial**

| node_id | MAC |
|---|---|
| node-01 | e8:f6:0a:f8:71:fc |
| node-02 | 9c:cc:01:40:64:a0 |
| node-03 | e8:f6:0a:f8:c9:88 |

Usuário `admin` (perfil ADM) com senha provisória aleatória, impressa uma única
vez no console pelo `init_db.py`. `init_db.py --reset-admin` gera outra.

## 4. Telas e rotas

| Rota | Método | Acesso | Função |
|---|---|---|---|
| `/` | GET | — | redireciona para `/painel` ou `/login` |
| `/login` | GET/POST | público | formulário; sucesso → `/painel` |
| `/logout` | POST | logado | encerra a sessão |
| `/painel` | GET | logado | cards por dispositivo + últimos 25 eventos |
| `/ws` | WebSocket | logado, mesma origem | atualizações ao vivo do painel |
| `/usuarios` | GET/POST | ADM | listar e criar usuários |
| `/usuarios/{id}` | POST | ADM | editar nome, perfil, ativo, nova senha |
| `/usuarios/{id}/excluir` | POST | ADM | excluir (não o próprio) |
| `/dispositivos` | GET/POST | ADM | listar e criar dispositivos |
| `/dispositivos/{id}` | POST | ADM | editar node_id, MAC, cômodo, ativo |
| `/dispositivos/{id}/excluir` | POST | ADM | excluir dispositivo e seus eventos |
| `/monitor` | GET | rede local, sem login | Sentinela Monitor (`app/static/monitor.html`), planta da casa ao vivo |
| `/api/state` | GET | rede local, sem login | estado dos nós para o monitor (polling a cada 2 s) |
| `/api/rooms` | GET/PUT | rede local, sem login | planta da casa em `rooms.json` na raiz (`[]` se não existir) |

**Sentinela Monitor:** `/api/state` devolve `ts` (epoch do último pacote de
qualquer nó), `now`, `age`, `company` e um item por dispositivo ativo, chaveado
pelo `node_id`:

| campo | origem |
|---|---|
| `presence` | bit 0 do flags dos pacotes `0xC5110002`/`0xC5110004` |
| `fall` | bit 1 do flags, com latch de 30 s (o bit do firmware é transitório) |
| `people` | `n_persons` do pacote de vitais |
| `energy` | `features[1]` (energia de movimento normalizada) do pacote `0xC5110003`, 2 casas |
| `baseline` | percentil 20 de `energy` nos últimos 60 s, 3 casas |
| `motion` | calculado por software: `energy > max(3 × baseline, baseline + 0,02)`. O bit 2 do flags é ignorado (é só `energia > 0,01` e dispara com ruído). O painel `/painel` e o evento `movimento` usam o mesmo cálculo |
| `still_s` | segundos desde o último `motion = 1` (0 se em movimento; desde o primeiro pacote do nó se nunca se moveu) |
| `last_seen` | epoch do último pacote do nó (0 se nunca enviou nada) |

Essas três rotas não exigem login — se o painel sair da rede local, proteja-as
com `exigir()`.

**Card do painel:** node_id, cômodo, online/offline, estado (presença com
número de pessoas, vazio, movimento), RSSI, respiração, frequência cardíaca e
horário da última leitura.

## 5. Fluxo de dados

### 5.1 Pacotes lidos

Formato definido no firmware (`firmware/esp32-csi-node/main/`):

| Magic | Origem | Campos usados |
|---|---|---|
| `0xC5110001` | `csi_collector.h` — frame CSI bruto (ADR-018) | `node_id` (byte 4), RSSI i8 (byte 16) |
| `0xC5110002` | `edge_processing.h` — `edge_vitals_pkt_t`, 32 bytes | flags (presença/queda; o bit de movimento é ignorado), respiração ×100, cardíaco ×10000, RSSI, pessoas, energia de movimento, score de presença |
| `0xC5110003` | `edge_processing.h` — `edge_feature_pkt_t`, 48 bytes (ADR-069) | `features[1]` = energia de movimento normalizada; alimenta `energy`, `baseline` e o movimento (painel, evento e `/api/state`) |
| `0xC5110004` | `edge_processing.h` — vitais fundidos CSI+mmWave | mesmos 32 bytes iniciais |
| outros `0xC511xxxx` | estado, mesh, WASM | contam só como "nó vivo" |

Pacotes curtos, com magic desconhecido ou de nós não cadastrados/inativos são
descartados. Uma exceção ao processar um pacote é registrada no log e não
derruba o listener.

### 5.2 Ligação pacote → dispositivo

O byte `node_id` do pacote é o número provisionado na NVS do ESP32. Ele é
ligado ao cadastro pelo número: `node-01` ↔ 1, `node-02` ↔ 2. O cadastro é
relido no máximo a cada 5 s (cache), e imediatamente após qualquer alteração
em `/dispositivos`.

### 5.3 Estado e eventos

- O estado de cada nó fica **em memória** (não vai para o banco a cada pacote).
- Eventos são gravados **só em transições**: `online` (primeiro pacote após
  ausência), `offline` (10 s sem pacotes), `presenca`/`ausencia` (mudança no
  bit de presença), `movimento` (início de movimento por software: energia das
  features acima do baseline — ver seção 4), `queda` (bit de queda).
- Um ciclo de 1 s verifica nós offline e, se algo mudou, envia um snapshot
  completo a todos os navegadores conectados.

### 5.4 Modo simulado

`run.py --simulado` não abre a porta UDP. Gera pacotes de vitais **no formato
real do firmware** para node-01..03 e os passa pelo mesmo parser, o que
exercita o caminho completo. O node-03 fica mudo ~20 s a cada 90 s para
mostrar o estado offline. Uma faixa amarela no topo de todas as telas indica
**MODO SIMULADO — dados SYNTHETIC**.

## 6. Segurança

| Aspecto | Tratamento |
|---|---|
| Senhas | bcrypt; mínimo de 8 caracteres; nunca armazenadas em texto |
| Sessão | cookie `websentinela` assinado, `SameSite=Strict`, validade 8 h |
| Chave da sessão | `WEBSENTINELA_SECRET` ou `.secret_key` gerado no primeiro uso (permissão 600, gitignored) |
| Usuário desativado | perde o acesso na próxima requisição (usuário relido a cada request) |
| Perfis | rotas ADM checadas no servidor, inclusive nos POSTs (não só no menu) |
| Último ADM | não é possível rebaixar, desativar ou excluir o último ADM ativo |
| Login | mensagem genérica; tempo equivalente para login inexistente; atraso de 0,5 s em falha |
| XSS | autoescape do Jinja2; painel monta o DOM só com `textContent` |
| WebSocket | exige sessão válida e mesma origem (bloqueia sequestro entre sites) |
| SQL | apenas consultas parametrizadas |
| Docs automáticas | `/docs`, `/redoc` e `/openapi.json` desligados |

## 7. Configuração

| Argumento de `run.py` | Variável de ambiente | Padrão |
|---|---|---|
| `--simulado` | `WEBSENTINELA_SIMULADO=1` | desligado |
| `--porta-udp` | `WEBSENTINELA_UDP_PORTA` | 5005 |
| `--host-udp` | `WEBSENTINELA_UDP_HOST` | 0.0.0.0 |
| `--porta` | — | 8080 |
| `--host` | — | 0.0.0.0 |
| — | `WEBSENTINELA_DB` | `websentinela/websentinela.db` |
| — | `WEBSENTINELA_SECRET` | conteúdo de `.secret_key` |

## 8. Testes realizados

Executados contra **cópias** do banco (o `websentinela.db` real contém só o
seed), em 2026-10-08.

**Modo simulado — 19 checagens, todas aprovadas:** redirecionamento sem login;
senha errada → 401; login do admin; painel com os 3 nós; edição de cômodo com
HTML escapado; validação de node_id/MAC; criação de usuário APOIO; login
duplicado recusado; proteção do último ADM; APOIO bloqueado em `/usuarios` e
no POST de `/dispositivos`; menu ADM oculto para APOIO; WebSocket recusado sem
sessão e de outra origem; snapshot inicial; painel ao vivo com eventos; logout.

**Modo UDP — aprovado:** pacotes montados no layout exato do firmware
enviados para uma porta de teste. node-02 decodificado corretamente (online,
presença, movimento, RSSI, respiração 14,5 rpm, cardíaco 72 bpm); pacote de
lixo e node 42 (não cadastrado) descartados; evento `offline` gravado após
10 s sem pacotes.

**Ainda não validado:** recepção de frames de um ESP32 real. Os testes
validam o parser contra o layout documentado no código do firmware, não
contra hardware.

## 9. Limitações e cuidados

- **Não é sensoriamento de grau câmera nem clínico.** Presença, queda,
  respiração e frequência cardíaca são estimativas do firmware a partir de
  WiFi CSI; não substituem equipamento médico nem alarme certificado.
- **Conflito na porta 5005.** É a mesma porta para a qual os nós enviam ao
  sensing server do RuView. Os dois não recebem o mesmo tráfego unicast na
  mesma máquina: rode só um, ou use `--porta-udp 5006` e aponte o relay/nós
  para ela.
- **WSL2 em modo NAT** não recebe tráfego da LAN diretamente: use
  `udp_relay.py` no Windows apontando para o IP do WSL (`hostname -I`, muda a
  cada `wsl --shutdown`).
- **Sem limite de tentativas de login** além do atraso e do custo do bcrypt.
  Adequado para a rede local; não exponha o painel na internet sem HTTPS e
  rate limiting.
- **HTTP sem TLS.** O cookie não tem a flag `Secure`; para acesso fora da LAN
  use um proxy reverso com HTTPS.
- **Estado em memória.** Ao reiniciar o servidor, os nós aparecem offline até
  o próximo pacote; o histórico de eventos permanece no banco.
- **Sem retenção de eventos.** A tabela EVENTO cresce indefinidamente; como só
  transições são gravadas, o volume é baixo, mas uma limpeza periódica pode
  ser necessária no futuro.
- **Cômodos vazios no seed.** Preencher em `/dispositivos`.

## 10. Firmware dos nós (contexto)

Os nós usam o firmware `firmware/esp32-csi-node` do RuView, compilado
localmente para ESP32-C6 com 16 MB de flash:

| Item | Valor |
|---|---|
| Toolchain | ESP-IDF v5.5.2 em `~/esp/esp-idf` (build local, não release oficial) |
| Overlays | `sdkconfig.defaults;sdkconfig.defaults.esp32c6;sdkconfig.defaults.16mb` — **nesta ordem** (o último vence) |
| Firmware | versão 0.8.12, commit `33a9e90` |
| Partições | `partitions_16mb.csv`: ota_0/ota_1 de 4 MB, coredump 64 KB, storage FAT 8000 KB |
| Recursos | rollback de OTA e coredump em flash ativos |
| Observação | `CONFIG_ESP_WIFI_DYNAMIC_TX_BUFFER_NUM=64` (padrão do repositório) |
| Pacote | `C:\Users\ferna\ruview-fw\c6-16mb\` com binários, `FLASHING.md` e `SHA256SUMS.txt` |

Offsets de gravação: `0x0` bootloader, `0x8000` tabela de partições, `0xf000`
otadata, `0x20000` aplicação. A NVS (`0x9000`) não é regravada, então SSID,
senha e `node_id` provisionados são preservados.

## 11. Próximos passos sugeridos

1. Validar com um nó real transmitindo e registrar o log como evidência.
2. Definir os cômodos de node-01..03.
3. Trocar a senha provisória do admin e criar os usuários de APOIO.
4. Decidir a convivência com o sensing server (porta única ou portas separadas).
5. Se o painel sair da LAN: HTTPS via proxy reverso e limite de tentativas de
   login.
