#!/usr/bin/env python3
"""Check database schema and status"""

import sqlite3
import os

db_path = 'data/app.db'

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

print("=" * 80)
print("DATABASE SCHEMA")
print("=" * 80)

# Get all tables
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [row[0] for row in cursor.fetchall()]

print(f"\nTables found: {tables}\n")

for table in tables:
    print(f"\nTABLE: {table}")
    print("-" * 80)
    
    cursor.execute(f"PRAGMA table_info({table})")
    cols = cursor.fetchall()
    for col in cols:
        cid, name, type_, notnull, default, pk = col
        print(f"  {name:30} {type_:15}")
    
    # Show sample data
    cursor.execute(f"SELECT COUNT(*) FROM {table}")
    count = cursor.fetchone()[0]
    print(f"  Total rows: {count}")

conn.close()
