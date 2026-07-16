#!/usr/bin/env python3
"""Check database status of recent articles"""

import sqlite3
import os
from datetime import datetime

db_path = 'data/app.db'

if not os.path.exists(db_path):
    print(f"Database not found at {db_path}")
    exit(1)

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

print("=" * 80)
print("DATABASE STATUS")
print("=" * 80)

# Check recent articles
print("\nRecent articles (last 10):")
print("-" * 80)

cursor.execute("""
    SELECT db_id, title, status, created_at
    FROM seen_articles
    ORDER BY created_at DESC
    LIMIT 10
""")

for row in cursor.fetchall():
    db_id, title, status, created_at = row
    print(f"{db_id:4} | {status:10} | {title[:60]:60} | {created_at}")

# Check posts created
print("\n\nRecently created posts:")
print("-" * 80)

cursor.execute("""
    SELECT article_id, wp_post_id, published_at
    FROM posts
    ORDER BY published_at DESC
    LIMIT 5
""")

rows = cursor.fetchall()
if rows:
    for row in rows:
        article_id, wp_post_id, published_at = row
        print(f"Article {article_id} → Post {wp_post_id} at {published_at}")
else:
    print("No posts found")

# Check failures
print("\n\nRecent failures (last 5):")
print("-" * 80)

cursor.execute("""
    SELECT article_id, error_reason, error_time
    FROM failures
    ORDER BY error_time DESC
    LIMIT 5
""")

rows = cursor.fetchall()
if rows:
    for row in rows:
        article_id, reason, error_time = row
        print(f"Article {article_id}: {reason[:70]} at {error_time}")
else:
    print("No failures logged")

# Count by status
print("\n\nStatus distribution:")
print("-" * 80)

cursor.execute("""
    SELECT status, COUNT(*) as count
    FROM seen_articles
    GROUP BY status
    ORDER BY count DESC
""")

for status, count in cursor.fetchall():
    print(f"{status:15} : {count:6} articles")

conn.close()

print("\n" + "=" * 80)
