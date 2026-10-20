"""Admin dashboard page (single-file HTML, zero dependencies).

Dark ZYRAXIS control-board aesthetic: glass cards, cyan glow accents,
copy-ready connect values. Auto-refreshes every 30 s.
"""
from __future__ import annotations

from html import escape


def render(stats, settings, ollama_ok: bool, ollama_models: list, admin_key: str = "", connect: dict | None = None, hub: dict | None = None) -> str:
    dev_rows = "".join(
        "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
        % (escape(sid), escape(d["ip"]), escape(d["model"]), escape(d["app"]), escape(d["last"]))
        for sid, d in stats.devices.items()
    ) or '<tr><td colspan="5">no devices yet — open the app and send a message</td></tr>'

    ip_rows = "".join(
        "<tr><td>%s</td><td>%d</td></tr>" % (escape(ip), n)
        for ip, n in sorted(stats.per_ip.items(), key=lambda kv: -kv[1])[:20]
    ) or '<tr><td colspan="2">—</td></tr>'

    event_rows = "".join(
        "<tr><td>%s</td><td>%s</td></tr>" % (escape(e["t"]), escape(e["msg"]))
        for e in stats.events
    ) or '<tr><td colspan="2">—</td></tr>'

    turn_rows = "".join(
        "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
        % (escape(t["t"]), escape(t["dev"]), escape(t["q"]), escape(t["a"]))
        for t in stats.transcript
    ) or '<tr><td colspan="4">no chats yet — send a message from the app</td></tr>'

    dev_opts = "".join(
        '<option value="%s">%s (%s)%s</option>'
        % (escape(sid), escape(d["model"]), escape(d["ip"]),
           " · ONLINE" if sid in stats.peers else "")
        for sid, d in stats.devices.items()
    ) or '<option value="">no devices yet</option>'

    pend_rows = "".join(
        "<tr><td>%s</td><td>%s</td></tr>" % (escape(dev), escape(", ".join(cmds)))
        for dev, cmds in stats.pending.items() if cmds
    )
    pend_html = ("<h2>Queued commands</h2><table><tr><th>device</th><th>waiting</th></tr>%s</table>" % pend_rows) if pend_rows else ""

    models = ", ".join(escape(m) for m in ollama_models) or "none pulled"
    order = ",".join(settings.provider_order)
    c = connect or {}
    connect_html = connect_card(c.get("base", "—"), c.get("ws", "—"), c.get("key", "—"))
    h = hub or {}
    sys = h.get("sys") or {}
    rd = h.get("rd") or {}
    rd_sessions = rd.get("sessions") or []
    if rd_sessions:
        boxes = "".join(
            """<div class="connbox"><code id="rd-key-%d">%s</code><span>%d device(s) attached — 2 = phone screen live</span> <button class="copy" onclick="copyVal('rd-key-%d')">Copy</button></div>"""
            % (i, escape(s["key"]), s["peers"], i)
            for i, s in enumerate(rd_sessions)
        )
        rd_html = """<div class="conn">%s</div>
<div class="note"><span class="step">Paste on iPhone:</span> ZYRAXIS → Settings → Remote Desktop → key paste karo → <b>Connect to Desktop</b>. Screen tabhi aayegi jab dono taraf (laptop + phone) connected hon — 2 device(s) dikhne chahiye.</div>""" % boxes
    else:
        rd_html = """<div class="connbox"><code>no active session</code><span>laptop side se session shuru karo</span></div>
<div class="note">
<span class="step">1 ·</span> Laptop ke taskbar tray me ZYRAXIS icon pe right-click → <b>Remote Desktop — session key</b><br>
<span class="step">2 ·</span> Dialog me jo <b>rd-...</b> key aayegi, wo copy karo (yahi page bhi dikhayega jab laptop connected ho)<br>
<span class="step">3 ·</span> iPhone → ZYRAXIS → <b>Settings → Remote Desktop</b> → key paste → <b>Connect to Desktop</b>
</div>"""
    ram = sys.get("ram") or {}
    disks = sys.get("disks") or {}
    da = disks.get("A:\\", {})
    batt = sys.get("battery")
    sys_html = """<div class="cards">
<div class="card"><b>%s%%</b><span>CPU</span></div>
<div class="card"><b>%s%%</b><span>RAM (%s/%s GB)</span></div>
<div class="card"><b>%s%%</b><span>Drive A free %s GB</span></div>
<div class="card"><b>%s</b><span>Battery</span></div>
<div class="card"><b>%s h</b><span>Uptime</span></div>
</div>""" % (
        escape(str(sys.get("cpu_pct", "?"))),
        escape(str(ram.get("pct", "?"))),
        escape(str(ram.get("used_gb", "?"))), escape(str(ram.get("total_gb", "?"))),
        escape(str(da.get("pct", "?"))), escape(str(da.get("free_gb", "?"))),
        escape(("%s%% %s" % (batt["pct"], "⚡" if batt["plugged"] else "🔋")) if batt else "—"),
        escape(str(sys.get("uptime_h", "?"))),
    )
    w = stats.last_weather if isinstance(stats.last_weather, dict) else {}
    if w.get("ok"):
        weather_html = """<div class="card" style="min-width:280px"><b style="font-size:15px">%s°C · %s</b><span>H %s%% · wind %s kph · today %s/%s°C · rain %s%%</span></div>""" % (
            escape(str(w.get("temp_c", "?"))), escape(str(w.get("desc", "?"))),
            escape(str(w.get("humidity", "?"))), escape(str(w.get("wind_kph", "?"))),
            escape(str((w.get("today") or {}).get("min_c", "?"))),
            escape(str((w.get("today") or {}).get("max_c", "?"))),
            escape(str((w.get("today") or {}).get("precip_prob", "?"))))
    elif w.get("error"):
        weather_html = """<div class="warn">Weather failed: %s</div>""" % escape(str(w["error"])[:120])
    else:
        weather_html = """<div class="note">No lookup yet — enter coordinates below.</div>"""
    vision_html = ("""<div class="card" style="min-width:280px"><b style="font-size:14px">%s</b><span>last vision result</span></div>"""
                   % escape(stats.last_vision[:500])) if stats.last_vision else ""
    return """<!DOCTYPE html><html><head><meta charset="utf-8">
<meta http-equiv="refresh" content="30">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ZYRAXIS Server</title>
<style>
:root{--bg:#070b12;--panel:#0e1622;--line:#1c2a3d;--txt:#d7ecf7;--dim:#7d93a8;--cy:#22d3ee;--cy2:#0891b2;--ok:#4ade80;--bad:#f87171;--warn:#fbbf24}
*{box-sizing:border-box}
body{background:radial-gradient(1200px 600px at 20%% -10%%,#0e2233 0%%,var(--bg) 55%%);color:var(--txt);font-family:'Segoe UI',system-ui,sans-serif;margin:0;padding:20px;max-width:1100px;margin-left:auto;margin-right:auto}
.top{display:flex;align-items:center;gap:12px;margin-bottom:2px}
.logo{width:38px;height:38px;border-radius:10px;background:linear-gradient(135deg,#0e3a46,#08202a);border:1px solid var(--line);display:flex;align-items:center;justify-content:center;color:var(--cy);font-size:20px;box-shadow:0 0 18px rgba(34,211,238,.25)}
h1{color:#fff;font-size:22px;margin:0;letter-spacing:.5px}
.sub{color:var(--dim);font-size:12.5px;margin:2px 0 16px 50px}
.dot{width:9px;height:9px;border-radius:50%%;background:var(--ok);box-shadow:0 0 10px var(--ok);animation:pulse 2s infinite}
@keyframes pulse{50%%{opacity:.45}}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px;margin-bottom:6px}
.card{background:linear-gradient(180deg,#101b29,#0c1420);border:1px solid var(--line);border-radius:12px;padding:12px 14px}
.card b{display:block;font-size:21px;color:#fff;font-variant-numeric:tabular-nums}
.card span{font-size:11.5px;color:var(--dim)}
.ok{color:var(--ok)}.bad{color:var(--bad)}
table{width:100%%;border-collapse:collapse;margin:8px 0 20px;font-size:13.5px;background:var(--panel);border:1px solid var(--line);border-radius:12px;overflow:hidden}
th,td{text-align:left;padding:9px 12px;border-bottom:1px solid var(--line)}
tr:last-child td{border-bottom:0}
th{color:var(--dim);font-size:11px;text-transform:uppercase;letter-spacing:.8px;background:#0b1320}
td.mono{font-family:Consolas,monospace;font-size:12.5px}
h2{font-size:15px;color:var(--cy);margin:22px 0 6px;letter-spacing:.4px}
form{background:linear-gradient(180deg,#101b29,#0c1420);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
label{display:block;font-size:12.5px;color:var(--dim);margin:10px 0 4px}
input,select{background:#070d16;border:1px solid #2b3f55;color:#fff;border-radius:8px;padding:9px 10px;width:100%%;max-width:460px}
input:focus,select:focus{outline:none;border-color:var(--cy2)}
button{background:linear-gradient(180deg,#0ea5c4,#087a94);color:#fff;border:0;border-radius:8px;padding:9px 22px;margin-top:12px;cursor:pointer;font-size:14px;font-weight:600}
button:hover{filter:brightness(1.15)}
button.ghost{background:#1a2635;margin-left:8px}
button.copy{padding:5px 12px;font-size:12px;margin:0 0 0 8px}
.note{font-size:12px;color:var(--dim);margin-top:10px}
.warn{font-size:12px;color:var(--warn);margin-top:8px}
.conn{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:10px;margin-bottom:4px}
.connbox{background:linear-gradient(180deg,#10202f,#0c1622);border:1px solid #23465c;border-radius:12px;padding:12px 14px}
.connbox code{display:block;font-family:Consolas,monospace;font-size:14px;color:#fff;word-break:break-all}
.connbox span{font-size:11.5px;color:var(--dim)}
.step{color:var(--cy);font-weight:700;margin-right:6px}
</style></head><body>
<div class="top"><div class="logo">⬡</div><h1>ZYRAXIS Server</h1><div class="dot"></div></div>
<div class="sub">local control board · auto-refresh 30s · protocol v1</div>
%s
<h2>Remote Desktop — phone controls this laptop</h2>
%s
<div class="cards">
<div class="card"><b>%s</b><span>uptime</span></div>
<div class="card"><b>%d</b><span>WS connected now</span></div>
<div class="card"><b>%d</b><span>REST chats</span></div>
<div class="card"><b>%d / %d</b><span>LLM ok / fail</span></div>
<div class="card"><b class="%s">%s</b><span>Ollama (%s)</span></div>
<div class="card"><b>%d</b><span>devices seen</span></div>
</div>
<h2>Devices</h2>
<table><tr><th>session</th><th>ip</th><th>client</th><th>app</th><th>last seen</th></tr>%s</table>
<h2>Remote command → device</h2>
<form method="post" action="/admin/cmd">
<input type="hidden" name="key" value="%s">
<label>Device</label>
<select name="device">%s</select>
<label>Command (e.g. open youtube — runs on the device)</label>
<input name="command" placeholder="open youtube" maxlength="200">
<div><button type="submit">Send now</button>
<button class="ghost" type="submit" formaction="/admin/ping" formmethod="post">Ping device</button></div>
<div class="warn">Offline device? Command queues and runs on its next session. Executing on the iPhone needs the client update — until then sends are safely ignored there.</div>
</form>
<form method="post" action="/admin/broadcast" style="margin-top:10px">
<input type="hidden" name="key" value="%s">
<label>Broadcast to ALL connected devices at once</label>
<input name="command" placeholder="open youtube" maxlength="200">
<div><button type="submit">Broadcast</button></div>
</form>
%s
<h2>Live transcript (device ⇄ server mirror)</h2>
<table><tr><th>time</th><th>device</th><th>request</th><th>reply</th></tr>%s</table>
<form method="post" action="/admin/clear" style="background:none;border:0;padding:0">
<input type="hidden" name="key" value="%s">
<button class="ghost" type="submit">Clear transcript + events</button>
</form>
<div style="margin-top:8px"><a href="/admin/export?key=%s" style="color:#22d3ee;font-size:13px">⬇ Download transcript (.txt)</a></div>
<h2>Quick add (shared hub — all devices see it)</h2>
<form method="post" action="/admin/note">
<input type="hidden" name="key" value="%s">
<label>Memory note</label>
<input name="content" placeholder="Rehan's birthday is June 5" maxlength="2000">
<div><button type="submit">Save note</button></div>
</form>
<form method="post" action="/admin/task" style="margin-top:10px">
<input type="hidden" name="key" value="%s">
<label>Task</label>
<input name="title" placeholder="Pay bill" maxlength="300">
<div><button type="submit">Add task</button></div>
</form>
<h2>Runtime config</h2>
<form method="post" action="/admin/config">
<input type="hidden" name="key" value="%s">
<label>Provider order (comma-separated: local, gemini, groq)</label>
<input name="provider_order" value="%s">
<label>Max output tokens (512 = fast on 8GB CPU)</label>
<input name="max_output_tokens" value="%d">
<label>Temperature (0.0 – 1.0)</label>
<input name="temperature" value="%.2f">
<button type="submit">Apply (no restart)</button>
<div class="note">Keys stay in environment — never shown or edited here.</div>
</form>
<h2>Model RAM</h2>
<form method="post" action="/admin/model-unload">
<input type="hidden" name="key" value="%s">
<div class="note">Unload the local model from RAM (frees ~2GB on this 8GB laptop). The next chat reloads it automatically (slow first reply).</div>
<div><button type="submit">Unload model</button></div>
</form>
<h2>System health (this laptop)</h2>
%s
<h2>Weather (Open-Meteo, keyless)</h2>
%s
<form method="post" action="/admin/weather">
<input type="hidden" name="key" value="%s">
<label>Coordinates (decimal degrees — e.g. Delhi 28.61, 77.20)</label>
<input name="lat" placeholder="28.61" style="max-width:200px"> <input name="lon" placeholder="77.20" style="max-width:200px">
<div><button type="submit">Check weather</button></div>
</form>
<h2>Vision (local moondream)</h2>
%s
<form method="post" action="/admin/vision" id="vi-form" onsubmit="return packVision()">
<input type="hidden" name="key" value="%s">
<input type="hidden" name="image" id="vi-b64" value="">
<input type="hidden" name="question" id="vi-q" value="">
<label>Photo</label>
<input type="file" id="vi-file" accept="image/*">
<label>Question</label>
<input id="vi-qq" placeholder="What is in this picture?" maxlength="500">
<div><button type="submit">Describe (1–3 min on CPU)</button></div>
</form>
<script>
function packVision(){
  var f=document.getElementById('vi-file').files[0];
  if(!f){alert('Choose a photo first.');return false;}
  if(f.size>8*1024*1024){alert('Photo must be under 8 MB.');return false;}
  var r=new FileReader();
  r.onload=function(){document.getElementById('vi-b64').value=r.result.split(',')[1]||'';
    document.getElementById('vi-q').value=document.getElementById('vi-qq').value;
    document.getElementById('vi-form').submit();};
  r.readAsDataURL(f);return false;
}
</script>
<h2>Requests per IP</h2>
<table><tr><th>ip</th><th>hits</th></tr>%s</table>
<h2>Recent events</h2>
<table><tr><th>time</th><th>event</th></tr>%s</table>
<script>
function copyVal(id){var el=document.getElementById(id);var t=el?el.textContent:'';if(navigator.clipboard){navigator.clipboard.writeText(t);}else{var ta=document.createElement('textarea');ta.value=t;document.body.appendChild(ta);ta.select();try{document.execCommand('copy');}catch(e){}ta.remove();}}
</script>
</body></html>""" % (
        connect_html,
        rd_html,
        escape(stats.uptime()),
        stats.ws_current,
        stats.rest_hits,
        stats.llm_ok,
        stats.llm_fail,
        "ok" if ollama_ok else "bad",
        "UP" if ollama_ok else "DOWN",
        escape(models),
        len(stats.devices),
        dev_rows,
        escape(admin_key),
        dev_opts,
        escape(admin_key),
        pend_html,
        turn_rows,
        escape(admin_key),
        escape(admin_key),
        escape(admin_key),
        escape(admin_key),
        escape(admin_key),
        escape(order),
        settings.max_output_tokens,
        settings.temperature,
        escape(admin_key),
        sys_html,
        weather_html,
        escape(admin_key),
        vision_html,
        escape(admin_key),
        ip_rows,
        event_rows,
    )


def connect_card(base, ws, key):
    return """<h2>Connect a new iPhone</h2>
<div class="conn">
<div class="connbox"><code id="cv-base">%s</code><span><span class="step">1 ·</span>Settings → AI Backend → base URL</span> <button class="copy" onclick="copyVal('cv-base')">Copy</button></div>
<div class="connbox"><code id="cv-ws">%s</code><span><span class="step">2 ·</span>WebSocket URL</span> <button class="copy" onclick="copyVal('cv-ws')">Copy</button></div>
<div class="connbox"><code id="cv-key">%s</code><span><span class="step">3 ·</span>Backend API key → Save → ZYRAXIS Backend</span> <button class="copy" onclick="copyVal('cv-key')">Copy</button></div>
</div>
<div class="note">Same Wi-Fi/hotspot required. IP changes on hotspot restart — this card always shows the live one.</div>""" % (
        escape(base), escape(ws), escape(key))
