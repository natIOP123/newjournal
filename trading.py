"""
Trading Journal - Full Stack App (PostgreSQL / psycopg2 version)
================================================================
Single-file Flask app with:
  - Embedded HTML/CSS/JS frontend (dark, professional trading aesthetic)
  - PostgreSQL backend via psycopg2 (Supabase or any PG instance)
  - Local screenshot upload & storage (served statically)
  - PDF report generation with charts via ReportLab + Matplotlib

SETUP
-----
1. pip install -r requirements.txt

2. Set the environment variable (Railway: Variables tab, local: .env file):
      DATABASE_URL=postgresql://user:password@host:port/dbname
      SECRET_KEY=any-random-secret

   For Supabase, get the connection string from:
   Settings → Database → Connection string → URI

3. The app will automatically create the 'trades' table on first startup.

4. Run:  python trading.py  → open http://localhost:8080
"""

import os, io, uuid, json, traceback, pathlib
from datetime import datetime, timedelta
from collections import defaultdict

from flask import Flask, request, jsonify, send_file, abort
from dotenv import load_dotenv

# Load .env from the same directory as this script
_HERE = pathlib.Path(__file__).parent.resolve()
load_dotenv(dotenv_path=_HERE / ".env")

# ── PostgreSQL via psycopg2 ───────────────────────────────────────────────────
import psycopg2
from psycopg2 import pool, sql

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
if not DATABASE_URL:
    print("❌  DATABASE_URL is not set.")
    print("   Set it in your environment or .env file.")
    print("   Example: postgresql://user:password@host:port/dbname")
    print("   The app will start but database features will not work.")
    db_pool = None
else:
    try:
        db_pool = pool.SimpleConnectionPool(1, 10, DATABASE_URL)
        # Test connection and create table if needed
        conn = db_pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS trades (
                        id            UUID DEFAULT gen_random_uuid() PRIMARY KEY,
                        created_at    TIMESTAMPTZ DEFAULT NOW(),
                        date          DATE NOT NULL,
                        symbol        TEXT NOT NULL,
                        direction     TEXT CHECK (direction IN ('LONG','SHORT')) NOT NULL,
                        entry_price   NUMERIC(18,6) NOT NULL,
                        exit_price    NUMERIC(18,6) NOT NULL,
                        quantity      NUMERIC(18,6) NOT NULL,
                        strategy      TEXT,
                        session       TEXT,
                        emotions      TEXT,
                        notes         TEXT,
                        pnl           NUMERIC(18,2),
                        pnl_pct       NUMERIC(10,4),
                        screenshot_url TEXT
                    );
                """)
                conn.commit()
        finally:
            db_pool.putconn(conn)
        print("✅  Database connected and 'trades' table ready.")
    except Exception as e:
        print(f"❌  Database connection failed: {e}")
        db_pool = None

# ── PDF libs ──────────────────────────────────────────────────────────────────
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors as rl_colors
from reportlab.lib.units import cm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                 TableStyle, Image as RLImage, PageBreak,
                                 HRFlowable)
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from PIL import Image as PILImage
import requests as http_req

# ── Flask App ─────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-secret-xyz")

# Local uploads folder (screenshots will be stored here)
UPLOAD_FOLDER = pathlib.Path("uploads")
UPLOAD_FOLDER.mkdir(exist_ok=True)

# Serve uploaded files statically
@app.route("/uploads/<filename>")
def uploaded_file(filename):
    return send_file(UPLOAD_FOLDER / filename)

# ═════════════════════════════════════════════════════════════════════════════
#  FRONTEND HTML (identical to original, but screenshot URLs now /uploads/...)
# ═════════════════════════════════════════════════════════════════════════════
HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>TradeLog — Personal Trading Journal</title>
<link href="https://fonts.googleapis.com/css2?family=Space+Mono:ital,wght@0,400;0,700;1,400&family=DM+Sans:wght@300;400;500;600&display=swap" rel="stylesheet"/>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css"/>
<style>
:root{
  --bg:#080c10;--surface:#0e1318;--surface2:#141b22;--surface3:#1a2330;
  --border:#1e2d3d;--border2:#243444;
  --green:#00e5a0;--red:#ff4d6d;--blue:#3b9eff;--yellow:#f5c842;--purple:#a78bfa;
  --text:#e2e8f0;--text2:#8899aa;--text3:#4a5568;
  --font-mono:'Space Mono',monospace;--font-sans:'DM Sans',sans-serif;
  --radius:8px;--radius-lg:14px;
}
*{margin:0;padding:0;box-sizing:border-box;}
html{scroll-behavior:smooth;}
body{background:var(--bg);color:var(--text);font-family:var(--font-sans);min-height:100vh;overflow-x:hidden;}
body::before{content:'';position:fixed;inset:0;background-image:url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.03'/%3E%3C/svg%3E");opacity:.4;pointer-events:none;z-index:0;}
.config-banner{background:rgba(245,200,66,.08);border:1px solid rgba(245,200,66,.25);border-radius:var(--radius);padding:1rem 1.25rem;margin-bottom:1.5rem;display:none;align-items:flex-start;gap:.75rem;}
.config-banner.show{display:flex;}
.config-banner i{color:var(--yellow);margin-top:.1rem;flex-shrink:0;}
.config-banner-text{font-size:.85rem;color:var(--text2);line-height:1.6;}
.config-banner-text strong{color:var(--yellow);}
.config-banner-text code{font-family:var(--font-mono);background:var(--surface2);padding:.1rem .35rem;border-radius:3px;font-size:.78rem;color:var(--text);}
nav{position:sticky;top:0;z-index:100;background:rgba(8,12,16,.92);backdrop-filter:blur(12px);border-bottom:1px solid var(--border);display:flex;align-items:center;justify-content:space-between;padding:0 2rem;height:62px;}
.logo{font-family:var(--font-mono);font-size:1.1rem;color:var(--green);letter-spacing:.05em;}
.logo span{color:var(--text2);}
.nav-tabs{display:flex;gap:.25rem;}
.nav-tab{background:none;border:none;color:var(--text2);font-family:var(--font-sans);font-size:.88rem;font-weight:500;padding:.45rem 1rem;border-radius:var(--radius);cursor:pointer;transition:all .2s;}
.nav-tab:hover{color:var(--text);background:var(--surface2);}
.nav-tab.active{color:var(--green);background:rgba(0,229,160,.08);}
.nav-actions{display:flex;gap:.5rem;}
.btn{display:inline-flex;align-items:center;gap:.45rem;padding:.45rem 1.1rem;border-radius:var(--radius);font-family:var(--font-sans);font-size:.85rem;font-weight:600;cursor:pointer;border:none;transition:all .2s;}
.btn-primary{background:var(--green);color:#080c10;}
.btn-primary:hover{background:#00ffb3;transform:translateY(-1px);}
.btn-ghost{background:transparent;color:var(--text2);border:1px solid var(--border2);}
.btn-ghost:hover{color:var(--text);border-color:var(--text3);background:var(--surface2);}
.btn-danger{background:rgba(255,77,109,.12);color:var(--red);border:1px solid rgba(255,77,109,.25);}
.btn-danger:hover{background:rgba(255,77,109,.2);}
.btn-sm{padding:.3rem .75rem;font-size:.8rem;}
main{max-width:1400px;margin:0 auto;padding:2rem;position:relative;z-index:1;}
.page{display:none;}
.page.active{display:block;}
.stats-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:1rem;margin-bottom:2rem;}
.stat-card{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius-lg);padding:1.4rem;position:relative;overflow:hidden;transition:border-color .2s;}
.stat-card:hover{border-color:var(--border2);}
.stat-card::before{content:'';position:absolute;inset:0;background:linear-gradient(135deg,rgba(255,255,255,.02),transparent);pointer-events:none;}
.stat-label{font-size:.75rem;font-weight:600;color:var(--text2);text-transform:uppercase;letter-spacing:.08em;margin-bottom:.6rem;}
.stat-value{font-family:var(--font-mono);font-size:1.7rem;font-weight:700;}
.stat-sub{font-size:.78rem;color:var(--text3);margin-top:.3rem;}
.pos{color:var(--green);}
.neg{color:var(--red);}
.neut{color:var(--blue);}
.charts-grid{display:grid;grid-template-columns:1fr 1fr;gap:1.5rem;margin-bottom:2rem;}
.chart-card{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius-lg);padding:1.5rem;}
.chart-title{font-size:.82rem;font-weight:700;color:var(--text2);text-transform:uppercase;letter-spacing:.1em;margin-bottom:1rem;}
canvas{width:100%!important;}
.section-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:1.25rem;}
.section-title{font-family:var(--font-mono);font-size:1rem;color:var(--text);}
.filter-bar{display:flex;gap:.75rem;align-items:center;margin-bottom:1.25rem;flex-wrap:wrap;}
.filter-bar input,.filter-bar select{background:var(--surface);border:1px solid var(--border);color:var(--text);border-radius:var(--radius);padding:.4rem .8rem;font-size:.83rem;font-family:var(--font-sans);}
.filter-bar input:focus,.filter-bar select:focus{outline:none;border-color:var(--blue);}
table{width:100%;border-collapse:collapse;}
thead th{font-size:.72rem;font-weight:700;color:var(--text3);text-transform:uppercase;letter-spacing:.1em;padding:.75rem 1rem;text-align:left;border-bottom:1px solid var(--border);}
tbody tr{border-bottom:1px solid var(--border);transition:background .15s;cursor:pointer;}
tbody tr:hover{background:var(--surface2);}
tbody td{padding:.85rem 1rem;font-size:.88rem;}
.badge{display:inline-flex;align-items:center;padding:.2rem .55rem;border-radius:4px;font-family:var(--font-mono);font-size:.7rem;font-weight:700;}
.badge-long{background:rgba(0,229,160,.12);color:var(--green);}
.badge-short{background:rgba(255,77,109,.12);color:var(--red);}
.table-wrap{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius-lg);overflow:auto;}
.empty-state{text-align:center;padding:4rem 2rem;color:var(--text3);}
.empty-state i{font-size:2.5rem;margin-bottom:1rem;display:block;}
.modal-overlay{position:fixed;inset:0;background:rgba(0,0,0,.75);backdrop-filter:blur(6px);z-index:200;display:none;align-items:center;justify-content:center;}
.modal-overlay.open{display:flex;}
.modal{background:var(--surface);border:1px solid var(--border2);border-radius:var(--radius-lg);width:min(680px,96vw);max-height:92vh;overflow-y:auto;padding:2rem;}
.modal-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:1.75rem;}
.modal-title{font-family:var(--font-mono);font-size:1rem;color:var(--green);}
.close-btn{background:none;border:none;color:var(--text2);font-size:1.2rem;cursor:pointer;padding:.25rem;transition:color .2s;}
.close-btn:hover{color:var(--text);}
.form-grid{display:grid;grid-template-columns:1fr 1fr;gap:1rem;}
.form-group{display:flex;flex-direction:column;gap:.5rem;}
.form-group.full{grid-column:1/-1;}
label{font-size:.78rem;font-weight:600;color:var(--text2);text-transform:uppercase;letter-spacing:.07em;}
input,select,textarea{background:var(--surface2);border:1px solid var(--border);color:var(--text);border-radius:var(--radius);padding:.6rem .85rem;font-family:var(--font-sans);font-size:.9rem;transition:border-color .2s;width:100%;}
input:focus,select:focus,textarea:focus{outline:none;border-color:var(--blue);background:var(--surface3);}
textarea{resize:vertical;min-height:80px;}
select option{background:var(--surface2);}
.drop-zone{border:2px dashed var(--border2);border-radius:var(--radius-lg);padding:2rem;text-align:center;cursor:pointer;transition:all .2s;position:relative;}
.drop-zone:hover,.drop-zone.dragover{border-color:var(--blue);background:rgba(59,158,255,.05);}
.drop-zone input[type=file]{position:absolute;inset:0;opacity:0;cursor:pointer;}
.drop-icon{font-size:2rem;color:var(--text3);margin-bottom:.5rem;}
.drop-text{font-size:.85rem;color:var(--text2);}
.preview-img{max-width:100%;max-height:200px;border-radius:var(--radius);margin-top:1rem;display:none;}
.detail-grid{display:grid;grid-template-columns:1fr 1fr;gap:1.25rem;}
.detail-field{background:var(--surface2);border-radius:var(--radius);padding:1rem;}
.detail-label{font-size:.7rem;font-weight:700;color:var(--text3);text-transform:uppercase;letter-spacing:.1em;margin-bottom:.4rem;}
.detail-value{font-family:var(--font-mono);font-size:.95rem;}
.screenshot-frame{width:100%;border-radius:var(--radius);overflow:hidden;background:var(--surface2);border:1px solid var(--border);margin-top:1rem;}
.screenshot-frame img{width:100%;display:block;}
.no-screenshot{padding:2rem;text-align:center;color:var(--text3);font-size:.85rem;}
.spinner{display:inline-block;width:1.1rem;height:1.1rem;border:2px solid rgba(255,255,255,.2);border-top-color:currentColor;border-radius:50%;animation:spin .7s linear infinite;}
@keyframes spin{to{transform:rotate(360deg);}}
.toast-container{position:fixed;bottom:1.5rem;right:1.5rem;z-index:500;display:flex;flex-direction:column;gap:.5rem;}
.toast{background:var(--surface2);border:1px solid var(--border2);border-radius:var(--radius);padding:.75rem 1.1rem;font-size:.85rem;display:flex;align-items:center;gap:.6rem;animation:slideIn .3s ease;min-width:260px;}
.toast.success{border-left:3px solid var(--green);}
.toast.error{border-left:3px solid var(--red);}
.toast.info{border-left:3px solid var(--blue);}
.toast.warning{border-left:3px solid var(--yellow);}
@keyframes slideIn{from{transform:translateX(100%);opacity:0;}to{transform:translateX(0);opacity:1;}}
::-webkit-scrollbar{width:6px;height:6px;}
::-webkit-scrollbar-track{background:transparent;}
::-webkit-scrollbar-thumb{background:var(--border2);border-radius:3px;}
@media(max-width:768px){
  .charts-grid{grid-template-columns:1fr;}
  .form-grid{grid-template-columns:1fr;}
  main{padding:1rem;}
  nav{padding:0 1rem;}
}
</style>
</head>
<body>

<!-- NAV -->
<nav>
  <div class="logo">Trade<span>Log</span></div>
  <div class="nav-tabs">
    <button class="nav-tab active" onclick="showPage('dashboard',event)"><i class="fa-solid fa-chart-line"></i> Dashboard</button>
    <button class="nav-tab" onclick="showPage('journal',event)"><i class="fa-solid fa-book"></i> Journal</button>
  </div>
  <div class="nav-actions">
    <button class="btn btn-ghost btn-sm" onclick="generatePDF()"><i class="fa-solid fa-file-pdf"></i> Export PDF</button>
    <button class="btn btn-primary btn-sm" onclick="openAddModal()"><i class="fa-solid fa-plus"></i> New Trade</button>
  </div>
</nav>

<!-- MAIN -->
<main>

  <!-- CONFIG BANNER (shown when DB not connected) -->
  <div class="config-banner" id="config-banner">
    <i class="fa-solid fa-triangle-exclamation"></i>
    <div class="config-banner-text" id="config-banner-text">
      <!-- Error message inserted dynamically -->
    </div>
  </div>

  <!-- DASHBOARD -->
  <div class="page active" id="page-dashboard">
    <div class="stats-grid" id="stats-grid">
      <div class="stat-card"><div class="stat-label">Total Trades</div><div class="stat-value neut" id="s-total">—</div></div>
      <div class="stat-card"><div class="stat-label">Win Rate</div><div class="stat-value" id="s-winrate">—</div></div>
      <div class="stat-card"><div class="stat-label">Net P&amp;L</div><div class="stat-value" id="s-pnl">—</div></div>
      <div class="stat-card"><div class="stat-label">Avg Win</div><div class="stat-value pos" id="s-avgwin">—</div></div>
      <div class="stat-card"><div class="stat-label">Avg Loss</div><div class="stat-value neg" id="s-avgloss">—</div></div>
      <div class="stat-card"><div class="stat-label">Profit Factor</div><div class="stat-value" id="s-pf">—</div></div>
    </div>
    <div class="charts-grid">
      <div class="chart-card"><div class="chart-title">Cumulative P&amp;L</div><canvas id="chart-equity" height="200"></canvas></div>
      <div class="chart-card"><div class="chart-title">Win / Loss by Symbol</div><canvas id="chart-symbol" height="200"></canvas></div>
      <div class="chart-card"><div class="chart-title">Daily P&amp;L</div><canvas id="chart-daily" height="200"></canvas></div>
      <div class="chart-card"><div class="chart-title">Trade Distribution</div><canvas id="chart-dist" height="200"></canvas></div>
    </div>
  </div>

  <!-- JOURNAL -->
  <div class="page" id="page-journal">
    <div class="section-header">
      <span class="section-title">Trade Journal</span>
    </div>
    <div class="filter-bar">
      <input type="text" id="filter-symbol" placeholder="Symbol…" oninput="filterTrades()"/>
      <select id="filter-dir" onchange="filterTrades()">
        <option value="">All Directions</option>
        <option value="LONG">LONG</option>
        <option value="SHORT">SHORT</option>
      </select>
      <input type="date" id="filter-from" onchange="filterTrades()"/>
      <input type="date" id="filter-to" onchange="filterTrades()"/>
      <button class="btn btn-ghost btn-sm" onclick="clearFilters()">Clear</button>
    </div>
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th>Date</th><th>Symbol</th><th>Direction</th>
          <th>Entry</th><th>Exit</th><th>Qty</th><th>P&amp;L</th><th>Strategy</th><th></th>
        </tr></thead>
        <tbody id="trades-tbody"></tbody>
      </table>
      <div class="empty-state" id="empty-state" style="display:none"><i class="fa-solid fa-magnifying-glass-chart"></i>No trades yet. Add your first trade!</div>
    </div>
  </div>

</main>

<!-- ADD TRADE MODAL -->
<div class="modal-overlay" id="add-modal">
  <div class="modal">
    <div class="modal-header">
      <span class="modal-title">// ADD TRADE</span>
      <button class="close-btn" onclick="closeAddModal()"><i class="fa-solid fa-xmark"></i></button>
    </div>
    <div class="form-grid">
      <div class="form-group"><label>Date *</label><input type="date" id="f-date" required/></div>
      <div class="form-group"><label>Symbol *</label><input type="text" id="f-symbol" placeholder="BTCUSDT, EURUSD…" required/></div>
      <div class="form-group"><label>Direction *</label>
        <select id="f-dir"><option value="LONG">LONG</option><option value="SHORT">SHORT</option></select>
      </div>
      <div class="form-group"><label>Quantity *</label><input type="number" id="f-qty" step="any" placeholder="0.00" required/></div>
      <div class="form-group"><label>Entry Price *</label><input type="number" id="f-entry" step="any" placeholder="0.00" required/></div>
      <div class="form-group"><label>Exit Price *</label><input type="number" id="f-exit" step="any" placeholder="0.00" required/></div>
      <div class="form-group"><label>Strategy</label><input type="text" id="f-strategy" placeholder="Breakout, Reversal…"/></div>
      <div class="form-group"><label>Session</label>
        <select id="f-session"><option value="">—</option><option>London</option><option>New York</option><option>Asia</option><option>Overlap</option></select>
      </div>
      <div class="form-group"><label>Emotions</label>
        <select id="f-emotions"><option value="">—</option><option>Calm</option><option>Fearful</option><option>Greedy</option><option>Confident</option><option>Uncertain</option><option>FOMO</option></select>
      </div>
      <div class="form-group full"><label>Notes</label><textarea id="f-notes" placeholder="Trade reasoning, market context…"></textarea></div>
      <div class="form-group full">
        <label>Screenshot</label>
        <div class="drop-zone" id="drop-zone">
          <input type="file" id="f-screenshot" accept="image/*" onchange="previewImage(this)"/>
          <div class="drop-icon"><i class="fa-solid fa-image"></i></div>
          <div class="drop-text">Drop chart screenshot here or click to browse</div>
          <img class="preview-img" id="preview-img"/>
        </div>
      </div>
    </div>
    <div style="display:flex;justify-content:flex-end;gap:.75rem;margin-top:1.5rem;">
      <button class="btn btn-ghost" onclick="closeAddModal()">Cancel</button>
      <button class="btn btn-primary" id="save-btn" onclick="saveTrade()"><i class="fa-solid fa-floppy-disk"></i> Save Trade</button>
    </div>
  </div>
</div>

<!-- TRADE DETAIL MODAL -->
<div class="modal-overlay" id="detail-modal">
  <div class="modal">
    <div class="modal-header">
      <span class="modal-title" id="detail-title">// TRADE DETAIL</span>
      <div style="display:flex;gap:.5rem;align-items:center;">
        <button class="btn btn-danger btn-sm" id="delete-btn"><i class="fa-solid fa-trash"></i> Delete</button>
        <button class="close-btn" onclick="closeDetailModal()"><i class="fa-solid fa-xmark"></i></button>
      </div>
    </div>
    <div class="detail-grid" id="detail-content"></div>
    <div class="screenshot-frame" id="detail-screenshot"></div>
  </div>
</div>

<!-- TOAST CONTAINER -->
<div class="toast-container" id="toast-container"></div>

<!-- Chart.js CDN -->
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<script>
// ── STATE ──────────────────────────────────────────────────────────────────
let allTrades = [];
let charts = {};

// ── NAV ───────────────────────────────────────────────────────────────────
function showPage(id, event){
  document.querySelectorAll('.page').forEach(p=>p.classList.remove('active'));
  document.getElementById('page-'+id).classList.add('active');
  document.querySelectorAll('.nav-tab').forEach(t=>t.classList.remove('active'));
  if(event && event.currentTarget) event.currentTarget.classList.add('active');
  if(id==='dashboard') buildDashboard(allTrades);
}

// ── TOAST ─────────────────────────────────────────────────────────────────
function toast(msg, type='info'){
  const c=document.getElementById('toast-container');
  const el=document.createElement('div');
  el.className=`toast ${type}`;
  const icons={success:'fa-circle-check',error:'fa-circle-xmark',info:'fa-circle-info',warning:'fa-triangle-exclamation'};
  el.innerHTML=`<i class="fa-solid ${icons[type]||icons.info}"></i>${msg}`;
  c.appendChild(el);
  setTimeout(()=>el.remove(),5000);
}

// ── HEALTH CHECK & BANNER ─────────────────────────────────────────────────
async function checkHealth(){
  try{
    const r = await fetch('/api/health');
    const d = await r.json();
    if(!d.ok){
      const banner = document.getElementById('config-banner');
      const textEl = document.getElementById('config-banner-text');
      textEl.innerHTML = `<strong>Database Error:</strong> ${d.error}.<br/>
        Make sure you have set <code>DATABASE_URL</code> correctly
        and that the <code>trades</code> table exists.`;
      banner.classList.add('show');
    }
  } catch(e){
    // network error
  }
}

// ── DATA ──────────────────────────────────────────────────────────────────
async function loadTrades(){
  try{
    const r=await fetch('/api/trades');
    const d=await r.json();
    if(d.error){
      const banner = document.getElementById('config-banner');
      const textEl = document.getElementById('config-banner-text');
      textEl.innerHTML = `<strong>${d.error}</strong><br/>
        Please check your DATABASE_URL and ensure the <code>trades</code> table exists.`;
      banner.classList.add('show');
      toast(d.error,'error');
      return;
    }
    allTrades=d.trades||[];
    renderTable(allTrades);
    buildDashboard(allTrades);
  }catch(e){
    toast('Failed to load trades: '+e.message,'error');
  }
}

// ── TABLE ─────────────────────────────────────────────────────────────────
function renderTable(trades){
  const tbody=document.getElementById('trades-tbody');
  const empty=document.getElementById('empty-state');
  if(!trades.length){tbody.innerHTML='';empty.style.display='block';return;}
  empty.style.display='none';
  tbody.innerHTML=trades.map(t=>{
    const pnl=parseFloat(t.pnl)||0;
    const pnlClass=pnl>=0?'pos':'neg';
    const pnlStr=(pnl>=0?'+':'')+pnl.toFixed(2);
    return `<tr onclick="openDetailModal('${t.id}')">
      <td>${t.date}</td>
      <td style="font-family:var(--font-mono);font-weight:700">${t.symbol}</td>
      <td><span class="badge badge-${t.direction.toLowerCase()}">${t.direction}</span></td>
      <td style="font-family:var(--font-mono)">${parseFloat(t.entry_price).toFixed(4)}</td>
      <td style="font-family:var(--font-mono)">${parseFloat(t.exit_price).toFixed(4)}</td>
      <td style="font-family:var(--font-mono)">${t.quantity}</td>
      <td style="font-family:var(--font-mono)" class="${pnlClass}">${pnlStr}</td>
      <td style="color:var(--text2);font-size:.82rem">${t.strategy||'—'}</td>
      <td><button class="btn btn-ghost btn-sm" onclick="event.stopPropagation();deleteTrade('${t.id}')"><i class="fa-solid fa-trash"></i></button></td>
    </tr>`;
  }).join('');
}

function filterTrades(){
  const sym=document.getElementById('filter-symbol').value.toLowerCase();
  const dir=document.getElementById('filter-dir').value;
  const from=document.getElementById('filter-from').value;
  const to=document.getElementById('filter-to').value;
  const filtered=allTrades.filter(t=>{
    if(sym && !t.symbol.toLowerCase().includes(sym)) return false;
    if(dir && t.direction!==dir) return false;
    if(from && t.date<from) return false;
    if(to && t.date>to) return false;
    return true;
  });
  renderTable(filtered);
}
function clearFilters(){
  ['filter-symbol','filter-from','filter-to'].forEach(id=>document.getElementById(id).value='');
  document.getElementById('filter-dir').value='';
  renderTable(allTrades);
}

// ── DASHBOARD ─────────────────────────────────────────────────────────────
function buildDashboard(trades){
  if(!trades.length) return;
  const pnls=trades.map(t=>parseFloat(t.pnl)||0);
  const wins=pnls.filter(p=>p>0), losses=pnls.filter(p=>p<0);
  const total=trades.length;
  const winRate=total?((wins.length/total)*100).toFixed(1):0;
  const netPnl=pnls.reduce((a,b)=>a+b,0);
  const avgWin=wins.length?(wins.reduce((a,b)=>a+b,0)/wins.length).toFixed(2):0;
  const avgLoss=losses.length?(Math.abs(losses.reduce((a,b)=>a+b,0)/losses.length)).toFixed(2):0;
  const grossWin=wins.reduce((a,b)=>a+b,0);
  const grossLoss=Math.abs(losses.reduce((a,b)=>a+b,0));
  const pf=grossLoss?(grossWin/grossLoss).toFixed(2):'∞';

  const s=id=>document.getElementById(id);
  s('s-total').textContent=total;
  s('s-winrate').textContent=winRate+'%';
  s('s-winrate').className='stat-value '+(winRate>=50?'pos':'neg');
  s('s-pnl').textContent=(netPnl>=0?'+':'')+netPnl.toFixed(2);
  s('s-pnl').className='stat-value '+(netPnl>=0?'pos':'neg');
  s('s-avgwin').textContent='+'+avgWin;
  s('s-avgloss').textContent='-'+avgLoss;
  s('s-pf').textContent=pf;
  s('s-pf').className='stat-value '+(parseFloat(pf)>=1?'pos':'neg');

  buildEquityChart(trades);
  buildSymbolChart(trades);
  buildDailyChart(trades);
  buildDistChart(wins.length, losses.length);
}

function chartOptions(){
  return{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false},tooltip:{backgroundColor:'#1a2330',borderColor:'#243444',borderWidth:1,titleColor:'#e2e8f0',bodyColor:'#8899aa'}},scales:{x:{grid:{color:'rgba(255,255,255,.04)'},ticks:{color:'#8899aa',font:{size:10}}},y:{grid:{color:'rgba(255,255,255,.04)'},ticks:{color:'#8899aa',font:{size:10}}}}};
}
function destroyChart(id){if(charts[id]){charts[id].destroy();delete charts[id];}}

function buildEquityChart(trades){
  const sorted=[...trades].sort((a,b)=>a.date.localeCompare(b.date));
  let cum=0;
  const labels=sorted.map(t=>t.date);
  const data=sorted.map(t=>{cum+=parseFloat(t.pnl)||0;return cum;});
  destroyChart('chart-equity');
  const ctx=document.getElementById('chart-equity').getContext('2d');
  const color=data[data.length-1]>=0?'#00e5a0':'#ff4d6d';
  charts['chart-equity']=new Chart(ctx,{type:'line',data:{labels,datasets:[{label:'Cumulative P&L',data,borderColor:color,backgroundColor:color+'22',fill:true,tension:0.35,pointRadius:2,pointHoverRadius:5,borderWidth:2}]},options:chartOptions()});
}

function buildSymbolChart(trades){
  const map={};
  trades.forEach(t=>{
    if(!map[t.symbol]) map[t.symbol]={wins:0,losses:0};
    (parseFloat(t.pnl)||0)>=0?map[t.symbol].wins++:map[t.symbol].losses++;
  });
  const labels=Object.keys(map);
  const wins=labels.map(s=>map[s].wins);
  const losses=labels.map(s=>map[s].losses);
  destroyChart('chart-symbol');
  const ctx=document.getElementById('chart-symbol').getContext('2d');
  charts['chart-symbol']=new Chart(ctx,{type:'bar',data:{labels,datasets:[
    {label:'Wins',data:wins,backgroundColor:'rgba(0,229,160,.7)',borderRadius:4},
    {label:'Losses',data:losses,backgroundColor:'rgba(255,77,109,.7)',borderRadius:4}
  ]},options:{...chartOptions(),plugins:{...chartOptions().plugins,legend:{display:true,labels:{color:'#8899aa',font:{size:10}}}}}});
}

function buildDailyChart(trades){
  const map={};
  trades.forEach(t=>{map[t.date]=(map[t.date]||0)+(parseFloat(t.pnl)||0);});
  const labels=Object.keys(map).sort();
  const data=labels.map(d=>map[d]);
  const colors=data.map(v=>v>=0?'rgba(0,229,160,.75)':'rgba(255,77,109,.75)');
  destroyChart('chart-daily');
  const ctx=document.getElementById('chart-daily').getContext('2d');
  charts['chart-daily']=new Chart(ctx,{type:'bar',data:{labels,datasets:[{label:'Daily P&L',data,backgroundColor:colors,borderRadius:4}]},options:chartOptions()});
}

function buildDistChart(wins,losses){
  destroyChart('chart-dist');
  const ctx=document.getElementById('chart-dist').getContext('2d');
  charts['chart-dist']=new Chart(ctx,{type:'doughnut',data:{labels:['Wins','Losses'],datasets:[{data:[wins,losses],backgroundColor:['rgba(0,229,160,.8)','rgba(255,77,109,.8)'],borderWidth:0,hoverOffset:6}]},
    options:{responsive:true,maintainAspectRatio:false,cutout:'65%',plugins:{legend:{position:'bottom',labels:{color:'#8899aa',padding:16,font:{size:11}}},tooltip:{backgroundColor:'#1a2330',borderColor:'#243444',borderWidth:1,titleColor:'#e2e8f0',bodyColor:'#8899aa'}}}
  });
}

// ── ADD TRADE ─────────────────────────────────────────────────────────────
function openAddModal(){
  document.getElementById('add-modal').classList.add('open');
  document.getElementById('f-date').value=new Date().toISOString().split('T')[0];
}
function closeAddModal(){document.getElementById('add-modal').classList.remove('open');}

function previewImage(input){
  const img=document.getElementById('preview-img');
  if(input.files&&input.files[0]){
    img.src=URL.createObjectURL(input.files[0]);
    img.style.display='block';
  }
}

async function saveTrade(){
  const btn=document.getElementById('save-btn');
  const required=['f-date','f-symbol','f-entry','f-exit','f-qty'];
  for(const id of required){
    if(!document.getElementById(id).value){toast('Please fill all required fields','error');return;}
  }
  btn.innerHTML='<span class="spinner"></span> Saving…';
  btn.disabled=true;
  const fd=new FormData();
  const fields={date:'f-date',symbol:'f-symbol',direction:'f-dir',entry_price:'f-entry',exit_price:'f-exit',quantity:'f-qty',strategy:'f-strategy',session:'f-session',emotions:'f-emotions',notes:'f-notes'};
  for(const[k,id] of Object.entries(fields)) fd.append(k,document.getElementById(id).value);
  const file=document.getElementById('f-screenshot').files[0];
  if(file) fd.append('screenshot',file);
  try{
    const r=await fetch('/api/trades',{method:'POST',body:fd});
    const d=await r.json();
    if(d.error) throw new Error(d.error);
    toast('Trade saved!','success');
    closeAddModal();
    resetForm();
    await loadTrades();
  }catch(e){toast('Error: '+e.message,'error');}
  finally{btn.innerHTML='<i class="fa-solid fa-floppy-disk"></i> Save Trade';btn.disabled=false;}
}

function resetForm(){
  ['f-date','f-symbol','f-entry','f-exit','f-qty','f-strategy','f-notes'].forEach(id=>document.getElementById(id).value='');
  document.getElementById('f-dir').value='LONG';
  document.getElementById('f-session').value='';
  document.getElementById('f-emotions').value='';
  document.getElementById('f-screenshot').value='';
  document.getElementById('preview-img').style.display='none';
}

// ── DETAIL MODAL ──────────────────────────────────────────────────────────
function openDetailModal(id){
  const t=allTrades.find(x=>x.id===id);
  if(!t) return;
  const pnl=parseFloat(t.pnl)||0;
  document.getElementById('detail-title').textContent=`// ${t.symbol} — ${t.date}`;
  document.getElementById('delete-btn').onclick=()=>deleteTrade(id);
  const fields=[
    ['Symbol',t.symbol],['Direction',t.direction],['Date',t.date],
    ['Entry',parseFloat(t.entry_price).toFixed(6)],['Exit',parseFloat(t.exit_price).toFixed(6)],
    ['Quantity',t.quantity],['P&L',(pnl>=0?'+':'')+pnl.toFixed(2)],
    ['P&L %',t.pnl_pct?(parseFloat(t.pnl_pct).toFixed(2)+'%'):'—'],
    ['Strategy',t.strategy||'—'],['Session',t.session||'—'],
    ['Emotions',t.emotions||'—'],
  ];
  document.getElementById('detail-content').innerHTML=fields.map(([l,v])=>`<div class="detail-field"><div class="detail-label">${l}</div><div class="detail-value">${v}</div></div>`).join('');
  if(t.notes){
    document.getElementById('detail-content').innerHTML+=`<div class="detail-field" style="grid-column:1/-1"><div class="detail-label">Notes</div><div class="detail-value" style="font-family:var(--font-sans);font-size:.88rem;line-height:1.6">${t.notes}</div></div>`;
  }
  const ssEl=document.getElementById('detail-screenshot');
  if(t.screenshot_url){
    ssEl.innerHTML=`<img src="${t.screenshot_url}" alt="Trade Screenshot" loading="lazy"/>`;
  } else {
    ssEl.innerHTML=`<div class="no-screenshot"><i class="fa-solid fa-image" style="font-size:1.5rem;margin-bottom:.5rem;display:block"></i>No screenshot</div>`;
  }
  document.getElementById('detail-modal').classList.add('open');
}
function closeDetailModal(){document.getElementById('detail-modal').classList.remove('open');}

// ── DELETE ────────────────────────────────────────────────────────────────
async function deleteTrade(id){
  if(!confirm('Delete this trade?')) return;
  try{
    const r=await fetch('/api/trades/'+id,{method:'DELETE'});
    const d=await r.json();
    if(d.error) throw new Error(d.error);
    toast('Trade deleted','info');
    closeDetailModal();
    await loadTrades();
  }catch(e){toast('Error: '+e.message,'error');}
}

// ── PDF ───────────────────────────────────────────────────────────────────
async function generatePDF(){
  toast('Generating PDF report…','info');
  try{
    const r=await fetch('/api/export/pdf');
    if(!r.ok) throw new Error('Server error');
    const blob=await r.blob();
    const url=URL.createObjectURL(blob);
    const a=document.createElement('a');
    a.href=url;a.download='trading_journal_'+new Date().toISOString().slice(0,10)+'.pdf';
    a.click();URL.revokeObjectURL(url);
    toast('PDF downloaded!','success');
  }catch(e){toast('PDF failed: '+e.message,'error');}
}

// ── DRAG & DROP ───────────────────────────────────────────────────────────
const dz=document.getElementById('drop-zone');
dz.addEventListener('dragover',e=>{e.preventDefault();dz.classList.add('dragover');});
dz.addEventListener('dragleave',()=>dz.classList.remove('dragover'));
dz.addEventListener('drop',e=>{
  e.preventDefault();dz.classList.remove('dragover');
  const file=e.dataTransfer.files[0];
  if(file&&file.type.startsWith('image/')){
    const inp=document.getElementById('f-screenshot');
    const dt=new DataTransfer();dt.items.add(file);inp.files=dt.files;
    previewImage(inp);
  }
});

// ── INIT ──────────────────────────────────────────────────────────────────
checkHealth();
loadTrades();
</script>
</body>
</html>"""

# ═════════════════════════════════════════════════════════════════════════════
#  HELPERS – Database connection & query execution
# ═════════════════════════════════════════════════════════════════════════════
def get_conn():
    """Get a connection from the pool."""
    if db_pool is None:
        raise RuntimeError("Database not connected. Set DATABASE_URL correctly.")
    return db_pool.getconn()

def return_conn(conn):
    db_pool.putconn(conn)

def execute_sql(sql, params=None, fetch=True):
    """Execute a SQL statement using a connection from the pool."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            if fetch:
                columns = [desc[0] for desc in cur.description] if cur.description else []
                rows = cur.fetchall()
                # Convert to list of dicts with string keys
                result = [dict(zip(columns, row)) for row in rows]
            else:
                result = None
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        return_conn(conn)

# ═════════════════════════════════════════════════════════════════════════════
#  ROUTES
# ═════════════════════════════════════════════════════════════════════════════
@app.route("/")
def index():
    return HTML

@app.route("/api/health")
def health():
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
        return_conn(conn)
        return jsonify({"ok": True, "message": "Connected to PostgreSQL"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

# ── GET all trades ────────────────────────────────────────────────────────────
@app.route("/api/trades", methods=["GET"])
def get_trades():
    try:
        trades = execute_sql("SELECT * FROM trades ORDER BY date DESC")
        return jsonify({"trades": trades})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── CREATE trade ──────────────────────────────────────────────────────────────
@app.route("/api/trades", methods=["POST"])
def create_trade():
    try:
        data = request.form.to_dict()
        screenshot_url = None

        # Handle file upload (screenshot) – save locally
        file = request.files.get("screenshot")
        if file and file.filename:
            ext = file.filename.rsplit(".", 1)[-1].lower()
            fname = f"{uuid.uuid4()}.{ext}"
            file.save(UPLOAD_FOLDER / fname)
            # URL to serve this file (relative to the server)
            screenshot_url = f"/uploads/{fname}"

        # Calculate P&L
        entry     = float(data.get("entry_price", 0))
        exit_     = float(data.get("exit_price", 0))
        qty       = float(data.get("quantity", 0))
        direction = data.get("direction", "LONG")
        pnl       = (exit_ - entry) * qty if direction == "LONG" else (entry - exit_) * qty
        pnl_pct   = ((exit_ - entry) / entry * 100) if direction == "LONG" and entry else \
                    ((entry - exit_) / entry * 100) if entry else 0

        record = (
            data["date"],
            data["symbol"].upper().strip(),
            direction,
            entry,
            exit_,
            qty,
            data.get("strategy") or None,
            data.get("session") or None,
            data.get("emotions") or None,
            data.get("notes") or None,
            round(pnl, 2),
            round(pnl_pct, 4),
            screenshot_url,
        )

        insert_sql = """
            INSERT INTO trades
                (date, symbol, direction, entry_price, exit_price, quantity,
                 strategy, session, emotions, notes, pnl, pnl_pct, screenshot_url)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
        """
        new_trade = execute_sql(insert_sql, record)
        return jsonify({"trade": new_trade[0]}), 201
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

# ── DELETE trade ──────────────────────────────────────────────────────────────
@app.route("/api/trades/<trade_id>", methods=["DELETE"])
def delete_trade(trade_id):
    try:
        execute_sql("DELETE FROM trades WHERE id = %s", (trade_id,), fetch=False)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ═════════════════════════════════════════════════════════════════════════════
#  PDF EXPORT  (unchanged logic, now using /uploads/ for screenshots)
# ═════════════════════════════════════════════════════════════════════════════
def hex_to_rl(h):
    h = h.lstrip("#")
    return rl_colors.Color(*[int(h[i:i+2], 16)/255 for i in (0, 2, 4)])

BG       = hex_to_rl("#080c10")
SURFACE  = hex_to_rl("#0e1318")
SURFACE2 = hex_to_rl("#141b22")
C_GREEN  = hex_to_rl("#00e5a0")
C_RED    = hex_to_rl("#ff4d6d")
C_BLUE   = hex_to_rl("#3b9eff")
C_TEXT   = hex_to_rl("#e2e8f0")
C_TEXT2  = hex_to_rl("#8899aa")
C_BORDER = hex_to_rl("#1e2d3d")

def make_equity_chart_img(trades):
    sorted_t = sorted(trades, key=lambda t: t["date"])
    cum = 0
    cumulative = []
    for t in sorted_t:
        cum += float(t.get("pnl") or 0)
        cumulative.append(cum)
    dates = [t["date"] for t in sorted_t]

    fig, ax = plt.subplots(figsize=(7, 2.8), facecolor="#0e1318")
    ax.set_facecolor("#0e1318")
    color = "#00e5a0" if (cumulative[-1] if cumulative else 0) >= 0 else "#ff4d6d"
    ax.plot(dates, cumulative, color=color, linewidth=2)
    ax.fill_between(range(len(cumulative)), cumulative, alpha=0.15, color=color)
    ax.set_xticks(range(0, len(dates), max(1, len(dates)//6)))
    ax.set_xticklabels([dates[i] for i in range(0, len(dates), max(1, len(dates)//6))],
                       rotation=30, ha="right", fontsize=7, color="#8899aa")
    ax.tick_params(axis="y", colors="#8899aa", labelsize=7)
    ax.spines[:].set_color("#1e2d3d")
    ax.grid(axis="y", color="#1e2d3d", linewidth=0.5)
    ax.set_title("Cumulative P&L", color="#e2e8f0", fontsize=9, pad=8)
    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor="#0e1318")
    plt.close()
    buf.seek(0)
    return buf

def make_daily_bar_img(trades):
    daily = defaultdict(float)
    for t in trades:
        daily[t["date"]] += float(t.get("pnl") or 0)
    dates = sorted(daily.keys())
    values = [daily[d] for d in dates]
    colors = ["#00e5a0" if v >= 0 else "#ff4d6d" for v in values]

    fig, ax = plt.subplots(figsize=(7, 2.8), facecolor="#0e1318")
    ax.set_facecolor("#0e1318")
    ax.bar(range(len(dates)), values, color=colors, width=0.7)
    ax.set_xticks(range(0, len(dates), max(1, len(dates)//6)))
    ax.set_xticklabels([dates[i] for i in range(0, len(dates), max(1, len(dates)//6))],
                       rotation=30, ha="right", fontsize=7, color="#8899aa")
    ax.tick_params(axis="y", colors="#8899aa", labelsize=7)
    ax.spines[:].set_color("#1e2d3d")
    ax.grid(axis="y", color="#1e2d3d", linewidth=0.5)
    ax.axhline(0, color="#243444", linewidth=1)
    ax.set_title("Daily P&L", color="#e2e8f0", fontsize=9, pad=8)
    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor="#0e1318")
    plt.close()
    buf.seek(0)
    return buf

def make_win_loss_img(trades):
    pnls   = [float(t.get("pnl") or 0) for t in trades]
    wins   = sum(1 for p in pnls if p >= 0)
    losses = sum(1 for p in pnls if p < 0)

    fig, ax = plt.subplots(figsize=(3.5, 2.8), facecolor="#0e1318")
    ax.set_facecolor("#0e1318")
    if wins + losses:
        wedges, texts, autotexts = ax.pie(
            [wins, losses], labels=["Wins", "Losses"],
            colors=["#00e5a0", "#ff4d6d"],
            autopct="%1.0f%%", startangle=90,
            wedgeprops={"linewidth": 0}, pctdistance=0.75
        )
        for at in autotexts:
            at.set_color("#080c10"); at.set_fontsize(8); at.set_fontweight("bold")
        for tx in texts:
            tx.set_color("#8899aa"); tx.set_fontsize(8)
    ax.set_title("Win / Loss", color="#e2e8f0", fontsize=9, pad=8)
    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor="#0e1318")
    plt.close()
    buf.seek(0)
    return buf

@app.route("/api/export/pdf")
def export_pdf():
    try:
        trades = execute_sql("SELECT * FROM trades ORDER BY date DESC")
        buf = io.BytesIO()
        doc = SimpleDocTemplate(
            buf, pagesize=A4,
            leftMargin=1.8*cm, rightMargin=1.8*cm,
            topMargin=2*cm, bottomMargin=2*cm,
            title="Trading Journal Report"
        )

        def ps(name, **kw):
            return ParagraphStyle(name, **kw)

        s_title  = ps("Title2",  fontName="Courier-Bold",   fontSize=20, textColor=C_GREEN,  spaceAfter=4)
        s_sub    = ps("Sub",     fontName="Helvetica",      fontSize=9,  textColor=C_TEXT2,  spaceAfter=20)
        s_h1     = ps("H1",      fontName="Courier-Bold",   fontSize=12, textColor=C_GREEN,  spaceBefore=16, spaceAfter=8)
        s_note   = ps("Note",    fontName="Helvetica",      fontSize=8,  textColor=C_TEXT2,  leading=13)
        s_small  = ps("Small",   fontName="Helvetica",      fontSize=7.5,textColor=C_TEXT2)
        s_footer = ps("Footer",  fontName="Courier",        fontSize=7,  textColor=C_TEXT2,  alignment=1)

        story = []

        story.append(Spacer(1, 1.5*cm))
        story.append(Paragraph("TRADELOG", s_title))
        story.append(Paragraph(f"Personal Trading Journal Report — Generated {datetime.now().strftime('%B %d, %Y')}", s_sub))
        story.append(HRFlowable(width="100%", thickness=1, color=C_BORDER, spaceAfter=20))

        pnls      = [float(t.get("pnl") or 0) for t in trades]
        wins      = [p for p in pnls if p >= 0]
        losses    = [p for p in pnls if p < 0]
        total     = len(trades)
        win_rate  = (len(wins)/total*100) if total else 0
        net_pnl   = sum(pnls)
        avg_win   = (sum(wins)/len(wins)) if wins else 0
        avg_loss  = (abs(sum(losses)/len(losses))) if losses else 0
        gross_win = sum(wins)
        gross_loss= abs(sum(losses))
        pf        = f"{gross_win/gross_loss:.2f}" if gross_loss else "∞"
        best      = max(pnls) if pnls else 0
        worst     = min(pnls) if pnls else 0

        story.append(Paragraph("PERFORMANCE SUMMARY", s_h1))
        stat_data = [
            ["Total Trades", str(total),                                         "Win Rate",     f"{win_rate:.1f}%"],
            ["Net P&L",      f"{'+' if net_pnl>=0 else ''}{net_pnl:.2f}",       "Profit Factor", pf],
            ["Avg Win",      f"+{avg_win:.2f}",                                  "Avg Loss",     f"-{avg_loss:.2f}"],
            ["Best Trade",   f"{'+' if best>=0 else ''}{best:.2f}",              "Worst Trade",  f"{worst:.2f}"],
        ]
        stat_table = Table(stat_data, colWidths=[4*cm, 4*cm, 4*cm, 4*cm])
        stat_table.setStyle(TableStyle([
            ("BACKGROUND",     (0,0),(-1,-1), SURFACE),
            ("ROWBACKGROUNDS", (0,0),(-1,-1), [SURFACE, SURFACE2]),
            ("FONTNAME",       (0,0),(-1,-1), "Helvetica"),
            ("FONTNAME",       (0,0),(0,-1),  "Helvetica-Bold"),
            ("FONTNAME",       (2,0),(2,-1),  "Helvetica-Bold"),
            ("FONTSIZE",       (0,0),(-1,-1), 8),
            ("TEXTCOLOR",      (0,0),(0,-1),  C_TEXT2),
            ("TEXTCOLOR",      (2,0),(2,-1),  C_TEXT2),
            ("TEXTCOLOR",      (1,0),(1,-1),  C_TEXT),
            ("TEXTCOLOR",      (3,0),(3,-1),  C_TEXT),
            ("FONTNAME",       (1,0),(1,-1),  "Courier-Bold"),
            ("FONTNAME",       (3,0),(3,-1),  "Courier-Bold"),
            ("PADDING",        (0,0),(-1,-1), 8),
            ("GRID",           (0,0),(-1,-1), 0.5, C_BORDER),
        ]))
        story.append(stat_table)
        story.append(Spacer(1, 20))

        if trades:
            story.append(Paragraph("ANALYTICS", s_h1))
            equity_buf = make_equity_chart_img(trades)
            daily_buf  = make_daily_bar_img(trades)
            wl_buf     = make_win_loss_img(trades)

            chart_row = Table(
                [[RLImage(equity_buf, width=10*cm, height=4*cm),
                  RLImage(wl_buf,    width=5.5*cm, height=4*cm)]],
                colWidths=[10.5*cm, 6*cm]
            )
            chart_row.setStyle(TableStyle([
                ("BACKGROUND",(0,0),(-1,-1),SURFACE),
                ("GRID",(0,0),(-1,-1),0.5,C_BORDER),
                ("PADDING",(0,0),(-1,-1),8),
            ]))
            story.append(chart_row)
            story.append(Spacer(1, 8))

            daily_row = Table(
                [[RLImage(daily_buf, width=16*cm, height=4*cm)]],
                colWidths=[17*cm]
            )
            daily_row.setStyle(TableStyle([
                ("BACKGROUND",(0,0),(-1,-1),SURFACE),
                ("GRID",(0,0),(-1,-1),0.5,C_BORDER),
                ("PADDING",(0,0),(-1,-1),8),
            ]))
            story.append(daily_row)
            story.append(Spacer(1, 20))

        story.append(PageBreak())
        story.append(Paragraph("TRADE LOG", s_h1))
        headers    = ["Date","Symbol","Dir","Entry","Exit","Qty","P&L","Strategy"]
        table_data = [headers]
        for t in sorted(trades, key=lambda x: x["date"], reverse=True):
            pnl = float(t.get("pnl") or 0)
            table_data.append([
                t["date"], t["symbol"], t["direction"],
                f"{float(t['entry_price']):.4f}", f"{float(t['exit_price']):.4f}",
                str(t["quantity"]),
                f"{'+' if pnl>=0 else ''}{pnl:.2f}",
                t.get("strategy") or "—"
            ])

        col_w = [2.4*cm, 2.8*cm, 1.4*cm, 2.6*cm, 2.6*cm, 1.8*cm, 2.4*cm, 3*cm]
        trade_table = Table(table_data, colWidths=col_w, repeatRows=1)
        ts = TableStyle([
            ("BACKGROUND",     (0,0),(-1,0),  SURFACE2),
            ("FONTNAME",       (0,0),(-1,0),  "Courier-Bold"),
            ("FONTSIZE",       (0,0),(-1,0),  7),
            ("TEXTCOLOR",      (0,0),(-1,0),  C_TEXT2),
            ("FONTNAME",       (0,1),(-1,-1), "Courier"),
            ("FONTSIZE",       (0,1),(-1,-1), 8),
            ("TEXTCOLOR",      (0,1),(-1,-1), C_TEXT),
            ("ROWBACKGROUNDS", (0,1),(-1,-1), [SURFACE, SURFACE2]),
            ("PADDING",        (0,0),(-1,-1), 6),
            ("GRID",           (0,0),(-1,-1), 0.4, C_BORDER),
            ("ALIGN",          (3,0),(-1,-1), "RIGHT"),
        ])
        for i, t in enumerate(trades, start=1):
            pnl = float(t.get("pnl") or 0)
            c   = C_GREEN if pnl >= 0 else C_RED
            ts.add("TEXTCOLOR", (6,i),(6,i), c)
            ts.add("FONTNAME",  (6,i),(6,i), "Courier-Bold")
        trade_table.setStyle(ts)
        story.append(trade_table)
        story.append(Spacer(1, 20))

        trades_with_notes = [t for t in trades if t.get("notes") or t.get("screenshot_url")]
        if trades_with_notes:
            story.append(PageBreak())
            story.append(Paragraph("TRADE DETAILS", s_h1))
            for t in sorted(trades_with_notes, key=lambda x: x["date"], reverse=True):
                pnl = float(t.get("pnl") or 0)
                header_text = (f"{t['date']}  |  {t['symbol']}  |  {t['direction']}  |  "
                               f"P&L: {'+' if pnl>=0 else ''}{pnl:.2f}")
                story.append(Paragraph(header_text, ps(
                    "TH", fontName="Courier-Bold", fontSize=8.5,
                    textColor=C_GREEN if pnl>=0 else C_RED,
                    spaceBefore=12, spaceAfter=4
                )))
                if t.get("notes"):
                    story.append(Paragraph(t["notes"], s_note))
                if t.get("screenshot_url"):
                    # For local uploads, the screenshot_url is like "/uploads/..."
                    img_path = pathlib.Path("uploads") / t["screenshot_url"].split("/")[-1]
                    if img_path.exists():
                        try:
                            pil_img = PILImage.open(img_path)
                            w, h = pil_img.size
                            max_w = 15*cm
                            ratio = h / w
                            rl_img = RLImage(str(img_path), width=max_w, height=max_w*ratio)
                            story.append(Spacer(1, 6))
                            story.append(rl_img)
                        except Exception:
                            story.append(Paragraph("(Screenshot unavailable)", s_small))
                    else:
                        story.append(Paragraph("(Screenshot file missing)", s_small))
                story.append(HRFlowable(width="100%", thickness=0.5, color=C_BORDER, spaceAfter=8))

        story.append(Spacer(1, 1*cm))
        story.append(HRFlowable(width="100%", thickness=0.5, color=C_BORDER, spaceAfter=8))
        story.append(Paragraph(
            f"TradeLog — Personal Trading Journal  |  "
            f"{datetime.now().strftime('%Y-%m-%d %H:%M')}  |  Total Trades: {total}",
            s_footer
        ))

        doc.build(story)
        buf.seek(0)
        return send_file(
            buf, as_attachment=True,
            download_name=f"trading_journal_{datetime.now().strftime('%Y%m%d')}.pdf",
            mimetype="application/pdf"
        )
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

# ═════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)