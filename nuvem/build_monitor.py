# ---------------------------------------------------------------------------
# nuvem/build_monitor.py — gera nuvem/static/monitor.html a partir do artefato de design
#   sentinela-blocos.html (mesma pasta), acrescentando o modo AO VIVO, o seletor de fonte
#   e os caminhos /app/.
# Onde roda: máquina de desenvolvimento (notebook), antes de empacotar a nuvem para o
#   T-140. Não roda em produção.
# Conexões: a saída é servida por nuvem/app.py em /app/monitor e consome /app/api/state e
#   /app/api/rooms. app/static/monitor.html (versão local) é a mesma página com os
#   caminhos sem /app.
# Cada rep() exige que o trecho exista exatamente N vezes: se o artefato mudar, o build
#   falha em vez de gerar uma página quebrada.
# ---------------------------------------------------------------------------
"""Gera static/monitor.html a partir do artefato sentinela-blocos.html (adiciona modo AO VIVO)."""
import re, pathlib
# Entrada (artefato de design) e saída (página final).
src = pathlib.Path(__file__).parent / "sentinela-blocos.html"
out = pathlib.Path(__file__).parent / "static" / "monitor.html"
h = src.read_text()

# Substituição com verificação de contagem: protege contra o artefato ter mudado.
def rep(a, b, n=1):
    global h
    assert h.count(a) == n, f"esperava {n}x: {a[:60]}"
    h = h.replace(a, b)

# 1. tabs: badge de fonte de dados + sair
rep('<button role="tab" id="tab-edit" aria-selected="false">Ambiente</button></div>',
    '<button role="tab" id="tab-edit" aria-selected="false">Ambiente</button></div>'
    '<div class="src" role="group"><button id="src-live" aria-pressed="true">Ao vivo</button><button id="src-sim" aria-pressed="false">Simulação</button></div>'
    '<div class="live" id="live" data-s="wait"><i></i><span id="live-t">conectando…</span></div>'
    '<a class="out" href="/app/painel">painel</a><a class="out" href="/app/logout">sair</a>')
rep('</style>', '''
.live{display:inline-flex;align-items:center;gap:7px;font:500 11px/1 "IBM Plex Mono",monospace;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);margin-left:auto;padding:6px 10px;border:1px solid var(--line);border-radius:999px}
.live i{width:7px;height:7px;border-radius:50%;background:var(--muted)}
.live[data-s="ok"] i{background:var(--ok);box-shadow:0 0 6px var(--ok)}
.live[data-s="old"] i{background:var(--warn)} .live[data-s="off"] i{background:var(--crit)}
.live[data-s="sim"] i{background:var(--accent)}
a.out{font:500 12px "IBM Plex Sans",sans-serif;color:var(--muted);text-decoration:none;margin-left:10px}
.src{display:inline-flex;margin-left:auto;border:1px solid var(--line);border-radius:999px;overflow:hidden}
.src button{border:0;background:transparent;color:var(--muted);font:600 12px "IBM Plex Sans",sans-serif;padding:7px 12px;cursor:pointer}
.src button[aria-pressed="true"]{background:var(--accent);color:#fff}
.live{margin-left:0}
body[data-src="live"] section.demo{display:none}
</style>''')

# 2. cômodos: carregar/salvar no servidor (mantém localStorage como cache)
rep('function save(){ try{ localStorage.setItem("sentinela-rooms-v3",JSON.stringify(rooms)); }catch(e){} }',
    'function save(){ try{ localStorage.setItem("sentinela-rooms-v3",JSON.stringify(rooms)); }catch(e){} '
    'fetch("/app/api/rooms",{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify(rooms)}).catch(()=>{}); }')

# 3. estado "sem sinal"
rep('fall:["fall","queda","queda detectada · no chão · alerta enviado"]};',
    'fall:["fall","queda","queda detectada · no chão · alerta enviado"],nosignal:["warn","sem sinal","sensor não envia leituras há mais de 2 min · verificar tomada/cabo"]};')
rep('const t=s.kind==="empty"?"vazio":s.kind==="fall"?"queda":s.kind==="warn"?"atenção":(s.people>1?s.people+" pessoas":"ocupado");',
    'const t=s.kind==="empty"?"vazio":s.kind==="nosignal"?"sem sinal":s.kind==="fall"?"queda":s.kind==="warn"?"atenção":(s.people>1?s.people+" pessoas":"ocupado");')
rep('const k=s.kind==="empty"?"empty":s.kind==="fall"?"fall":s.kind==="warn"?"warn":"ok";',
    'const k=s.kind==="empty"?"empty":s.kind==="fall"?"fall":(s.kind==="warn"||s.kind==="nosignal")?"warn":"ok";')
rep('modeN.textContent=`${rooms.length} cômodos · ${rooms.length} sensores online`;',
    'const on=rooms.filter(r=>sense[r.id].kind!=="nosignal").length; modeN.textContent=`${rooms.length} cômodos · ${on} sensores online`;')

# 4. bloco AO VIVO
# Código JS do modo ao vivo, inserido antes do fechamento da IIFE (é o mesmo bloco
# que existe em app/static/monitor.html, com os caminhos /app/api/...).
live = r'''
  /* ---------- AO VIVO: estado real vindo da casa (webSentinela -> T-140) ---------- */
  const liveEl=document.getElementById("live"), liveT=document.getElementById("live-t");
  let liveOn=false, lastSig="";
  function setLive(s,t){ liveEl.dataset.s=s; liveT.textContent=t; }
  // simulação só enquanto não há dados reais; ao clicar num botão de demo, volta à simulação por 60 s
  let srcMode="live"; try{ srcMode=localStorage.getItem("sentinela-src")||"live"; }catch(e){}
  function setSrc(m){ srcMode=m; document.body.dataset.src=m; document.getElementById("src-live").setAttribute("aria-pressed",m==="live"); document.getElementById("src-sim").setAttribute("aria-pressed",m==="sim");
    try{ localStorage.setItem("sentinela-src",m); }catch(e){} if(m==="sim"){ setLive("sim","simulação — dados reais ignorados"); note("Modo simulação.",true); } else { lastSig=""; note("Modo ao vivo.",true); poll(); } }
  document.getElementById("src-live").onclick=()=>setSrc("live"); document.getElementById("src-sim").onclick=()=>setSrc("sim");
  const simUntil=0;
  // regra de casa de uma pessoa: movimento = energia acima do ruído do nó; a pessoa está no cômodo
  // com movimento mais recente; parou = continua lá, parada; sem movimento em lugar nenhum por 30 min = ninguém
  const MOV_DELTA=0.12, STILL_WARN=720, NOBODY=1800, BED_AFTER=180, SIT_AFTER=60;
  const lastMove={};  // id do cômodo -> epoch (s) do último movimento visto
  let lastFocusRoom=null;
  function applyLive(st){
    if(srcMode!=="live") return;
    const nodes=st.nodes||{}, now=st.now||Date.now()/1000;
    let sig=[], anyMotion=null, fallRoom=null, companyNow=false;
    rooms.forEach(r=>{ const s=sense[r.id];
      const live=nodesOf(r).map(id=>nodes[id]).filter(n=>n&&(now-(n.last_seen||0))<=120);
      if(!live.length){ s.people=0; s.kind="nosignal"; s.furn=null; sig.push(r.id+s.kind); return; }
      // nó mais forte (maior energia acima do ruído); queda se qualquer um acusar
      const n=live.reduce((a,b)=>(((b.energy||0)-(b.baseline||0))>((a.energy||0)-(a.baseline||0))?b:a)); n.fall=live.some(x=>x.fall)?1:0;
      const moving = n.fall ? true : ((n.energy||0) > (n.baseline||0)+MOV_DELTA);
      if(moving) lastMove[r.id]=now;
      if(n.fall) fallRoom=r.id;
      if(moving&&(!anyMotion||lastMove[r.id]>=lastMove[anyMotion])) anyMotion=r.id;
      if((n.people||0)>1&&moving) companyNow=true;
      s.people=0; s.kind="empty"; s.furn=null; });
    // onde está a pessoa: último cômodo com movimento (dentro de 30 min)
    let cur=null, curT=0; rooms.forEach(r=>{ if(lastMove[r.id]&&lastMove[r.id]>curT){ cur=r.id; curT=lastMove[r.id]; } });
    if(cur&&now-curT>NOBODY) cur=null;
    companyMode=!!st.company||companyNow;
    if(cur){ const r=rooms.find(x=>x.id===cur), s=sense[cur], stillS=now-curT;
      s.people=1;
      if(fallRoom===cur){ s.kind="fall"; }
      else if(anyMotion===cur){ s.kind="walk"; }
      else { const fo=(a)=>{const i=furnOf(r,a); return i<0?null:i;}, bedI=fo(["bed","bed1"]), sitI=fo(["sofa2","sofa3","armchair","chair","tround","tsquare","trect"]);
        if(bedI!=null&&stillS>BED_AFTER){ s.kind="still"; s.furn=bedI; }
        else if(stillS>STILL_WARN){ s.kind="warn"; }
        else if(sitI!=null&&stillS>SIT_AFTER){ s.kind="still"; s.furn=sitI; }
        else s.kind="still"; }
      focused=cur; }
    const S=rooms.map(r=>r.id+sense[r.id].kind+sense[r.id].people+sense[r.id].furn).join("|");
    if(S!==lastSig){ lastSig=S; renderStage(); rooms.forEach(r=>{ const s=sense[r.id]; if(s.kind==="fall") note(`QUEDA: ${r.name}. Alerta enviado.`,true); else if(s.kind==="warn") note(`Imóvel há ${Math.round((now-curT)/60)} min: ${r.name}. Verificar.`,true); else if(s.kind==="walk"&&lastFocusRoom!==r.id) note(`Movimento: ${r.name}.`); }); lastFocusRoom=cur; }
    else renderList();
    // detalhe técnico no rodapé do cômodo em foco: energia × ruído do nó
    const fr=rooms.find(x=>x.id===focused); const fn=fr&&nodesOf(fr).map(id=>nodes[id]).filter(Boolean).sort((a,b)=>((b.energy||0)-(b.baseline||0))-((a.energy||0)-(a.baseline||0)))[0]; if(fn&&fn.energy!=null){ const d=stageStatus.querySelector(".detail"); d.textContent=d.textContent.replace(/ · energia .*$/,"")+` · energia ${fn.energy.toFixed(2)} / ruído ${(fn.baseline||0).toFixed(2)}`; }
  }
  async function poll(){
    try{ const res=await fetch("/app/api/state",{cache:"no-store"}); if(res.status===401){ location.href="/app/login"; return; }
      const st=JSON.parse((await res.text()).replace(/\b(NaN|-?Infinity)\b/g,"0")); const age=st.age!=null?st.age:9999;
      if(!st.ts){ setLive("wait","aguardando a casa"); }
      else if(srcMode!=="live"){ /* simulação: ignora */ }
      else if(age>120){ setLive("off",`casa sem envio há ${Math.round(age/60)} min`); rooms.forEach(r=>{ sense[r.id]={people:0,kind:"nosignal",furn:null}; }); renderStage(); }
      else { setLive(age>15?"old":"ok", age>15?`atraso ${Math.round(age)} s`:"ao vivo"); applyLive(st); }
    }catch(e){ setLive("off","sem conexão"); }
  }
  (async()=>{ try{ const r=await fetch("/app/api/rooms"); const j=await r.json(); if(Array.isArray(j)&&j.length){ rooms=j; rooms.forEach(x=>{ if(!sense[x.id]) sense[x.id]={people:0,kind:"empty",furn:null}; }); renderStage(); } }catch(e){}
    setSrc(srcMode); setInterval(poll,2000); })();
</script>'''
# Injeta o bloco ao vivo dentro da IIFE e grava a página.
i=h.rfind('})();\n</script>'); assert i>0; h=h[:i]+live.replace('</script>','')+'})();\n</script>'+h[i+len('})();\n</script>'):]
out.write_text(h); print("ok", out, len(h))
