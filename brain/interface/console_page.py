"""The operator console — the AIOS desk, served by the brain itself.

GET /console returns this single self-contained page (no build step, no external
assets, stdlib-served). The page is a static shell with zero secrets: every data
call goes same-origin to the existing /v1 endpoints with the bearer token the
operator pastes in (kept in localStorage on their machine, never served back).

Same-origin by construction, so the CORS allowlist never has to widen for it.
All candidate/approval content renders via textContent — scraped or proposed
text can never execute in the operator's browser.
"""

CONSOLE_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Prepende Console</title>
<style>
  :root{--ground:#0e1420;--panel:#151d2c;--panel2:#1a2334;--line:#263149;
    --ink:#dbe2f0;--muted:#8791a5;--amber:#e8b45a;--live:#4fae6e;
    --warn:#d98e4a;--red:#d1605c;
    --mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace;
    --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
    --serif:"Iowan Old Style",Georgia,serif}
  *{box-sizing:border-box}
  body{background:var(--ground);color:var(--ink);font-family:var(--sans);
    margin:0;padding:0 20px 60px;line-height:1.5}
  .wrap{max-width:980px;margin:0 auto}
  header{display:flex;justify-content:space-between;align-items:baseline;gap:16px;
    flex-wrap:wrap;padding:26px 0 14px;border-bottom:1px solid var(--line)}
  h1{font-family:var(--serif);font-size:26px;font-weight:600;margin:0}
  h1 span{color:var(--amber)}
  #health{font-family:var(--mono);font-size:12px;color:var(--muted)}
  #health b{color:var(--live);font-weight:600}
  #health b.bad{color:var(--red)}
  section{margin-top:28px}
  h2{font-family:var(--serif);font-size:19px;font-weight:600;margin:0 0 3px}
  .sub{color:var(--muted);font-size:13px;margin:0 0 12px}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:14px 16px}
  label{font-family:var(--mono);font-size:11px;letter-spacing:.1em;
    text-transform:uppercase;color:var(--muted);display:block;margin-bottom:4px}
  input,textarea{width:100%;background:var(--panel2);border:1px solid var(--line);
    border-radius:4px;color:var(--ink);font-family:var(--mono);font-size:13px;
    padding:8px 10px}
  input:focus,textarea:focus,button:focus{outline:2px solid var(--amber);outline-offset:1px}
  .row{display:flex;gap:12px;flex-wrap:wrap;align-items:end}
  .row>div{flex:1;min-width:180px}
  button{background:var(--panel2);border:1px solid var(--line);border-radius:4px;
    color:var(--ink);font-family:var(--mono);font-size:12px;padding:7px 14px;
    cursor:pointer;white-space:nowrap}
  button:hover{border-color:var(--amber)}
  button.approve{border-color:var(--live);color:var(--live)}
  button.reject{border-color:var(--red);color:var(--red)}
  button.defer{color:var(--muted)}
  button.primary{border-color:var(--amber);color:var(--amber)}
  button:disabled{opacity:.45;cursor:default}
  .item{border-top:1px solid var(--line);padding:12px 0}
  .item:first-child{border-top:none}
  .item .meta{font-family:var(--mono);font-size:11px;color:var(--muted);margin-bottom:6px}
  .item .content{font-size:13.5px;white-space:pre-wrap;word-break:break-word;
    max-height:130px;overflow-y:auto;color:var(--ink)}
  .item .acts{display:flex;gap:8px;margin-top:8px;flex-wrap:wrap;align-items:center}
  .item .result{font-family:var(--mono);font-size:11.5px;color:var(--muted)}
  .item .result.ok{color:var(--live)}
  .item .result.err{color:var(--red)}
  .queuebar{display:flex;justify-content:space-between;align-items:center;gap:12px;
    flex-wrap:wrap;margin-bottom:10px}
  .count{font-family:var(--mono);font-size:12px;color:var(--amber)}
  .danger-note{font-size:12px;color:var(--warn)}
  #reply{white-space:pre-wrap;font-size:13.5px;margin-top:10px;border-top:1px solid var(--line);padding-top:10px}
  #reply .receipt{font-family:var(--mono);font-size:11px;color:var(--muted);margin-top:8px}
  .empty{color:var(--muted);font-size:13px;font-style:italic}
</style>
</head>
<body>
<div class="wrap">
<header>
  <h1>Prepende <span>Console</span></h1>
  <div id="health">not connected</div>
</header>

<section>
  <h2>Connect</h2>
  <p class="sub">Token and tenant stay in this browser's localStorage — the server never echoes them.</p>
  <div class="card"><div class="row">
    <div><label for="tok">bearer token</label><input id="tok" type="password" autocomplete="off"></div>
    <div><label for="ten">tenant scope</label><input id="ten" value="default"></div>
    <div style="flex:0"><button class="primary" id="connect">Connect</button></div>
  </div></div>
</section>

<section>
  <h2>Memory candidates</h2>
  <p class="sub">Approve is the only door to durable memory: the fact joins recall for this tenant, permanently. Reject keeps the audit row, writes nothing.</p>
  <div class="card">
    <div class="queuebar">
      <span class="count" id="candcount">–</span>
      <span style="display:flex;gap:8px;flex-wrap:wrap">
        <button id="refreshcands">Refresh</button>
        <button class="reject" id="rejectall">Reject all shown…</button>
      </span>
    </div>
    <div id="cands"><p class="empty">Connect to load the queue.</p></div>
  </div>
</section>

<section>
  <h2>Action approvals</h2>
  <p class="sub danger-note">Approving here EXECUTES that one external action live (webhook fires). Reject persists the refusal.</p>
  <div class="card">
    <div class="queuebar">
      <span class="count" id="appcount">–</span>
      <button id="refreshapps">Refresh</button>
    </div>
    <div id="apps"><p class="empty">Connect to load the lane.</p></div>
  </div>
</section>

<section>
  <h2>Ask the brain</h2>
  <p class="sub">Same /v1/chat lane as every product surface — memory-grounded, action requests refused into the approval lane.</p>
  <div class="card">
    <textarea id="q" rows="3" placeholder="Ask, or give it a goal…"></textarea>
    <div style="margin-top:8px;display:flex;gap:8px;align-items:center">
      <button class="primary" id="ask">Ask</button>
      <span class="result" id="askstate"></span>
    </div>
    <div id="reply" hidden></div>
  </div>
</section>
</div>

<script>
(() => {
  const $ = id => document.getElementById(id);
  const store = window.localStorage;
  $("tok").value = store.getItem("prepende_console_token") || store.getItem("engram_console_token") || "";
  $("ten").value = store.getItem("prepende_console_tenant") || store.getItem("engram_console_tenant") || "default";

  function headers() {
    return { "Content-Type": "application/json",
      "Authorization": "Bearer " + $("tok").value.trim(),
      "X-Tenant": $("ten").value.trim() };
  }
  async function api(method, path, body) {
    const r = await fetch(path, { method, headers: headers(),
      body: body ? JSON.stringify(body) : undefined });
    let j = null; try { j = await r.json(); } catch (e) {}
    return { status: r.status, body: j };
  }

  async function health() {
    const h = $("health");
    const r = await api("GET", "/v1/health");
    if (r.status === 200 && r.body && r.body.ok) {
      h.innerHTML = "";
      const b = document.createElement("b");
      b.textContent = "online";
      h.append("brain ", b, " · model " + r.body.model + " · tenant " + r.body.tenantId +
               " · " + r.body.runtime);
    } else {
      h.innerHTML = "";
      const b = document.createElement("b"); b.className = "bad";
      b.textContent = r.status === 401 ? "unauthorized" : "unreachable";
      h.append("brain ", b);
    }
  }

  function itemNode(meta, content) {
    const div = document.createElement("div"); div.className = "item";
    const m = document.createElement("div"); m.className = "meta"; m.textContent = meta;
    const c = document.createElement("div"); c.className = "content"; c.textContent = content;
    const acts = document.createElement("div"); acts.className = "acts";
    div.append(m, c, acts);
    return { div, acts };
  }
  function actBtn(label, cls, fn) {
    const b = document.createElement("button"); b.className = cls; b.textContent = label;
    b.addEventListener("click", fn); return b;
  }
  function resultSpan(acts) {
    let s = acts.querySelector(".result");
    if (!s) { s = document.createElement("span"); s.className = "result"; acts.append(s); }
    return s;
  }

  async function decideCandidate(id, decision, acts) {
    const s = resultSpan(acts);
    s.className = "result"; s.textContent = "…";
    const r = await api("POST", "/v1/memory/candidates/" + id, { decision });
    if (r.status === 200 && r.body.ok) {
      s.className = "result ok";
      s.textContent = decision === "approve"
        ? "approved -> memory " + (r.body.memoryId || "") : decision + "ed";
      acts.querySelectorAll("button").forEach(b => b.disabled = true);
    } else {
      s.className = "result err";
      s.textContent = (r.body && r.body.error) || ("HTTP " + r.status);
    }
  }

  let shownCandidates = [];
  async function loadCandidates() {
    const box = $("cands"); box.textContent = "loading…";
    const r = await api("GET", "/v1/memory/candidates?status=pending");
    if (r.status !== 200) {
      box.innerHTML = ""; const p = document.createElement("p"); p.className = "empty";
      p.textContent = r.status === 401 ? "Unauthorized — check the token." : "Failed to load (" + r.status + ").";
      box.append(p); $("candcount").textContent = "–"; return;
    }
    const cands = (r.body.candidates || []);
    shownCandidates = cands.map(c => c.id);
    $("candcount").textContent = cands.length + " pending";
    box.innerHTML = "";
    if (!cands.length) { const p = document.createElement("p"); p.className = "empty";
      p.textContent = "Queue is clear."; box.append(p); return; }
    for (const c of cands) {
      const src = c.source || (c.metadata && c.metadata.source) || "unknown";
      const when = (c.createdAt || c.created_at || "").toString().slice(0, 19);
      const { div, acts } = itemNode(c.id + " · " + src + (when ? " · " + when : ""), c.content || "");
      acts.append(
        actBtn("Approve", "approve", () => decideCandidate(c.id, "approve", acts)),
        actBtn("Reject", "reject", () => decideCandidate(c.id, "reject", acts)),
        actBtn("Defer", "defer", () => decideCandidate(c.id, "defer", acts)));
      box.append(div);
    }
  }

  async function rejectAll() {
    if (!shownCandidates.length) return;
    if (!window.confirm("Reject ALL " + shownCandidates.length + " shown candidates? " +
        "Nothing is written to memory; audit rows are kept.")) return;
    const btn = $("rejectall"); btn.disabled = true;
    let done = 0;
    for (const id of shownCandidates) {
      const r = await api("POST", "/v1/memory/candidates/" + id,
        { decision: "reject", reason: "console bulk sweep" });
      if (r.status === 200) done++;
    }
    btn.disabled = false;
    await loadCandidates();
    $("candcount").textContent += " · swept " + done;
  }

  async function decideApproval(id, decision, acts) {
    if (decision === "approve" &&
        !window.confirm("Approve executes this external action LIVE. Continue?")) return;
    const s = resultSpan(acts);
    s.className = "result"; s.textContent = "…";
    const r = await api("POST", "/v1/approvals/" + id, { decision });
    if (r.status === 200 && (r.body.ok || r.body.approval)) {
      s.className = "result ok"; s.textContent = decision + (decision === "approve" ? "d — executed" : "ed");
      acts.querySelectorAll("button").forEach(b => b.disabled = true);
    } else {
      s.className = "result err";
      s.textContent = (r.body && r.body.error) || ("HTTP " + r.status);
    }
  }

  async function loadApprovals() {
    const box = $("apps"); box.textContent = "loading…";
    const r = await api("GET", "/v1/approvals?status=pending");
    if (r.status !== 200) {
      box.innerHTML = ""; const p = document.createElement("p"); p.className = "empty";
      p.textContent = r.status === 401 ? "Unauthorized — check the token." : "Failed to load (" + r.status + ").";
      box.append(p); $("appcount").textContent = "–"; return;
    }
    const apps = (r.body.approvals || []);
    $("appcount").textContent = apps.length + " pending";
    box.innerHTML = "";
    if (!apps.length) { const p = document.createElement("p"); p.className = "empty";
      p.textContent = "No external actions waiting."; box.append(p); return; }
    for (const a of apps) {
      const { div, acts } = itemNode(
        a.id + " · " + (a.workflow || "?") + " · " + (a.createdAt || "").toString().slice(0, 19),
        a.reason || JSON.stringify(a.params || {}));
      acts.append(
        actBtn("Approve (executes)", "reject", () => decideApproval(a.id, "approve", acts)),
        actBtn("Reject", "defer", () => decideApproval(a.id, "reject", acts)));
      box.append(div);
    }
  }

  async function ask() {
    const q = $("q").value.trim(); if (!q) return;
    $("ask").disabled = true; $("askstate").textContent = "thinking…";
    const r = await api("POST", "/v1/chat", { message: q });
    $("ask").disabled = false; $("askstate").textContent = "";
    const box = $("reply"); box.hidden = false; box.innerHTML = "";
    if (r.status === 200 && r.body.reply !== undefined) {
      const t = document.createElement("div"); t.textContent = r.body.reply;
      const rec = document.createElement("div"); rec.className = "receipt";
      const loop = r.body.loop || {};
      rec.textContent = "model " + (r.body.model || "?") +
        (loop.tactic ? " · tactic " + loop.tactic : "") +
        (loop.used ? " · goal loop" : " · fast path") +
        (r.body.approvalRequired ? " · ACTION REFUSED -> approval lane" : "");
      box.append(t, rec);
    } else {
      const t = document.createElement("div"); t.className = "result err";
      t.textContent = (r.body && (r.body.error || r.body.reply)) || ("HTTP " + r.status);
      box.append(t);
    }
  }

  function connect() {
    store.setItem("prepende_console_token", $("tok").value.trim());
    store.setItem("prepende_console_tenant", $("ten").value.trim());
    health(); loadCandidates(); loadApprovals();
  }

  $("connect").addEventListener("click", connect);
  $("refreshcands").addEventListener("click", loadCandidates);
  $("refreshapps").addEventListener("click", loadApprovals);
  $("rejectall").addEventListener("click", rejectAll);
  $("ask").addEventListener("click", ask);
  if ($("tok").value) connect();
})();
</script>
</body>
</html>
"""
