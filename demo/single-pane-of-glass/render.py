"""Render the aggregated model into a self-contained single-pane dashboard.

Produces one static HTML file (inline CSS + inline SVG charts, no external
CDNs) so it renders offline and screenshots cleanly. The browser screenshot
captured by the web-portal connector is referenced alongside it as portal.png.
"""
import html
import os
from datetime import datetime, timezone


def _esc(v):
    return html.escape(str(v if v is not None else ""))


_STATUS_DOT = {
    "ok": ("#16a34a", "live"),
    "unavailable": ("#dc2626", "unavailable"),
}


def _source_card(s):
    color, _ = _STATUS_DOT.get(s["status"], ("#9ca3af", ""))
    return f"""
      <div class="src">
        <div class="src-top"><span class="dot" style="background:{color}"></span>
          <span class="src-type">{_esc(s['type'])}</span></div>
        <div class="src-name">{_esc(s['name'])}</div>
        <div class="src-detail">{_esc(s['detail'])}</div>
      </div>"""


def _kpi(value, label):
    return f"""
      <div class="kpi"><div class="kpi-v">{_esc(value)}</div>
        <div class="kpi-l">{_esc(label)}</div></div>"""


def _dept_bars(departments):
    if not departments:
        return "<p class='muted'>No department data.</p>"
    top = departments[:12]
    max_bytes = max((d["storage_bytes"] for d in top), default=1) or 1
    rows = []
    for d in top:
        pct = 100 * d["storage_bytes"] / max_bytes
        rows.append(f"""
        <div class="bar-row">
          <div class="bar-label" title="{_esc(d['name'])}">{_esc(d['name'])}</div>
          <div class="bar-track"><div class="bar-fill" style="width:{pct:.1f}%"></div></div>
          <div class="bar-val">{_esc(d['storage_human'])}
            <span class="bar-sub">{d['file_count']:,} files &middot;
              {d['document_count']} docs</span>
          </div>
        </div>""")
    return "\n".join(rows)


def _file_types(types):
    if not types:
        return ""
    total = sum(t["count"] for t in types) or 1
    chips = []
    for t in types:
        pct = round(100 * t["count"] / total)
        chips.append(
            f"<span class='chip'>{_esc(t['label'])} "
            f"<b>{t['count']:,}</b> <i>{pct}%</i></span>"
        )
    return "<div class='chips'>" + "".join(chips) + "</div>"


def _external(external):
    inds = external.get("indicators", [])
    if not inds:
        return "<p class='muted'>External feed unavailable.</p>"
    cells = []
    for i in inds:
        cells.append(f"""
        <div class="ext-cell">
          <div class="ext-v">{_esc(i['value'])}<span>{_esc(i['unit'])}</span></div>
          <div class="ext-l">{_esc(i['name'])}</div>
          <div class="ext-y">{_esc(i['year'])}</div>
        </div>""")
    url = external.get("source_url", "")
    return ("<div class='ext-grid'>" + "".join(cells) + "</div>"
            f"<div class='ext-src'>Source: <a href='{_esc(url)}'>"
            f"{_esc(external.get('source', 'External'))}</a></div>")


def _portal(portal):
    files = portal.get("recent_files") or []
    if not files and not portal.get("screenshot"):
        return "<p class='muted'>Portal not reached.</p>"
    figs = "".join(
        f"<li><span>{_esc(f.get('name',''))}</span>"
        f"<b>{_esc((f.get('meta','') or '').split(chr(0xB7))[0].strip())}</b></li>"
        for f in files[:6]
    )
    shot = ""
    if portal.get("screenshot"):
        shot = (f"<img class='shot' src='{_esc(portal['screenshot'])}' "
                f"alt='Web portal screenshot'/>")
    who = portal.get("account") or portal.get("logged_in_as", "")
    return (f"<div class='portal-note'>Logged into the web app through the "
            f"browser as <b>{_esc(who)}</b> \u2014 files read straight off the "
            f"screen (no API):</div><ul class='figs'>{figs}</ul>{shot}")


def _activity(activity):
    if not activity:
        return "<p class='muted'>No recent activity.</p>"
    rows = []
    for a in activity[:10]:
        rows.append(
            f"<li><span class='act-type act-{_esc(a.get('type',''))}'>"
            f"{_esc(a.get('type',''))}</span>"
            f"<span class='act-desc'>{_esc(a.get('description',''))}</span></li>"
        )
    return "<ul class='activity'>" + "".join(rows) + "</ul>"


def _insights(insights):
    if not insights:
        return ""
    items = "".join(f"<li>{_esc(x)}</li>" for x in insights)
    return f"<ul class='insights'>{items}</ul>"


def render(data, generated_at=None):
    generated_at = generated_at or datetime.now(timezone.utc)
    ts = generated_at.strftime("%Y-%m-%d %H:%M UTC")
    k = data["kpis"]
    recon = data.get("reconciliation", {})
    recon_ok = recon.get("ok")
    recon_badge = (
        "<span class='badge ok'>&#10003; reconciled</span>" if recon_ok
        else ("<span class='badge warn'>&#9888; mismatch</span>"
              if recon_ok is False else "")
    )

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>OtterWorks &mdash; Single Pane of Glass</title>
<style>
  :root{{--bg:#0b1120;--card:#fff;--ink:#0f172a;--muted:#64748b;--line:#e2e8f0;
    --accent:#2563eb;--accent2:#7c3aed;--good:#16a34a;}}
  *{{box-sizing:border-box}}
  body{{margin:0;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
    background:#f1f5f9;color:var(--ink);}}
  .wrap{{max-width:1200px;margin:0 auto;padding:0 20px 48px}}
  header.hero{{background:linear-gradient(120deg,#0b1120,#1e293b 60%,#312e81);
    color:#fff;padding:28px 0 22px;margin-bottom:22px;}}
  .hero .wrap{{padding-bottom:0}}
  .hero h1{{margin:0;font-size:26px;letter-spacing:-.5px}}
  .hero p{{margin:6px 0 0;color:#cbd5e1;font-size:14px}}
  .hero .meta{{margin-top:14px;font-size:12px;color:#94a3b8}}
  .sources{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:18px}}
  .src{{background:rgba(255,255,255,.07);border:1px solid rgba(255,255,255,.12);
    border-radius:12px;padding:12px 14px}}
  .src-top{{display:flex;align-items:center;gap:7px}}
  .dot{{width:8px;height:8px;border-radius:50%;display:inline-block}}
  .src-type{{font-size:11px;text-transform:uppercase;letter-spacing:.6px;color:#94a3b8}}
  .src-name{{color:#fff;font-weight:600;font-size:14px;margin-top:5px}}
  .src-detail{{color:#cbd5e1;font-size:12px;margin-top:3px}}
  .kpis{{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:20px}}
  .kpi{{background:var(--card);border:1px solid var(--line);border-radius:12px;
    padding:16px;box-shadow:0 1px 2px rgba(15,23,42,.04)}}
  .kpi-v{{font-size:24px;font-weight:700}}
  .kpi-l{{font-size:12px;color:var(--muted);margin-top:3px}}
  .grid{{display:grid;grid-template-columns:1.35fr 1fr;gap:18px}}
  .card{{background:var(--card);border:1px solid var(--line);border-radius:14px;
    padding:18px 20px;margin-bottom:18px;box-shadow:0 1px 2px rgba(15,23,42,.04)}}
  .card h2{{margin:0 0 14px;font-size:15px;display:flex;align-items:center;
    justify-content:space-between}}
  .bar-row{{display:grid;grid-template-columns:150px 1fr 150px;align-items:center;
    gap:10px;margin-bottom:9px;font-size:12px}}
  .bar-label{{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:#334155}}
  .bar-track{{background:#eef2ff;border-radius:6px;height:16px;overflow:hidden}}
  .bar-fill{{height:100%;background:linear-gradient(90deg,var(--accent),var(--accent2))}}
  .bar-val{{text-align:right;font-weight:600}}
  .bar-sub{{display:block;font-weight:400;color:var(--muted);font-size:10.5px}}
  .chips{{display:flex;flex-wrap:wrap;gap:7px;margin-top:14px}}
  .chip{{background:#f1f5f9;border:1px solid var(--line);border-radius:20px;
    padding:4px 10px;font-size:11.5px;color:#475569}}
  .chip i{{color:var(--muted);font-style:normal}}
  .ext-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}}
  .ext-cell{{background:#f8fafc;border:1px solid var(--line);border-radius:10px;padding:12px}}
  .ext-v{{font-size:22px;font-weight:700}}
  .ext-v span{{font-size:13px;color:var(--muted);margin-left:2px}}
  .ext-l{{font-size:11.5px;color:#475569;margin-top:3px}}
  .ext-y{{font-size:11px;color:var(--muted);margin-top:2px}}
  .ext-src{{font-size:11px;color:var(--muted);margin-top:10px}}
  .portal-note{{font-size:12.5px;color:#475569;margin-bottom:10px}}
  ul.figs{{list-style:none;padding:0;margin:0 0 12px;display:grid;
    grid-template-columns:repeat(2,1fr);gap:8px}}
  ul.figs li{{display:flex;justify-content:space-between;background:#f8fafc;
    border:1px solid var(--line);border-radius:8px;padding:8px 10px;font-size:12.5px}}
  ul.figs b{{font-weight:700}}
  .shot{{width:100%;border:1px solid var(--line);border-radius:10px;margin-top:4px}}
  ul.activity{{list-style:none;padding:0;margin:0}}
  ul.activity li{{display:flex;gap:9px;align-items:center;padding:7px 0;
    border-bottom:1px solid #f1f5f9;font-size:12.5px}}
  .act-type{{font-size:10px;text-transform:uppercase;letter-spacing:.5px;
    padding:2px 7px;border-radius:5px;background:#eef2ff;color:var(--accent);flex-shrink:0}}
  .act-desc{{color:#334155;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
  ul.insights{{margin:0;padding-left:18px}}
  ul.insights li{{margin-bottom:8px;font-size:13px;color:#334155}}
  .badge{{font-size:11px;font-weight:600;padding:3px 9px;border-radius:20px}}
  .badge.ok{{background:#dcfce7;color:#166534}}
  .badge.warn{{background:#fef9c3;color:#854d0e}}
  .muted{{color:var(--muted);font-size:13px}}
  .lineage{{font-size:11.5px;color:#94a3b8;text-align:center;margin-top:26px}}
  @media(max-width:900px){{.grid{{grid-template-columns:1fr}}
    .kpis{{grid-template-columns:repeat(2,1fr)}}.sources{{grid-template-columns:1fr}}}}
</style></head>
<body>
<header class="hero"><div class="wrap">
  <h1>Enterprise Single Pane of Glass</h1>
  <p>One consolidated view assembled from three heterogeneous systems &mdash;
     structured API/DB, a UI-only web app (via the browser), and the public web.</p>
  <div class="sources">{''.join(_source_card(s) for s in data['sources'])}</div>
  <div class="meta">Generated {ts} &middot; automated agentic workflow</div>
</div></header>

<div class="wrap">
  <div class="kpis">
    {_kpi(f"{k['total_files']:,}", "Files")}
    {_kpi(f"{k['total_documents']:,}", "Documents")}
    {_kpi(k['total_storage_human'], "Storage")}
    {_kpi(k['department_count'], "Departments")}
    {_kpi(k['external_indicators'], "External signals")}
  </div>

  <div class="card">
    <h2>Key findings <span>{recon_badge}</span></h2>
    {_insights(data['insights'])}
  </div>

  <div class="grid">
    <div>
      <div class="card">
        <h2>Storage by department</h2>
        {_dept_bars(data['departments'])}
        {_file_types(data['file_types'])}
      </div>
      <div class="card">
        <h2>Recent activity</h2>
        {_activity(data['activity'])}
      </div>
    </div>
    <div>
      <div class="card">
        <h2>External market context</h2>
        {_external(data['external'])}
      </div>
      <div class="card">
        <h2>Retrieved via browser (no API)</h2>
        {_portal(data['portal'])}
      </div>
    </div>
  </div>

  <div class="lineage">
    Structured API/DB &nbsp;+&nbsp; UI web portal (browser) &nbsp;+&nbsp; public web
    &nbsp;&rarr;&nbsp; transform &amp; reconcile &nbsp;&rarr;&nbsp; single pane of glass
  </div>
</div>
</body></html>"""


def write(data, output_dir, generated_at=None):
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, "dashboard.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(render(data, generated_at))
    return path
