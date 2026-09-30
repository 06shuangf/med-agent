# -*- coding: utf-8 -*-
"""MedAgent Web 对话前端:纯标准库 http.server,零依赖。

启动:
    python -m medagent.webui            # 默认 http://127.0.0.1:8600
    python -m medagent.webui --port 9000

页面功能:
    - 对话框:输入症状/问题,选择智能体(协调/诊断/文献/病历)
    - 诊断可切换推理模式(react/cot/self_refine/tot)
    - 展示回答 + 执行轨迹摘要(步骤/工具/token/耗时/合规门禁)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .config import load_config
from .knowledge.kb import KnowledgeEngine, KNOWLEDGE_BASE_DEFAULT
from .tools import medical_tools
from .tools.registry import build_registry
from .agents.coordinator import CoordinatorAgent
from .agents.diagnosis import DiagnosisAgent
from .agents.literature import LiteratureAgent
from .agents.record_analyst import MedicalRecordAgent
from .runtime.trace import Trace
from .cli import make_db

_PAGE = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MedAgent 医疗智能体</title>
<style>
:root{--bg:#f5f7fa;--card:#fff;--ink:#1f2937;--sub:#6b7280;--acc:#0d9488;--acc2:#115e59;--err:#b91c1c;--warn:#b45309}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:"Segoe UI","Microsoft YaHei",system-ui,sans-serif;background:var(--bg);color:var(--ink);height:100vh;display:flex;flex-direction:column}
header{background:linear-gradient(135deg,var(--acc2),var(--acc));color:#fff;padding:14px 22px;display:flex;align-items:center;gap:12px}
header .logo{font-size:22px}header h1{font-size:17px;font-weight:600}
header .badge{margin-left:auto;font-size:12px;background:rgba(255,255,255,.18);padding:4px 10px;border-radius:999px}
main{flex:1;display:flex;overflow:hidden}
#chat{flex:1;display:flex;flex-direction:column;max-width:900px;margin:0 auto;width:100%}
#msgs{flex:1;overflow-y:auto;padding:20px;display:flex;flex-direction:column;gap:14px}
.msg{max-width:82%;padding:12px 15px;border-radius:14px;line-height:1.65;font-size:14.5px;white-space:pre-wrap;word-break:break-word;box-shadow:0 1px 2px rgba(0,0,0,.06)}
.msg.user{align-self:flex-end;background:var(--acc);color:#fff;border-bottom-right-radius:4px}
.msg.bot{align-self:flex-start;background:var(--card);border:1px solid #e5e7eb;border-bottom-left-radius:4px}
.msg.bot .meta{margin-top:10px;padding-top:8px;border-top:1px dashed #e5e7eb;font-size:12px;color:var(--sub)}
.msg.bot .meta b{color:var(--acc2)}
.msg.bot.blocked{border-color:#fca5a5;background:#fef2f2}
.hint{align-self:center;color:var(--sub);font-size:12.5px;background:#eef2f7;padding:6px 14px;border-radius:999px}
.typing{align-self:flex-start;color:var(--sub);font-size:13px;padding:8px 4px}
.typing span{animation:blink 1.2s infinite}
.typing span:nth-child(2){animation-delay:.2s}.typing span:nth-child(3){animation-delay:.4s}
@keyframes blink{0%,80%,100%{opacity:.2}40%{opacity:1}}
#panel{width:270px;background:var(--card);border-left:1px solid #e5e7eb;padding:16px;overflow-y:auto}
#panel h3{font-size:13px;color:var(--sub);margin:14px 0 8px;font-weight:600}
#panel h3:first-child{margin-top:0}
select,button{font:inherit}
select{width:100%;padding:8px 10px;border:1px solid #d1d5db;border-radius:8px;background:#fff;margin-bottom:4px}
.optnote{font-size:11.5px;color:var(--sub);margin:2px 0 10px;line-height:1.5}
label.chk{display:flex;gap:8px;align-items:center;font-size:13px;margin:8px 0;cursor:pointer}
.case{font-size:12.5px;color:var(--acc2);background:#f0fdfa;border:1px solid #ccfbf1;border-radius:8px;padding:8px 10px;margin:6px 0;cursor:pointer;line-height:1.5}
.case:hover{background:#ccfbf1}
footer{background:var(--card);border-top:1px solid #e5e7eb;padding:14px 20px}
#form{display:flex;gap:10px;max-width:900px;margin:0 auto}
#input{flex:1;padding:12px 15px;border:1.5px solid #d1d5db;border-radius:12px;font:inherit;font-size:14.5px;resize:none;height:52px;outline:none;transition:border .15s}
#input:focus{border-color:var(--acc)}
#send{padding:0 24px;background:var(--acc);color:#fff;border:none;border-radius:12px;font-size:15px;font-weight:600;cursor:pointer}
#send:disabled{background:#9ca3af;cursor:not-allowed}
#send.stop{background:var(--err)}
.warnbar{background:#fffbeb;border-bottom:1px solid #fde68a;color:#92400e;font-size:12.5px;padding:6px 20px;text-align:center}
@media(max-width:760px){#panel{display:none}.msg{max-width:95%}}
</style>
</head>
<body>
<header><span class="logo">🩺</span><h1>MedAgent 医疗智能体</h1><span class="badge" id="badge">glm-5.3</span></header>
<div class="warnbar">教学演示系统 · 不构成医疗建议 · 急症请立即拨打 120</div>
<main>
<div id="chat"><div id="msgs">
  <div class="hint">描述症状或提问,例如「空腹血糖 7.2 是糖尿病吗」;急症描述会自动触发就医引导</div>
</div></div>
<aside id="panel">
  <h3>智能体</h3>
  <select id="agent">
    <option value="coordinator">协调(自动路由,推荐)</option>
    <option value="diagnosis">诊断推理</option>
    <option value="literature">文献指南 RAG</option>
    <option value="record">病历分析</option>
  </select>
  <div class="optnote">协调 Agent 按任务关键词分诊,必要时并行多 Agent 并做合规终审</div>
  <h3>推理模式(诊断)</h3>
  <select id="mode">
    <option value="react">ReAct(默认)</option>
    <option value="cot">CoT 思维链</option>
    <option value="self_refine">Self-Refine 自我修订</option>
    <option value="tot">Tree of Thoughts</option>
  </select>
  <div class="optnote">四种推理算法同一任务可切换对比</div>
  <h3>轨迹显示</h3>
  <label class="chk"><input type="checkbox" id="showtrace"> 展开执行轨迹</label>
  <div class="optnote">工具调用 / 步数 / token / 合规门禁结论</div>
  <h3>示例</h3>
  <div class="case" onclick="use(this)">62岁男性突发压榨性胸痛40分钟<br>伴大汗放射左臂,怎么办?</div>
  <div class="case" onclick="use(this)">空腹血糖 7.4 / 糖化 6.9<br>能确定是糖尿病吗?</div>
  <div class="case" onclick="use(this)">长期吃华法林,医生开了克拉霉素<br>能一起用吗?</div>
  <div class="case" onclick="use(this)">孩子2岁发热38.9℃一天<br>精神还可以,怎么处理?</div>
  <div class="case" onclick="use(this)">高血压的诊断标准和分级是什么?</div>
</aside>
</main>
<footer><form id="form">
  <textarea id="input" placeholder="输入症状 / 检验结果 / 医学问题… (Enter 发送,Shift+Enter 换行)" rows="1"></textarea>
  <button id="send" type="submit">发送</button>
</form></footer>
<script>
const msgs=document.getElementById('msgs'),input=document.getElementById('input'),
      send=document.getElementById('send'),agentEl=document.getElementById('agent'),
      modeEl=document.getElementById('mode'),traceChk=document.getElementById('showtrace');
let busy=false,ctrl=null;
function esc(s){return s.replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
function use(el){input.value=el.innerText.trim();input.focus()}
function add(cls,html){const d=document.createElement('div');d.className='msg '+cls;d.innerHTML=html;msgs.appendChild(d);msgs.scrollTop=msgs.scrollHeight;return d}
function fmt(a,t){
  let h=esc(a.answer||a.error||'(空)');
  const m=[];
  if(t){m.push(`<b>${t.steps.length}</b> 步`);
        const tools=t.steps.filter(s=>s.type==='action');
        if(tools.length)m.push('工具: '+tools.map(s=>s.tool).join(', '));
        m.push(`<b>${(t.usage.total_tokens||0)}</b> tok`);
        m.push(`<b>${t.duration_s||'?'}</b> s`)}
  if(a.gate&&a.gate.violations&&a.gate.violations.length)
    m.push('门禁: '+a.gate.violations.map(v=>v.id).join(' '));
  let tr='';
  if(t&&traceChk.checked&&t.steps.length){
    tr='<div style="margin-top:10px;text-align:left;background:#f8fafc;border-radius:8px;padding:8px;font-size:12px;color:#475569">'
      +t.steps.map(s=>`#${s.index} ${s.type}${s.tool?'·'+s.tool:''} ${s.error?'⚠ '+esc(s.error):''}`).join('<br>')+'</div>';
    tr='<details style="margin-top:8px"><summary style="cursor:pointer;font-size:12px;color:#6b7280">执行轨迹</summary>'+tr+'</details>';
  }
  return h+(m.length?`<div class="meta">${m.join(' · ')}${a.status?' · 状态: '+a.status:''}</div>`:'')+tr;
}
async function ask(text){
  busy=true;send.disabled=true;send.textContent='思考中…';
  const tip=add('typing','<span>●</span><span>●</span><span>●</span> 智能体分析中,长任务约 1-3 分钟…');
  try{
    const r=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({task:text,agent:agentEl.value,mode:modeEl.value})});
    const a=await r.json();
    tip.remove();
    const d=add('bot'+(a.status==='blocked'?' blocked':''),fmt(a,a.trace));
  }catch(e){tip.remove();add('bot blocked','⚠ 请求失败: '+esc(e.message))}
  busy=false;send.disabled=false;send.textContent='发送';
}
document.getElementById('form').onsubmit=e=>{e.preventDefault();
  const t=input.value.trim();if(!t||busy)return;
  add('user',esc(t));input.value='';input.style.height='52px';ask(t)};
input.oninput=()=>{input.style.height='auto';input.style.height=Math.min(140,input.scrollHeight)+'px'};
input.onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();document.getElementById('form').requestSubmit()}};
fetch('/api/health').then(r=>r.json()).then(d=>{document.getElementById('badge').textContent=d.model||'MedAgent'});
</script>
</body></html>"""

_AGENTS: dict[str, object] = {}
_LOCK = threading.Lock()


def _build_agents():
    cfg = load_config()
    knowledge = KnowledgeEngine(cards=KNOWLEDGE_BASE_DEFAULT, top_k=cfg.rag_top_k)
    medical_tools.bind_guideline_engine(knowledge)
    registry = build_registry()
    db = make_db(cfg)
    _AGENTS.update({
        "coordinator": CoordinatorAgent(cfg, knowledge, registry),
        "diagnosis": DiagnosisAgent(cfg, registry, knowledge),
        "literature": LiteratureAgent(cfg, knowledge, database=db),
        "record": MedicalRecordAgent(cfg, knowledge),
    })
    _AGENTS["cfg"] = cfg


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # 安静模式
        pass

    def _json(self, code: int, obj) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            body = _PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/health":
            self._json(200, {"ok": True, "model": _AGENTS["cfg"].model})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if urllib.parse.urlparse(self.path).path != "/api/chat":
            self._json(404, {"error": "not found"})
            return
        try:
            n = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(n).decode("utf-8"))
            task = str(req.get("task", "")).strip()
            which = req.get("agent", "coordinator")
            mode = req.get("mode", "react")
            if not task:
                self._json(400, {"error": "task 为空"})
                return
            with _LOCK:  # Agent 非线程安全,串行处理
                agent = _AGENTS.get(which, _AGENTS["coordinator"])
                trace = Trace(task)
                if which == "diagnosis":
                    answer, trace = agent.run(task, trace, reasoning=mode)
                else:
                    answer, trace = agent.run(task, trace)
            def _ser_step(s):
                d = {"index": s.index, "type": s.type}
                if s.tool:
                    d["tool"] = s.tool
                if s.error:
                    d["error"] = s.error[:120]
                return d
            self._json(200, {
                "answer": trace.final_answer or answer,
                "status": trace.status,
                "steps": [_ser_step(s) for s in trace.steps],
                "usage": trace.usage or {},
                "duration_s": getattr(trace, "duration_s", None),
                "gate": trace.gate or {},
            })
        except Exception as e:
            self._json(500, {"error": f"{type(e).__name__}: {e}"})


def main() -> None:
    ap = argparse.ArgumentParser(prog="medagent-webui")
    ap.add_argument("--port", type=int, default=8600)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    _build_agents()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"MedAgent Web UI → http://{args.host}:{args.port}")
    print("Ctrl+C 停止")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
