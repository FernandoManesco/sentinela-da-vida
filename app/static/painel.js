// --------------------------------------------------------------------------
// app/static/painel.js — lógica do Painel dos dispositivos (tela /painel).
// Onde roda: navegador (servido pelo webSentinela do notebook em /static/).
// Conexões: lê o snapshot inicial embutido em painel.html (#dados-iniciais), abre o
//   WebSocket /ws de app/main.py e redesenha cards e eventos a cada mensagem.
//   Fechamento com código 1008 = sessão inválida: volta para /login.
// --------------------------------------------------------------------------
// Painel: renderiza o snapshot do servidor e se atualiza via WebSocket.
// Todo texto vindo do servidor entra por textContent (nunca innerHTML).
(function () {
  const cards = document.getElementById("cards");
  const eventos = document.getElementById("eventos");
  const conexao = document.getElementById("conexao");

  // Cria um elemento com classe e texto (textContent: seguro contra XSS).
  function el(tag, cls, texto) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (texto !== undefined && texto !== null) e.textContent = texto;
    return e;
  }

  // '2026-10-14T10:20:30' -> '2026-10-14 10:20:30'.
  function hora(iso) {
    return iso ? iso.replace("T", " ") : "—";
  }

  // Número com casas fixas, ou '—' se nulo.
  function num(v, casas) {
    return v === null || v === undefined ? "—" : Number(v).toFixed(casas);
  }

  // Acrescenta um par rótulo/valor numa lista de definição (dl).
  function linha(dl, rotulo, valor) {
    dl.append(el("dt", null, rotulo), el("dd", null, valor));
  }

  // Redesenha tudo a partir de um snapshot {dispositivos, eventos}: um card por nó
  // (online/offline, estado, RSSI, vitais) e a tabela dos últimos eventos.
  function renderizar(s) {
    cards.replaceChildren(...s.dispositivos.map((d) => {
      const c = el("article", "card " + (d.online ? "on" : "off"));
      const topo = el("div", "card-topo");
      topo.append(el("strong", null, d.node_id), el("span", "selo", d.online ? "online" : "offline"));
      c.append(topo, el("div", "comodo", d.comodo || "(cômodo não definido)"));
      const dl = el("dl");
      let presenca = "—";
      if (d.presenca === true) presenca = "presença" + (d.pessoas ? ` (${d.pessoas})` : "");
      else if (d.presenca === false) presenca = "vazio";
      linha(dl, "Estado", presenca + (d.movimento ? " · movimento" : ""));
      linha(dl, "RSSI", d.rssi === null ? "—" : d.rssi + " dBm");
      linha(dl, "Respiração", d.respiracao_bpm ? num(d.respiracao_bpm, 1) + " rpm" : "—");
      linha(dl, "Cardíaco", d.cardiaco_bpm ? num(d.cardiaco_bpm, 0) + " bpm" : "—");
      linha(dl, "Última leitura", hora(d.leitura_em));
      c.append(dl);
      return c;
    }));
    if (!s.dispositivos.length) cards.append(el("p", "nota", "Nenhum dispositivo ativo cadastrado."));

    eventos.replaceChildren(...s.eventos.map((e) => {
      const tr = el("tr", "ev-" + e.tipo);
      [hora(e.data_hora), e.node_id, e.comodo || "—", e.tipo, e.valor || ""].forEach((v) => tr.append(el("td", null, v)));
      return tr;
    }));
  }

  // Abre o WebSocket (ws ou wss conforme a página) e reconecta sozinho após 3 s.
  function conectar() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(proto + "//" + location.host + "/ws");
    ws.onopen = () => { conexao.textContent = "ao vivo"; conexao.className = "conexao viva"; };
    ws.onmessage = (m) => renderizar(JSON.parse(m.data));
    ws.onclose = (ev) => {
      if (ev.code === 1008) { location.href = "/login"; return; }
      conexao.textContent = "reconectando…"; conexao.className = "conexao";
      setTimeout(conectar, 3000);
    };
  }

  // Inicialização: desenha o snapshot embutido e liga o canal ao vivo.
  renderizar(JSON.parse(document.getElementById("dados-iniciais").textContent));
  conectar();
})();
