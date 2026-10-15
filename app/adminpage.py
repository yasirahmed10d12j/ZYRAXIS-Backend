"""Admin dashboard page (single-file HTML, zero JS dependencies).

Dark ZYRAXIS-styled status board: server health, connected devices,
runtime config form, recent events. Auto-refreshes every 30 s.
"""
from __future__ import annotations

from html import escape


def render(stats, settings, ollama_ok: bool, ollama_models: list, admin_key: str = "", connect: dict | None = None, hub: dict | None = None) -> str:
    # Devices grouped by (ip, client): one iPhone opens a fresh session per
    # chat turn (sessionId == requestId by design), so raw rows would spam
    # one line per message. Group shows: client, ip, session count, last seen.
    groups: dict = {}
    for sid, d in stats.devices.items():
        gkey = (d.get("ip", "?"), d.get("model", "?"))
        g = groups.setdefault(gkey, {"n": 0, "last": "", "online": False, "name": d.get("name", "?")})
        g["n"] += 1
        if d.get("last", "") >= g["last"]:
            g["last"] = d.get("last", "")
            g["name"] = d.get("name", "?")
        if sid in stats.peers:
            g["online"] = True
    dev_rows = "".join(
        "<tr><td>%s</td><td>%s</td><td>%s</td><td>%d session%s%s</td><td>%s</td></tr>"
        % (escape(g["name"]), escape(model), escape(ip), g["n"], "" if g["n"] == 1 else "s",
           " · ONLINE" if g["online"] else "", escape(g["last"]))
        for (ip, model), g in groups.items()
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

    def _device_online(did, info):
        if did in stats.peers:
            return True
        return any(s in stats.peers for s in info.get("sessions", []))

    dev_opts = "".join(
        '<option value="%s">%s (%s · %s)%s</option>'
        % (escape(did), escape(info.get("name", "?")), escape(info.get("model", "?")), escape(info.get("ip", "?")),
           " · ONLINE" if _device_online(did, info) else "")
        for did, info in stats.devices.items()
    ) or '<option value="">no devices yet</option>'

    pend_rows = "".join(
        "<tr><td>%s</td><td>%s</td></tr>" % (escape(dev), escape(", ".join(cmds)))
        for dev, cmds in stats.pending.items() if cmds
    )
    pend_html = ("<h2>Queued commands</h2><table><tr><th>device</th><th>waiting</th></tr>%s</table>" % pend_rows) if pend_rows else ""

    connect = connect or {}
    connect_html = """<h2>Connect a new iPhone</h2>
<div class="cards"><div class="card" style="min-width:280px"><b style="font-size:15px">%s</b><span>1 · Settings → AI Backend → base URL</span></div>
<div class="card" style="min-width:280px"><b style="font-size:15px">%s</b><span>2 · WebSocket URL</span></div>
<div class="card" style="min-width:280px"><b style="font-size:15px">%s</b><span>3 · Backend API key → Save key → provider ZYRAXIS Backend</span></div></div>
<div class="note">Same Wi-Fi/hotspot required. IP changes on hotspot restart — this card always shows the live one.</div>""" % (
        escape(connect.get("base", "—")),
        escape(connect.get("ws", "—")),
        escape(connect.get("key", "—")),
    )

    models = ", ".join(escape(m) for m in ollama_models) or "none pulled"
    order = ",".join(settings.provider_order)
    return """<!DOCTYPE html><html><head><meta charset="utf-8">
<meta http-equiv="refresh" content="30">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ZYRAXIS Server</title>
<style>
body{background:#0a0e14;color:#c9e6f2;font-family:Segoe UI,system-ui,sans-serif;margin:0;padding:16px}
h1{color:#22d3ee;font-size:22px;margin:0 0 4px}.sub{color:#64748b;font-size:13px;margin-bottom:16px}
.cards{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:16px}
.card{background:#111827;border:1px solid #1e293b;border-radius:10px;padding:12px 16px;min-width:130px}
.card b{display:block;font-size:22px;color:#fff}.card span{font-size:12px;color:#64748b}
.ok{color:#4ade80}.bad{color:#f87171}
table{width:100%%;border-collapse:collapse;margin:8px 0 20px;font-size:14px}
th,td{text-align:left;padding:8px;border-bottom:1px solid #1e293b}
th{color:#64748b;font-size:12px;text-transform:uppercase}
h2{font-size:16px;color:#22d3ee;margin:20px 0 4px}
form{background:#111827;border:1px solid #1e293b;border-radius:10px;padding:14px}
label{display:block;font-size:13px;color:#94a3b8;margin:8px 0 4px}
input{background:#0a0e14;border:1px solid #334155;color:#fff;border-radius:6px;padding:8px;width:100%%;max-width:420px;box-sizing:border-box}
select{background:#0a0e14;border:1px solid #334155;color:#fff;border-radius:6px;padding:8px;max-width:420px}
button{background:#0891b2;color:#fff;border:0;border-radius:6px;padding:9px 20px;margin-top:12px;cursor:pointer;font-size:14px}
button:hover{background:#06b6d4}
button.ghost{background:#1e293b;margin-left:8px}
.note{font-size:12px;color:#64748b;margin-top:10px}
.warn{font-size:12px;color:#fbbf24;margin-top:8px}
</style></head><body>
<h1>⬡ ZYRAXIS Server</h1>
<div class="sub">local control board · auto-refresh 30s · protocol v1</div>
%s
<div class="cards">
<div class="card"><b>%s</b><span>uptime</span></div>
<div class="card"><b>%d</b><span>WS connected now</span></div>
<div class="card"><b>%d</b><span>REST chats</span></div>
<div class="card"><b>%d / %d</b><span>LLM ok / fail</span></div>
<div class="card"><b class="%s">%s</b><span>Ollama (%s)</span></div>
<div class="card"><b>%d</b><span>devices seen</span></div>
<div class="card"><b>%d</b><span>shared memories</span></div>
<div class="card"><b>%d</b><span>active tasks</span></div>
</div>
<h2>Devices</h2>
<table><tr><th>name</th><th>device</th><th>ip</th><th>sessions</th><th>last seen</th></tr>%s</table>
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
%s
<h2>Live transcript (device ⇄ server mirror)</h2>
<table><tr><th>time</th><th>device</th><th>request</th><th>reply</th></tr>%s</table>
<form method="post" action="/admin/clear" style="background:none;border:0;padding:0">
<input type="hidden" name="key" value="%s">
<button class="ghost" type="submit">Clear transcript + events</button>
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
<h2>Requests per IP</h2>
<table><tr><th>ip</th><th>hits</th></tr>%s</table>
<h2>Recent events</h2>
<table><tr><th>time</th><th>event</th></tr>%s</table>
</body></html>""" % (
        connect_html,
        escape(stats.uptime()),
        stats.ws_current,
        stats.rest_hits,
        stats.llm_ok,
        stats.llm_fail,
        "ok" if ollama_ok else "bad",
        "UP" if ollama_ok else "DOWN",
        escape(models),
        len(stats.devices),
        (hub or {}).get("memories", 0),
        (hub or {}).get("tasks", 0),
        dev_rows,
        escape(admin_key),
        dev_opts,
        pend_html,
        turn_rows,
        escape(admin_key),
        escape(admin_key),
        escape(order),
        settings.max_output_tokens,
        settings.temperature,
        ip_rows,
        event_rows,
    )
