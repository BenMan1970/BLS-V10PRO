_INLINE_TEMPLATE = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BLUESTAR FX Desk_Signal Report_{{date_hdr_file}}</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@300;400;500;600;700&display=swap');
:root{
  --royal:#1B45B4;--royal-mid:#2355C3;--royal-light:#E8EEFF;--royal-dim:#6B89D8;
  --bg:#f5f7fc;--white:#fff;--card:#f0f3fa;--dark:#0d1f4e;--body:#1a1a2e;--sec:#3a4a7a;--muted:#6B89D8;--th:#E8EEFF;
  --green:#1a7a4a;--grn-bg:#e8f5ee;--grn-bd:#6EE7B7;--grn-tx:#065F46;
  --red:#c0292a;--red-bg:#fdecea;--red-bd:#FCA5A5;--red-tx:#7F1D1D;
  --blue:#2355C3;--purple:#1B45B4;
  --border:#dde3f5;--border2:#bbc6e8;--r:5px;--rl:7px;--gap:12px;
  --sans:'IBM Plex Sans',system-ui,sans-serif;--mono:'IBM Plex Mono','SF Mono','Courier New',monospace
}
*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--bg);color:var(--body);font-family:var(--sans);font-size:12px;line-height:1.45;-webkit-font-smoothing:antialiased}
#page{max-width:1180px;margin:0 auto;background:var(--bg)}
.wrap{padding:14px 20px}
.section{background:var(--white);border:1px solid var(--border);border-radius:var(--rl);margin-bottom:var(--gap);overflow:hidden;box-shadow:0 1px 3px rgba(13,31,78,.03)}
.sec-hdr{display:flex;align-items:center;gap:10px;padding:9px 16px;border-bottom:1px solid var(--border);background:var(--white)}
.sec-num{width:22px;height:22px;border-radius:50%;background:var(--royal);color:#fff;font-size:10px;font-weight:700;display:flex;align-items:center;justify-content:center;flex-shrink:0;font-family:var(--mono)}
.sec-ttl{font-size:12px;font-weight:700;color:var(--dark);text-transform:uppercase;letter-spacing:.5px;font-family:var(--mono)}
.sec-sub{margin-left:auto;font-size:9.5px;color:var(--muted);font-style:italic}
.sec-body{padding:12px 16px}
.banner{background:var(--red-bg);border:1px solid var(--red-bd);color:var(--red-tx);border-radius:var(--r);padding:9px 14px;margin-bottom:12px;font-family:var(--mono);font-size:10.5px;font-weight:600}
.setup{border:1px solid var(--border);border-radius:var(--rl);overflow:hidden;margin-bottom:11px;box-shadow:0 1px 2px rgba(13,31,78,.03)}
.setup:last-child{margin-bottom:0}
.setup.aaa{border-left:3px solid var(--royal)}.setup.aa{border-left:3px solid var(--royal-mid)}.setup.a{border-left:3px solid var(--green)}.setup.bbb{border-left:3px solid var(--muted)}.setup.bb{border-left:3px solid var(--border2)}.setup.b{border-left:3px solid var(--border2)}
.setup-hdr{display:flex;align-items:center;gap:10px;padding:9px 16px;border-bottom:1px solid var(--border);flex-wrap:wrap}
.setup-hdr.long{background:var(--grn-bg)}.setup-hdr.short{background:var(--red-bg)}
.pair{font-size:16px;font-weight:700;font-family:var(--mono);color:var(--dark)}
.dir{display:inline-flex;align-items:center;gap:4px;padding:2px 9px;border-radius:4px;font-size:10.5px;font-weight:700;font-family:var(--mono)}
.dir.long{background:var(--grn-bg);border:1px solid var(--grn-bd);color:var(--grn-tx)}
.dir.short{background:var(--red-bg);border:1px solid var(--red-bd);color:var(--red-tx)}
.conv{display:inline-flex;padding:2px 9px;border-radius:4px;font-size:10.5px;font-weight:700;font-family:var(--mono)}
.conv.aaa{background:var(--royal-light);border:1px solid var(--royal-dim);color:var(--royal)}
.conv.aa{background:var(--royal-light);border:1px solid var(--royal-dim);color:var(--royal-mid)}
.conv.a{background:var(--grn-bg);border:1px solid var(--grn-bd);color:var(--green)}
.conv.bbb,.conv.bb,.conv.b{background:var(--card);border:1px solid var(--border2);color:var(--sec)}
.scen-lbl{margin-left:auto;font-size:9.5px;color:var(--muted);font-family:var(--mono)}
.setup-body{padding:12px 16px;background:var(--white)}
.metrics-grid{display:grid;grid-template-columns:repeat(6,1fr);gap:5px;margin-bottom:11px;padding:9px;background:var(--card);border:1px solid var(--border);border-radius:var(--r)}
.metric{text-align:center;padding:3px 0}
.metric-lbl{font-size:8px;color:var(--muted);text-transform:uppercase;letter-spacing:.6px;font-family:var(--mono);margin-bottom:2px}
.metric-val{font-size:12px;font-weight:700;font-family:var(--mono)}
.metric-val.ok{color:var(--green)}.metric-val.warn{color:var(--royal)}.metric-val.danger{color:var(--red)}
.factor-grid{display:grid;grid-template-columns:repeat(8,1fr);gap:5px;margin-bottom:11px;padding:9px;background:var(--royal-light);border:1px solid var(--royal-dim);border-radius:var(--r)}
.factor{text-align:center}
.factor-lbl{font-size:7.5px;color:var(--royal);text-transform:uppercase;letter-spacing:.5px;font-family:var(--mono);margin-bottom:2px;font-weight:700}
.factor-val{font-size:12px;font-weight:700;font-family:var(--mono);color:var(--dark)}
.factor-val.miss{color:var(--muted);font-style:italic}
.factor.mean .factor-val{color:var(--royal)}
.px-grid{display:grid;grid-template-columns:repeat(5,1fr);gap:7px;margin-bottom:11px}
.px-card{background:var(--card);border:1px solid var(--border);border-radius:var(--r);padding:8px 10px;text-align:center}
.px-card.entry{border-top:2px solid var(--royal)}.px-card.sl{border-top:2px solid var(--red)}.px-card.tp1{border-top:2px solid var(--green)}.px-card.tp2{border-top:2px solid var(--royal-mid)}.px-card.rr{border-top:2px solid var(--royal-dim)}
.px-lbl{font-size:7.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.8px;font-weight:600;margin-bottom:3px;font-family:var(--mono)}
.px-val{font-size:14px;font-weight:700;font-family:var(--mono)}
.px-sub{font-size:8.5px;color:var(--muted);margin-top:2px}
.rationale{background:var(--royal-light);border-left:3px solid var(--royal);padding:9px 12px;font-size:11px;color:var(--dark);margin-bottom:10px;line-height:1.55;border-radius:var(--r)}
.rationale strong{display:block;font-size:8.5px;text-transform:uppercase;letter-spacing:1px;margin-bottom:4px;color:var(--royal);font-family:var(--mono)}
.flags-row{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:10px}
.flag{display:inline-flex;align-items:center;gap:4px;padding:2px 8px;border-radius:4px;font-size:9px;font-weight:700;font-family:var(--mono)}
.flag.minor{background:var(--royal-light);border:1px solid var(--royal-dim);color:var(--royal-mid)}
.flag.major{background:var(--red-bg);border:1px solid var(--red-bd);color:var(--red-tx)}
.cap-note{font-size:9.5px;color:var(--red);font-family:var(--mono);font-weight:600;margin-bottom:8px}
.cluster-tag{font-size:9px;font-family:var(--mono);color:var(--sec);background:var(--card);border:1px solid var(--border2);padding:1px 7px;border-radius:4px}
.cal-row{display:flex;align-items:center;gap:8px;font-size:10.5px;color:var(--sec);margin-bottom:10px}
.cal-ok,.cal-prox,.cal-proximity,.cal-blackout,.cal-watch{padding:2px 8px;border-radius:4px;font-size:9.5px;font-weight:700;font-family:var(--mono)}
.cal-ok{background:var(--grn-bg);border:1px solid var(--grn-bd);color:var(--grn-tx)}
.cal-watch,.cal-prox,.cal-proximity{background:var(--royal-light);border:1px solid var(--royal-dim);color:var(--royal-mid)}
.cal-blackout{background:var(--red-bg);border:1px solid var(--red-bd);color:var(--red-tx)}
.sub-lbl{font-size:8.5px;font-weight:700;color:var(--royal);text-transform:uppercase;letter-spacing:1px;margin:11px 0 7px;font-family:var(--mono)}
.sub-lbl:first-child{margin-top:0}
.elim{background:var(--card);border:1px solid var(--border);border-left:3px solid var(--border2);border-radius:var(--r);padding:8px 12px;margin-bottom:6px;display:flex;align-items:flex-start;gap:10px}
.elim.sus{border-left-color:var(--red);background:var(--red-bg)}
.elim-pair{font-size:12px;font-weight:700;font-family:var(--mono);color:var(--sec);min-width:84px;flex-shrink:0}
.elim-txt{font-size:10px;color:var(--muted)}
hr.div{border:none;border-top:1px solid var(--border);margin:9px 0}
table{width:100%;border-collapse:collapse;font-size:11px}
thead tr{background:var(--royal)!important}
thead th{padding:7px 10px;text-align:left;font-size:8.5px;font-weight:700;color:#fff;letter-spacing:.8px;text-transform:uppercase;white-space:nowrap;font-family:var(--mono)}
tbody tr{border-bottom:1px solid var(--border)}
tbody tr:nth-child(even){background:var(--card)}
tbody td{padding:5px 10px;vertical-align:middle}
.no-setup{background:var(--card);border:2px dashed var(--border2);border-radius:var(--rl);padding:36px 20px;text-align:center}
.no-setup-icon{font-size:32px;margin-bottom:10px}.no-setup-title{font-size:15px;font-weight:700;color:var(--dark);margin-bottom:6px}.no-setup-sub{font-size:11px;color:var(--muted);font-family:var(--mono)}
.reject-code{font-family:var(--mono);font-size:9.5px;font-weight:700;color:var(--red)}
.audit-block{background:#0d1f4e;color:#E8EEFF;border-radius:var(--r);padding:9px 12px;margin-top:10px;font-family:var(--mono);font-size:9px;line-height:1.55;word-break:break-word}
.audit-block strong{color:#6EE7B7;font-size:8.5px;text-transform:uppercase;letter-spacing:1px;display:block;margin-bottom:4px}
.footer{text-align:center;font-family:var(--mono);font-size:7.5px;color:var(--muted);border-top:1px solid var(--border);padding:9px 20px;margin-top:4px;letter-spacing:1.2px}
.page-header{background:linear-gradient(135deg,#F8FAFF 0%,#F0F4FE 100%);border:1px solid var(--border);border-radius:var(--rl) var(--rl) 0 0;display:flex;align-items:center;justify-content:space-between;padding:13px 24px;box-shadow:0 1px 4px rgba(13,31,78,.04),inset 0 1px 0 rgba(255,255,255,.8);position:relative}
.page-header::after{content:'';position:absolute;bottom:0;left:24px;right:24px;height:2px;background:linear-gradient(90deg,var(--royal),var(--royal-dim),transparent);border-radius:2px}
.header-left{display:flex;align-items:center;gap:14px}
.logo-marker{width:34px;height:34px;display:flex;align-items:center;justify-content:center;flex-shrink:0;background:var(--white);border:1px solid var(--border);border-radius:var(--r)}
.sys-label{font-size:8.5px;letter-spacing:.3em;color:var(--royal-dim);font-family:var(--mono);font-weight:600;text-transform:uppercase}
.sys-name{font-size:21px;font-weight:700;color:var(--dark);letter-spacing:-.02em;line-height:1.1;font-family:var(--mono)}
.sys-desc{font-size:8.5px;color:var(--muted);font-family:var(--mono);margin-top:2px;letter-spacing:.02em}
.header-right{text-align:right;border-left:1px solid var(--border2);padding-left:18px}
.briefing-label{font-size:10.5px;color:var(--royal);font-family:var(--mono);letter-spacing:.08em;font-weight:600;text-transform:uppercase}
.briefing-sub{font-size:8.5px;color:var(--sec);font-family:var(--mono);margin-top:4px;letter-spacing:.02em}
.page-subbar{background:rgba(27,69,180,.04);border-left:1px solid var(--border);border-right:1px solid var(--border);border-bottom:1px solid var(--border);padding:7px 24px;display:flex;align-items:center;gap:22px;flex-wrap:wrap;font-size:9.5px;font-family:var(--mono);color:var(--sec)}
.confidential{margin-left:auto;color:var(--royal);font-weight:600;background:rgba(27,69,180,.08);padding:2px 10px;border-radius:20px;font-size:8.5px}
/* Page sans running header, marge @page nulle, background étendu */
@page {
  size: A4 portrait;
  margin: 0;
  @bottom-center {
    content: "CONFIDENTIEL · BLUESTAR SYSTEM v10 HYBRID V4 · {{date_hdr}} · MAX {{max_setups}} SETUPS · RR ∈ [{{rr_min}}, {{rr_max}}]";
    font-family: 'IBM Plex Mono', monospace;
    font-size: 6pt;
    color: #6B89D8;
    letter-spacing: 0.2px;
  }
}
/* Impression : aucun running header, plus de bandeau contextuel en double */
@media print {
  * {
    -webkit-print-color-adjust: exact !important;
    print-color-adjust: exact !important;
  }
  html, body {
    background: var(--bg) !important;
    margin: 0 !important;
    padding: 0 !important;
    width: 100% !important;
  }
  #page {
    max-width: none !important;
    width: 100% !important;
    margin: 0 !important;
    padding: 0 !important;
    background: var(--bg) !important;
  }
  .wrap {
    padding: 10mm 12mm 12mm 12mm !important;
  }
  /* En-tête page 1 (normal flow) */
  .page-header {
    padding: 5px 10mm !important;
    border-radius: 0 !important;
    border-left: none !important;
    border-right: none !important;
    border-top: none !important;
  }
  .page-subbar {
    padding: 3px 10mm !important;
    gap: 8px !important;
    font-size: 6.5pt !important;
    border-left: none !important;
    border-right: none !important;
  }
  .sys-name { font-size: 12px !important; }
  .sys-label, .sys-desc, .briefing-label, .briefing-sub { font-size: 5.5pt !important; }
  .logo-marker { width: 20px !important; height: 20px !important; }

  /* sections, setups, grilles inchangés - identiques à avant */
  .section { overflow: visible !important; box-shadow: none !important; margin-bottom: 7px !important; border: 1px solid var(--border) !important; }
  .sec-body { padding: 7px 6px !important; }
  .sec-hdr { padding: 5px 10px !important; break-after: avoid !important; }
  .sec-num { width: 16px !important; height: 16px !important; font-size: 7pt !important; }
  .sec-ttl { font-size: 8pt !important; }
  .setup { break-inside: avoid !important; margin-bottom: 6px !important; }
  .setup-hdr { padding: 5px 10px !important; }
  .setup-body { padding: 6px 10px !important; }
  .pair { font-size: 12.5px !important; }
  .factor-grid, .metrics-grid { padding: 4px 6px !important; gap: 3px !important; margin-bottom: 5px !important; break-inside: avoid !important; }
  .factor-lbl, .metric-lbl { font-size: 6pt !important; }
  .factor-val, .metric-val { font-size: 9pt !important; }
  .px-grid { gap: 4px !important; margin-bottom: 5px !important; break-inside: avoid !important; }
  .px-card { padding: 4px 7px !important; }
  .rationale { padding: 5px 8px !important; margin-bottom: 5px !important; font-size: 6.8pt !important; }
  .audit-block { padding: 5px 8px !important; margin-top: 5px !important; font-size: 5.9pt !important; line-height: 1.32 !important; }
  .section + .section { break-before: page !important; }
  .sus-grid { grid-template-columns: repeat(3,1fr) !important; gap: 4px !important; }
  .sus-item { padding: 3px 7px !important; break-inside: avoid !important; }
  table { font-size: 7pt !important; }
  thead { display: table-header-group !important; }
  .footer { display: none !important; }  /* footer déjà dans @bottom-center */
  a[href]:after { content: "" !important; }
}
.sus-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:5px;margin-bottom:8px}
.sus-item{background:var(--red-bg);border:1px solid var(--red-bd);border-left:3px solid var(--red);border-radius:var(--r);padding:5px 9px;display:flex;flex-direction:column;gap:2px}
.sus-item-pair{font-family:var(--mono);font-weight:700;font-size:11px;color:var(--dark)}
.sus-item-txt{font-size:9px;color:var(--muted)}
</style>
</head>
<body>
<div id="page">
<div class="page-header">
  <div class="header-left">
    <div class="logo-marker"><svg width="28" height="28" viewBox="0 0 24 24" fill="none"><path d="M12 17.27L18.18 21L16.54 13.97L22 9.24L14.81 8.63L12 2L9.19 8.63L2 9.24L7.46 13.97L5.82 21L12 17.27Z" fill="#1B45B4"/></svg></div>
    <div><div class="sys-label">BLUESTAR SYSTEM</div><div class="sys-name">BLUESTAR</div><div class="sys-desc">FX INSTITUTIONAL DESK · v10 HYBRID V4</div></div>
  </div>
  <div class="header-right"><div class="briefing-label">FX CASCADE · TRADER</div><div class="briefing-sub">{{date_hdr}}</div></div>
</div>
<div class="page-subbar">
  <span>{{date_hdr}}</span><span>GMT+1</span>
  <span style="background:rgba(27,69,180,.12);color:var(--royal);padding:2px 10px;border-radius:20px;font-weight:700;border:1px solid var(--royal-dim)">{{n_setups}} setup(s)</span>
  <span>Universe <strong>{{n_passed}}/{{n_total}}</strong></span>
  <span>Event Risk : <strong style="color:{% if event_risk == 'High' %}var(--red){% elif event_risk == 'Medium' %}#EA580C{% else %}var(--green){% endif %}">{{event_risk}}</strong></span>
  {% if themes %}<span>Thèmes : {{themes}}</span>{% endif %}
  <span class="confidential">CONFIDENTIEL</span>
</div>
<div class="wrap">

<div class="section">
  <div class="sec-hdr"><div class="sec-num">1</div><div class="sec-ttl">Setups Valides</div><div class="sec-sub">{{n_setups}} validé(s) · Universe {{n_passed}}/{{n_total}}</div></div>
  <div class="sec-body">
  {% if sr_degraded %}<div class="banner">⚠ SR indisponible — niveaux en mode ATR synthétique (entrées Market, TP 2×ATR)</div>{% endif %}
  {% for s in setups %}
  {% set dc = 'long' if s.direction.value == 'Bullish' else 'short' %}
  {% set arrow = '▲' if s.direction.value == 'Bullish' else '▼' %}
  {% set cv = s.conviction.value|lower %}
  {% set fs = s.factor_scores %}
  <div class="setup {{cv}}">
    <div class="setup-hdr {{dc}}">
      <span class="pair">{{s.symbol}}</span>
      <span class="dir {{dc}}">{{arrow}} {{s.direction.value}}</span>
      <span class="conv {{cv}}">{{s.conviction.value}} ({{ '%.2f'|format(fs.absolute_mean) }})</span>
      <span class="cluster-tag">{{s.cluster}}</span>
      <span class="scen-lbl">{{s.scenario_hint}}{% if s.cal_status.value != 'OK' %} · {{s.cal_status.value}}{% endif %}</span>
    </div>
    <div class="setup-body">
      <div class="factor-grid">
        <div class="factor"><div class="factor-lbl">F1 HWA</div><div class="factor-val {% if 'f1_hwa' in fs.missing %}miss{% endif %}">{{ '%.2f'|format(fs.f1_hwa) }}</div></div>
        <div class="factor"><div class="factor-lbl">F2 RMG</div><div class="factor-val {% if 'f2_rmg' in fs.missing %}miss{% endif %}">{{ '%.2f'|format(fs.f2_rmg) }}</div></div>
        <div class="factor"><div class="factor-lbl">F3 EXT</div><div class="factor-val {% if 'f3_ext' in fs.missing %}miss{% endif %}">{{ '%.2f'|format(fs.f3_ext) }}</div></div>
        <div class="factor"><div class="factor-lbl">F4 TRG</div><div class="factor-val {% if 'f4_trg' in fs.missing %}miss{% endif %}">{{ '%.2f'|format(fs.f4_trg) }}</div></div>
        <div class="factor"><div class="factor-lbl">F5 XCTX</div><div class="factor-val {% if 'f5_xctx' in fs.missing %}miss{% endif %}">{{ '%.2f'|format(fs.f5_xctx) }}</div></div>
        <div class="factor"><div class="factor-lbl">F6 THM</div><div class="factor-val {% if 'f6_theme' in fs.missing %}miss{% endif %}">{{ '%.2f'|format(fs.f6_theme) }}</div></div>
        <div class="factor"><div class="factor-lbl">F7 MAC</div><div class="factor-val {% if 'f7_macro' in fs.missing %}miss{% endif %}">{{ '%.2f'|format(fs.f7_macro) }}</div></div>
        <div class="factor mean"><div class="factor-lbl">Q-rang</div><div class="factor-val">{{ '%.2f'|format(fs.quantile) }}</div></div>
      </div>
      <div class="metrics-grid">
        <div class="metric"><div class="metric-lbl">Distance ATR</div><div class="metric-val {% if (s.distance_atr or 0) <= 0.3 %}ok{% elif (s.distance_atr or 0) <= 1.0 %}warn{% else %}danger{% endif %}">{{s.distance_atr|round(2)}}×</div></div>
        <div class="metric"><div class="metric-lbl">Score CHoCH</div><div class="metric-val {% if (s.choch_score or 0) >= 70 %}ok{% elif (s.choch_score or 0) >= 50 %}warn{% else %}danger{% endif %}">{{s.choch_score|round(0)|int if s.choch_score else '—'}}</div></div>
        <div class="metric"><div class="metric-lbl">Quality</div><div class="metric-val {% if s.gps_quality in ['A+','A'] %}ok{% else %}warn{% endif %}">{{s.gps_quality or '—'}}</div></div>
        <div class="metric"><div class="metric-lbl">MTF %</div><div class="metric-val {% if s.mtf_pct >= 85 %}ok{% elif s.mtf_pct >= 60 %}warn{% else %}danger{% endif %}">{{s.mtf_pct}}%</div></div>
        <div class="metric"><div class="metric-lbl">RSI H4</div><div class="metric-val {% if s.rsi_h4_status == 'favorable' %}ok{% elif 'extreme' in (s.rsi_h4_status or '') %}danger{% else %}warn{% endif %}">{{s.rsi_h4|round(1) if s.rsi_h4 else '—'}}</div></div>
        <div class="metric"><div class="metric-lbl">Age</div><div class="metric-val {% if s.age_d1 <= 15 %}ok{% elif s.age_d1 <= 30 %}warn{% else %}danger{% endif %}">{{s.age_d1}}j</div></div>
      </div>
      <div class="px-grid">
        <div class="px-card entry"><div class="px-lbl">Entry</div><div class="px-val" style="color:var(--royal)">{{s.entry}}</div><div class="px-sub">{{s.entry_type}}</div></div>
        <div class="px-card sl"><div class="px-lbl">Stop Loss</div><div class="px-val" style="color:var(--red)">{{s.sl}}</div><div class="px-sub">{{s.sl_atr_multiple|round(1)}}×ATR</div></div>
        <div class="px-card tp1"><div class="px-lbl">TP1 (60%)</div><div class="px-val" style="color:var(--green)">{{s.tp1}}</div><div class="px-sub">{% if s.tp1_atr_multiple %}{{s.tp1_atr_multiple}}×ATR{% else %}synth{% endif %}</div></div>
        <div class="px-card tp2"><div class="px-lbl">TP2 (40%)</div><div class="px-val" style="color:var(--blue)">{{s.tp2 if s.tp2 else '—'}}</div><div class="px-sub">{% if s.tp2_atr_multiple %}{{s.tp2_atr_multiple}}×ATR{% else %}synth{% endif %}</div></div>
        <div class="px-card rr"><div class="px-lbl">R : R</div><div class="px-val" style="color:var(--purple)">{{s.rr|round(2)}}</div><div class="px-sub">pondéré 60/40</div></div>
      </div>
      {% if s.flags %}<div class="flags-row">{% for f in s.flags %}<span class="flag {{f.severity}}">{{f.code}} · {{f.detail}}</span>{% endfor %}</div>{% endif %}
      {% if s.capped_reason %}<div class="cap-note">Plafond conviction appliqué : {{s.capped_reason}}</div>{% endif %}
      <div class="rationale"><strong>Rationale</strong>{{s.rationale}}{% if s.cal_note %} · <em>{{s.cal_note}}</em>{% endif %}</div>
      <div class="cal-row"><span class="cal-{{s.cal_status.value|lower}}">{{s.cal_status.value}}</span>{% if s.cal_note %}<span>{{s.cal_note}}</span>{% endif %}</div>
      <div class="audit-block"><strong>Audit Trail</strong>{{s.sl_detail}}<br>{{s.rr_detail}}<br>absolute_mean={{ '%.4f'|format(fs.absolute_mean) }} · quantile={{ '%.4f'|format(fs.quantile) }} · missing={{fs.missing}}<br>{% for k,v in fs.details.items() %}{{v}}<br>{% endfor %}ATR={{s.atr_source}} · cluster={{s.cluster}} · htf={{s.htf_aligned}}</div>
    </div>
  </div>
  {% endfor %}
  {% if not setups %}
  <div class="no-setup"><div class="no-setup-icon">∅</div><div class="no-setup-title">Aucun setup conforme aujourd'hui</div><div class="no-setup-sub">Event Risk : {{event_risk}} · Universe {{n_passed}}/{{n_total}}</div></div>
  {% endif %}
  </div>
</div>

<div class="section">
  <div class="sec-hdr"><div class="sec-num">2</div><div class="sec-ttl">Éliminés &amp; Surveillance</div><div class="sec-sub">{{elimines|length}} actif(s) filtré(s)</div></div>
  <div class="sec-body">
  {% set suspendus = elimines | selectattr('reject_code', 'equalto', 'CAL_BLACKOUT') | list %}
  {% set rejets = elimines | rejectattr('reject_code', 'equalto', 'CAL_BLACKOUT') | list %}
  {% if suspendus %}
  <div class="sub-lbl">SUSPENDUS — Calendrier ({{suspendus|length}})</div>
  <div class="sus-grid">
  {% for e in suspendus %}
  <div class="sus-item"><span class="sus-item-pair">{{e.symbol}}</span><span class="sus-item-txt">RSI H4 : {{e.rsi_h4|round(2) if e.rsi_h4 else '—'}} · Age : {{e.age_d1}}j</span></div>
  {% endfor %}
  </div>
  <hr class="div">
  {% endif %}
  {% if rejets %}
  <div class="sub-lbl">REJETS — Filtre / Preflight / Cluster ({{rejets|length}})</div>
  <table>
    <thead><tr><th>Paire</th><th>Dir.</th><th>Code</th><th>Détail</th><th>RSI H4</th><th>Age</th><th>Cal.</th></tr></thead>
    <tbody>
    {% for e in rejets %}
    {% set dc = 'long' if e.direction.value == 'Bullish' else 'short' %}
    <tr><td style="font-family:var(--mono);font-weight:700">{{e.symbol}}</td><td><span class="dir {{dc}}" style="font-size:9.5px;padding:1px 6px">{{e.direction.value}}</span></td><td class="reject-code">{{e.reject_code}}</td><td style="font-size:10px">{{e.reject_detail}}</td><td style="font-family:var(--mono);font-size:10px">{{e.rsi_h4|round(2) if e.rsi_h4 else '—'}}</td><td style="font-family:var(--mono);font-size:10px">{{e.age_d1}}j</td><td><span class="cal-{{e.cal_status.value|lower}}" style="font-size:9.5px;padding:1px 6px">{{e.cal_status.value}}</span></td></tr>
    {% endfor %}
    </tbody>
  </table>
  {% endif %}
  {% if not elimines %}<div style="padding:14px;color:var(--muted);font-style:italic;font-size:11px">Aucun actif éliminé ce cycle.</div>{% endif %}
  </div>
</div>

</div><!-- /.wrap -->
</div><!-- /#page -->
</body>
</html>"""
