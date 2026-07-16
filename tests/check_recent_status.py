#!/usr/bin/env python3
"""Check recent article status and posts"""

import sqlite3
import os
from datetime import datetime, timedelta

db_path = 'data/app.db'
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

print("=" * 90)
print("ARTIGOS RECENTES (últimas 15)")
print("=" * 90)

cursor.execute("""
    SELECT id, status, published_at, url
    FROM seen_articles
    ORDER BY published_at DESC
    LIMIT 15
""")

for row in cursor.fetchall():
    art_id, status, published_at, url = row
    # Show only domain
    domain = url.split('/')[2] if '://' in url else url[:40]
    print(f"{art_id:6} | {status:12} | {published_at[:19]:19} | {domain:40}")

print("\n" + "=" * 90)
print("POSTS PUBLICADOS (últimos 10)")
print("=" * 90)

cursor.execute("""
    SELECT p.id, p.wp_post_id, p.created_at, sa.status
    FROM posts p
    LEFT JOIN seen_articles sa ON p.seen_article_id = sa.id
    ORDER BY p.created_at DESC
    LIMIT 10
""")

rows = cursor.fetchall()
for row in rows:
    post_id, wp_id, created_at, art_status = row
    print(f"Post {post_id:6} → WP ID {wp_id:6} | {created_at[:19]:19} | Article status: {art_status}")

print("\n" + "=" * 90)
print("DISTRIBUIÇÃO DE STATUS")
print("=" * 90)

cursor.execute("""
    SELECT status, COUNT(*) as count
    FROM seen_articles
    GROUP BY status
    ORDER BY count DESC
""")

for status, count in cursor.fetchall():
    pct = (count / 12510) * 100
    print(f"{status:15} : {count:6} ({pct:5.1f}%)")

print("\n" + "=" * 90)
print("ÚLTIMAS 24 HORAS")
print("=" * 90)

cursor.execute("""
    SELECT status, COUNT(*) as count
    FROM seen_articles
    WHERE published_at > datetime('now', '-1 day')
    GROUP BY status
    ORDER BY count DESC
""")

rows = cursor.fetchall()
if rows:
    for status, count in rows:
        print(f"{status:15} : {count:6} articles")
else:
    print("No articles in the last 24 hours")

print("\n" + "=" * 90)
print("POSTS CRIADOS HOJE")
print("=" * 90)

cursor.execute("""
    SELECT COUNT(*) FROM posts
    WHERE created_at > datetime('now', '-1 day')
""")

count = cursor.fetchone()[0]
print(f"Total: {count} posts")

conn.close()
