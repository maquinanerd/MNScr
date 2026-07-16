#!/usr/bin/env python3
"""Verificar chaves carregadas"""
from app.config import AI_API_KEYS

print("="*80)
print("CHAVES CARREGADAS")
print("="*80)
print(f"Total: {len(AI_API_KEYS)} chaves")
for idx, key in enumerate(AI_API_KEYS, 1):
    print(f"  [{idx}] {key[:15]}...{key[-4:]}")
