#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Dashboard operacional - MNScr
Exibe tokens, feeds, posts publicados, status e métricas de SEO
"""

import json
import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

from flask import Flask, render_template, render_template_string, jsonify
import os

# =====================================================
# PATHS
# =====================================================
PROJECT_ROOT = Path(__file__).parent

# =====================================================
# LOGGING
# =====================================================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# =====================================================
# FLASK
# =====================================================
app = Flask(__name__, template_folder=str(PROJECT_ROOT / 'templates'))
app.config['JSON_SORT_KEYS'] = False


@app.before_request
def _require_token():
    """Block all routes unless a valid Bearer token is supplied."""
    from flask import request

    dashboard_token = os.environ.get('DASHBOARD_TOKEN', '').strip()
    if not dashboard_token:
        logger.warning("DASHBOARD_TOKEN não configurada - acesso bloqueado")
        return jsonify({"error": "forbidden"}), 403

    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer '):
        return jsonify({"error": "unauthorized"}), 401

    provided_token = auth_header[len('Bearer '):].strip()
    if provided_token != dashboard_token:
        return jsonify({"error": "unauthorized"}), 401
LOGS_DIR = PROJECT_ROOT / 'logs' / 'tokens'
TOKEN_STATS_FILE = LOGS_DIR / 'token_stats.json'
DB_PATH = PROJECT_ROOT / 'data' / 'app.db'

# Carrega feeds do config
try:
    from app.config import RSS_FEEDS, PIPELINE_ORDER
except Exception as e:
    logger.warning(f"Erro ao carregar feeds: {e}")
    RSS_FEEDS = {}
    PIPELINE_ORDER = []

# =====================================================
# CARREGAMENTO DE DADOS
# =====================================================

def load_stats():
    """Carrega estatísticas de tokens"""
    try:
        if TOKEN_STATS_FILE.exists():
            with open(TOKEN_STATS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"Erro ao carregar stats: {e}")
    return {"gemini": {}, "publishing": {}}

def load_recent_tokens(limit=15):
    """Carrega tokens recentes"""
    recent = []
    try:
        if LOGS_DIR.exists():
            for jsonl_file in sorted(LOGS_DIR.glob("tokens_*.jsonl"), reverse=True):
                with open(jsonl_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        try:
                            entry = json.loads(line)
                            recent.append(entry)
                            if len(recent) >= limit:
                                break
                        except json.JSONDecodeError:
                            continue
                if len(recent) >= limit:
                    break
    except Exception as e:
        logger.error(f"Erro ao carregar tokens: {e}")
    recent.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
    return recent[:limit]

def load_daily_stats():
    """Carrega stats por dia"""
    daily_stats = {}
    try:
        if LOGS_DIR.exists():
            for jsonl_file in sorted(LOGS_DIR.glob("tokens_*.jsonl")):
                date_str = jsonl_file.stem.replace("tokens_", "")
                daily_stats[date_str] = {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "requests": 0,
                }
                with open(jsonl_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        try:
                            entry = json.loads(line)
                            daily_stats[date_str]["prompt_tokens"] += entry.get("prompt_tokens", 0)
                            daily_stats[date_str]["completion_tokens"] += entry.get("completion_tokens", 0)
                            daily_stats[date_str]["total_tokens"] += entry.get("total_tokens", 0)
                            daily_stats[date_str]["requests"] += 1
                        except json.JSONDecodeError:
                            pass
    except Exception as e:
        logger.error(f"Erro ao carregar stats diários: {e}")
    return daily_stats

def load_feeds_data():
    """Carrega dados dos feeds"""
    feeds_data = []
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        
        for feed_id in PIPELINE_ORDER:
            config = RSS_FEEDS.get(feed_id, {})
            
            cursor.execute('SELECT COUNT(*) FROM seen_articles WHERE source_id = ?', (feed_id,))
            total_articles = cursor.fetchone()[0] or 0
            
            cursor.execute('SELECT COUNT(*) FROM seen_articles WHERE source_id = ? AND inserted_at > datetime("now", "-24 hours")', (feed_id,))
            recent_articles = cursor.fetchone()[0] or 0
            
            cursor.execute('SELECT COUNT(*) FROM posts WHERE seen_article_id IN (SELECT id FROM seen_articles WHERE source_id = ?)', (feed_id,))
            published = cursor.fetchone()[0] or 0
            
            cursor.execute('SELECT consecutive_failures FROM feed_status WHERE source_id = ?', (feed_id,))
            row = cursor.fetchone()
            failures = row[0] if row else 0
            
            feeds_data.append({
                'id': feed_id,
                'name': config.get('source_name', feed_id.replace('_', ' ').title()),
                'category': config.get('category', 'Unknown'),
                'url': config.get('urls', ['N/A'])[0],
                'total_articles': total_articles,
                'recent_articles': recent_articles,
                'published': published,
                'failures': failures,
                'health': '✅ Saudável' if failures == 0 else '⚠️ Alerta' if failures < 3 else '❌ Erro'
            })
        conn.close()
    except Exception as e:
        logger.error(f"Erro ao carregar feeds: {e}")
        for feed_id in PIPELINE_ORDER:
            config = RSS_FEEDS.get(feed_id, {})
            feeds_data.append({
                'id': feed_id,
                'name': config.get('source_name', feed_id.replace('_', ' ').title()),
                'category': config.get('category', 'Unknown'),
                'url': config.get('urls', ['N/A'])[0],
                'total_articles': 0,
                'recent_articles': 0,
                'published': 0,
                'failures': 0,
                'health': '⚙️ Desconhecido'
            })
    return feeds_data

def load_category_stats():
    """Carrega stats por categoria"""
    stats = defaultdict(lambda: {'articles': 0, 'published': 0, 'seo_avg': 0})
    
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        
        for feed_id in PIPELINE_ORDER:
            config = RSS_FEEDS.get(feed_id, {})
            category = config.get('category', 'Unknown')
            
            cursor.execute('SELECT COUNT(*) FROM seen_articles WHERE source_id = ?', (feed_id,))
            articles = cursor.fetchone()[0] or 0
            
            cursor.execute('SELECT COUNT(*) FROM posts WHERE seen_article_id IN (SELECT id FROM seen_articles WHERE source_id = ?)', (feed_id,))
            published = cursor.fetchone()[0] or 0
            
            stats[category]['articles'] += articles
            stats[category]['published'] += published
        
        conn.close()
    except Exception as e:
        logger.error(f"Erro ao carregar stats por categoria: {e}")
    
    return dict(stats)

def prepare_chart_data(daily_stats):
    """Prepara dados para gráficos"""
    dates = sorted(daily_stats.keys())[-30:]
    prompts = [daily_stats[d]["prompt_tokens"] for d in dates]
    completions = [daily_stats[d]["completion_tokens"] for d in dates]
    totals = [daily_stats[d]["total_tokens"] for d in dates]
    
    return {
        'dates': json.dumps(dates),
        'prompts': json.dumps(prompts),
        'completions': json.dumps(completions),
        'totals': json.dumps(totals)
    }

def prepare_feeds_chart(feeds_data):
    """Prepara dados para gráfico de feeds"""
    names = [f['name'] for f in feeds_data]
    published = [f['published'] for f in feeds_data]
    articles = [f['total_articles'] for f in feeds_data]
    
    return {
        'names': json.dumps(names),
        'published': json.dumps(published),
        'articles': json.dumps(articles)
    }

def prepare_category_chart(category_stats):
    """Prepara dados para gráfico de categorias"""
    categories = list(category_stats.keys())
    published = [category_stats[c]['published'] for c in categories]
    articles = [category_stats[c]['articles'] for c in categories]
    
    return {
        'categories': json.dumps(categories),
        'published': json.dumps(published),
        'articles': json.dumps(articles)
    }

def calculate_totals(stats):
    """Calcula totais"""
    totals = {'prompt': 0, 'completion': 0, 'requests': 0}
    for api, models in stats.items():
        if isinstance(models, dict):
            for model, data in models.items():
                if isinstance(data, dict):
                    totals['prompt'] += data.get('total_prompt_tokens', 0)
                    totals['completion'] += data.get('total_completion_tokens', 0)
                    totals['requests'] += data.get('total_requests', 0)
    return totals

# =====================================================
# HTML TEMPLATE
# =====================================================

def get_dashboard_html():
    """Retorna HTML do dashboard - Design Bootstrap 5.3.5 profissional"""
    return '''<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Dashboard - MNScr</title>
    <style>
        :root {
            --bs-purple: #7367f0;
            --bs-success: #28c76f;
            --bs-info: #00bad1;
            --bs-warning: #ff9f43;
            --bs-danger: #ff4c51;
            --bs-primary: #7367f0;
            --bs-secondary: #808390;
            --bs-light: #dfdfe3;
            --bs-dark: #2f3349;
            --bs-white: #fff;
            --bs-body-bg: #f8f7fa;
            --bs-body-color: #6d6b77;
            --bs-heading-color: #444050;
            --bs-border-color: #e6e6e8;
        }
        
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: "Public Sans", -apple-system, BlinkMacSystemFont, "Segoe UI", Oxygen, Ubuntu, sans-serif;
            background-color: var(--bs-body-bg);
            color: var(--bs-body-color);
            line-height: 1.375;
            font-size: 0.9375rem;
        }
        
        .header {
            background: linear-gradient(135deg, var(--bs-primary) 0%, #6657d9 100%);
            color: white;
            padding: 3rem 1.5rem;
            margin-bottom: 2rem;
            border-radius: 0.375rem;
            box-shadow: 0 0.1875rem 0.75rem rgba(47, 43, 61, 0.14);
        }
        
        .header h1 {
            font-size: 2.5rem;
            font-weight: 500;
            margin-bottom: 0.5rem;
        }
        
        .header p {
            font-size: 1rem;
            opacity: 0.9;
        }
        
        .container {
            max-width: 1400px;
            margin: 0 auto;
            padding: 0 1rem;
        }
        
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 1.5rem;
            margin-bottom: 2rem;
        }
        
        .stat-card {
            background: var(--bs-white);
            padding: 1.5rem;
            border-radius: 0.375rem;
            border: 1px solid var(--bs-border-color);
            box-shadow: 0 0.1875rem 0.75rem rgba(47, 43, 61, 0.08);
            transition: all 0.3s ease;
        }
        
        .stat-card:hover {
            box-shadow: 0 0.25rem 1.125rem rgba(47, 43, 61, 0.16);
            transform: translateY(-2px);
        }
        
        .stat-label {
            font-size: 0.8125rem;
            color: #808390;
            font-weight: 500;
            margin-bottom: 0.75rem;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        
        .stat-value {
            font-size: 1.75rem;
            font-weight: 600;
            color: var(--bs-primary);
            line-height: 1.2;
        }
        
        .stat-subtext {
            font-size: 0.75rem;
            color: var(--bs-body-color);
            margin-top: 0.5rem;
        }
        
        .tables-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(500px, 1fr));
            gap: 1.5rem;
        }
        
        .table-container {
            background: var(--bs-white);
            border-radius: 0.375rem;
            border: 1px solid var(--bs-border-color);
            box-shadow: 0 0.1875rem 0.75rem rgba(47, 43, 61, 0.08);
            overflow: hidden;
        }
        
        .table-header {
            background: #f3f2f3;
            padding: 1rem 1.5rem;
            border-bottom: 1px solid var(--bs-border-color);
            font-weight: 600;
            color: var(--bs-heading-color);
            font-size: 0.9375rem;
        }
        
        table {
            width: 100%;
            border-collapse: collapse;
            font-size: 0.875rem;
        }
        
        th {
            background: #f3f2f3;
            padding: 0.782rem 1.25rem;
            text-align: left;
            font-weight: 600;
            color: var(--bs-heading-color);
            border-bottom: 1px solid var(--bs-border-color);
            font-size: 0.8125rem;
        }
        
        td {
            padding: 0.782rem 1.25rem;
            border-bottom: 1px solid var(--bs-border-color);
            color: var(--bs-body-color);
        }
        
        tr:last-child td { border-bottom: none; }
        tr:hover { background-color: #f3f2f3; }
        
        .badge {
            display: inline-block;
            padding: 0.35em 0.65em;
            font-size: 0.75em;
            font-weight: 500;
            line-height: 1;
            border-radius: 0.25rem;
            white-space: nowrap;
        }
        
        .badge-success {
            background-color: #d4f4e2;
            color: #10502c;
        }
        
        .badge-warning {
            background-color: #ffecd9;
            color: #66401b;
        }
        
        .badge-info {
            background-color: #d6f4f8;
            color: #004a54;
        }
        
        .badge-danger {
            background-color: #ffe2e3;
            color: #661e20;
        }
        
        .footer {
            text-align: center;
            padding: 2rem 1rem;
            color: var(--bs-body-color);
            border-top: 1px solid var(--bs-border-color);
            margin-top: 2rem;
        }
        
        @media (max-width: 768px) {
            .header h1 { font-size: 1.8rem; }
            .stats-grid { grid-template-columns: repeat(2, 1fr); }
            .tables-grid { grid-template-columns: 1fr; }
            th, td { padding: 0.5rem 0.75rem; font-size: 0.75rem; }
        }
    </style>
</head>
<body>
    <div class="header">
        <div class="container">
            <h1>📊 MNScr</h1>
            <p>Real-time Token Tracking & Content Pipeline Metrics</p>
        </div>
    </div>

    <div class="container">
        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-label">Prompt Tokens</div>
                <div class="stat-value">{{ "{:,}".format(total_prompt) }}</div>
                <div class="stat-subtext">Total input tokens</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Completion Tokens</div>
                <div class="stat-value">{{ "{:,}".format(total_completion) }}</div>
                <div class="stat-subtext">Total output tokens</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">API Requests</div>
                <div class="stat-value">{{ "{:,}".format(total_requests) }}</div>
                <div class="stat-subtext">Total API calls</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">RSS Feeds</div>
                <div class="stat-value">{{ feeds_count }}</div>
                <div class="stat-subtext">Active sources</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Posts Published</div>
                <div class="stat-value">{{ total_published }}</div>
                <div class="stat-subtext">Published content</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Articles</div>
                <div class="stat-value">{{ total_articles }}</div>
                <div class="stat-subtext">Total articles</div>
            </div>
        </div>

        <div class="tables-grid">
            <div class="table-container">
                <div class="table-header">⚡ Recent AI Operations</div>
                <table>
                    <thead>
                        <tr>
                            <th>Timestamp</th>
                            <th>Model</th>
                            <th style="text-align: right;">Tokens</th>
                            <th>Status</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for token in recent_tokens[:5] %}
                        <tr>
                            <td>{{ token.get('timestamp', 'N/A')[:16] }}</td>
                            <td><span class="badge badge-info">{{ token.get('model', 'API')[:6] }}</span></td>
                            <td style="text-align: right;">{{ "{:,}".format(token.get('total_tokens', 0)) }}</td>
                            <td><span class="badge badge-success">OK</span></td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>

            <div class="table-container">
                <div class="table-header">🔗 RSS Feed Status</div>
                <table>
                    <thead>
                        <tr>
                            <th>Feed Name</th>
                            <th>Category</th>
                            <th style="text-align: right;">Articles</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for feed in feeds %}
                        <tr>
                            <td><strong>{{ feed.name }}</strong></td>
                            <td><span class="badge badge-warning">{{ feed.category }}</span></td>
                            <td style="text-align: right;">{{ feed.total_articles }}</td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>

            <div class="table-container">
                <div class="table-header">📅 Daily Token Summary</div>
                <table>
                    <thead>
                        <tr>
                            <th>Date</th>
                            <th style="text-align: right;">Requests</th>
                            <th style="text-align: right;">Tokens</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for date, stats in daily_stats.items() %}
                        <tr>
                            <td><strong>{{ date }}</strong></td>
                            <td style="text-align: right;">{{ stats.requests }}</td>
                            <td style="text-align: right;"><strong>{{ "{:,}".format(stats.total_tokens) }}</strong></td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        </div>

        <div class="footer">
            <p>Last updated: {{ now }}</p>
        </div>
    </div>
</body>
</html>'''

# =====================================================
# ROTAS
# =====================================================

@app.route('/')
def index():
    """Dashboard principal"""
    # Load all data
    stats = load_stats()
    totals = calculate_totals(stats)
    daily_stats = load_daily_stats()
    feeds = load_feeds_data()
    categories = load_category_stats()
    recent_tokens = load_recent_tokens(20)
    
    # Calculate totals
    total_published = sum(f['published'] for f in feeds)
    total_articles = sum(f['total_articles'] for f in feeds)
    
    # Prepare context for template
    template_context = {
        'now': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'stats': {
            'seen_articles': total_articles,
            'published_posts': total_published,
            'failures': 0,
            'recent_posts': [],
            'api_usage': {'Gemini Flash': totals['requests']}
        },
        'system_status': 'Running',
        'logs': [],
        'total_prompt': totals['prompt'],
        'total_completion': totals['completion'],
        'total_requests': totals['requests'],
        'feeds_count': len(feeds),
        'feeds': feeds,
        'categories': categories,
        'recent_tokens': recent_tokens,
        'daily_stats': daily_stats,
    }
    
    return render_template('dashboard.html', **template_context)

@app.route('/api/stats')
def api_stats():
    """API de estatísticas"""
    stats = load_stats()
    totals = calculate_totals(stats)
    return jsonify({
        'status': 'success',
        'totals': totals,
        'timestamp': datetime.now().isoformat()
    })

@app.route('/metrics')
def metrics():
    """Pipeline health metrics from the SQLite database."""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        # backlog counts
        backlog_statuses = ('NEW', 'QUEUED', 'DEFERRED', 'FAILED_PERMANENT')
        cur.execute(
            "SELECT status, COUNT(*) AS cnt FROM seen_articles WHERE status IN ({}) GROUP BY status".format(
                ','.join('?' * len(backlog_statuses))
            ),
            backlog_statuses,
        )
        backlog = {s: 0 for s in backlog_statuses}
        for row in cur.fetchall():
            backlog[row['status']] = row['cnt']

        # published last 24 h (table: posts, column: created_at)
        cur.execute(
            "SELECT COUNT(*) AS cnt FROM posts WHERE created_at > datetime('now', '-24 hours')"
        )
        published_last_24h = cur.fetchone()['cnt']

        # failures last 24 h
        cur.execute(
            """
            SELECT COUNT(*) AS cnt FROM seen_articles
            WHERE status IN ('FAILED', 'FAILED_PERMANENT')
              AND fail_reason IS NOT NULL
              AND inserted_at > datetime('now', '-24 hours')
            """
        )
        failures_last_24h = cur.fetchone()['cnt']

        # oldest pending (minutes)
        cur.execute(
            """
            SELECT MIN(inserted_at) AS oldest FROM seen_articles
            WHERE status IN ('NEW', 'QUEUED', 'DEFERRED')
            """
        )
        oldest_row = cur.fetchone()['oldest']
        if oldest_row:
            try:
                oldest_dt = datetime.fromisoformat(oldest_row.replace('Z', '+00:00'))
                oldest_pending_minutes = int((datetime.utcnow() - oldest_dt.replace(tzinfo=None)).total_seconds() / 60)
            except Exception:
                oldest_pending_minutes = None
        else:
            oldest_pending_minutes = None

        # total articles
        cur.execute("SELECT COUNT(*) AS cnt FROM seen_articles")
        total_articles = cur.fetchone()['cnt']

        conn.close()
    except sqlite3.Error as e:
        logger.error(f"[/metrics] Database error: {e}")
        return jsonify({"error": "database unavailable"}), 503

    return jsonify({
        "backlog": backlog,
        "published_last_24h": published_last_24h,
        "failures_last_24h": failures_last_24h,
        "oldest_pending_minutes": oldest_pending_minutes,
        "total_articles": total_articles,
        "timestamp": datetime.utcnow().isoformat(),
    })


if __name__ == '__main__':
    logger.info("🚀 Dashboard iniciando em http://localhost:5555")
    app.run(host='127.0.0.1', port=5555, debug=False)
