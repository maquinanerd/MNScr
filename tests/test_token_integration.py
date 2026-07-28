#!/usr/bin/env python3
"""
Test script to validate token logging integration with real production code.

Testa se os tokens estão sendo capturados corretamente do fluxo real:
1. ai_client_gemini.py retorna tokens_info
2. ai_processor.py registra tokens com log_tokens()
3. logs/tokens/ contém arquivos JSONL com dados reais
"""

import json
import logging
import sys
from datetime import datetime
from pathlib import Path

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def check_token_files():
    """Verify token log files were created."""
    logs_dir = Path("logs/tokens")
    if not logs_dir.exists():
        logger.error(f"❌ Token logs directory not found: {logs_dir}")
        return False

    # Check for today's JSONL file
    today = datetime.now().strftime("%Y-%m-%d")
    jsonl_file = logs_dir / f"tokens_{today}.jsonl"

    if not jsonl_file.exists():
        logger.warning(f"⚠️  No JSONL file yet for today: {jsonl_file}")
        logger.info("   (This is normal if no posts have been processed yet)")
        return True

    logger.info(f"✅ Found JSONL log file: {jsonl_file}")

    # Check JSONL content
    try:
        line_count = 0
        with open(jsonl_file, 'r', encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    data = json.loads(line)
                    line_count += 1
                    logger.info(f"   Entry {line_count}: {data.get('timestamp', 'N/A')} - "
                              f"Entrada: {data.get('prompt_tokens', 0)} | "
                              f"Saída: {data.get('completion_tokens', 0)}")

        if line_count > 0:
            logger.info(f"✅ JSONL file has {line_count} entries")
        else:
            logger.warning("⚠️  JSONL file is empty (yet)")
    except Exception as e:
        logger.error(f"❌ Error reading JSONL file: {e}")
        return False

    # Check stats JSON
    stats_file = logs_dir / "stats.json"
    if stats_file.exists():
        try:
            with open(stats_file, 'r', encoding='utf-8') as f:
                stats = json.load(f)
            logger.info("✅ Found stats file")
            logger.info(f"   Total Entrada: {stats.get('total_prompt_tokens', 0)} tokens")
            logger.info(f"   Total Saída: {stats.get('total_completion_tokens', 0)} tokens")
            logger.info(f"   Total Requisições: {stats.get('total_requests', 0)}")
            logger.info(f"   Requisições com sucesso: {stats.get('successful_requests', 0)}")
        except Exception as e:
            logger.error(f"❌ Error reading stats file: {e}")
            return False
    else:
        logger.warning("⚠️  Stats file not found (yet)")

    return True

def check_integration_code():
    """Verify that token logging is integrated into the production code."""
    logger.info("\n📋 Checking code integration...")

    # Check ai_client_gemini.py
    ai_client_file = Path("app/ai_client_gemini.py")
    if not ai_client_file.exists():
        logger.error(f"❌ File not found: {ai_client_file}")
        return False

    with open(ai_client_file, 'r', encoding='utf-8') as f:
        ai_client_content = f.read()

    if "tokens_info" in ai_client_content and "usage_metadata" in ai_client_content:
        logger.info("✅ ai_client_gemini.py has token capture code")
    else:
        logger.error("❌ ai_client_gemini.py missing token capture code")
        return False

    # Check ai_processor.py
    ai_processor_file = Path("app/ai_processor.py")
    if not ai_processor_file.exists():
        logger.error(f"❌ File not found: {ai_processor_file}")
        return False

    with open(ai_processor_file, 'r', encoding='utf-8') as f:
        ai_processor_content = f.read()

    checks = [
        ("log_tokens import", "from .token_tracker import log_tokens"),
        ("batch rewrite token logging", "log_tokens(" in ai_processor_content and "batch_rewrite" in ai_processor_content),
        ("content rewrite token logging", "single_rewrite" in ai_processor_content),
    ]

    all_good = True
    for check_name, check_code in checks:
        if isinstance(check_code, bool):
            if check_code:
                logger.info(f"✅ {check_name}")
            else:
                logger.error(f"❌ {check_name}")
                all_good = False
        else:
            if check_code in ai_processor_content:
                logger.info(f"✅ {check_name}")
            else:
                logger.error(f"❌ {check_name}")
                all_good = False

    return all_good

def main():
    logger.info("=" * 60)
    logger.info("VALIDAÇÃO DE INTEGRAÇÃO DE TOKEN LOGGING")
    logger.info("=" * 60)

    logger.info("\n1️⃣  Verificando código integrado...")
    code_ok = check_integration_code()

    logger.info("\n2️⃣  Verificando arquivos de log...")
    check_token_files()

    logger.info("\n" + "=" * 60)
    if code_ok:
        logger.info("✅ CÓDIGO INTEGRADO COM SUCESSO!")
        logger.info("\n📝 O que acontece agora:")
        logger.info("   1. Quando posts forem processados, tokens serão capturados")
        logger.info("   2. Arquivos JSONL criarão logs diários em logs/tokens/")
        logger.info("   3. stats.json consolidará dados semanais/mensais")
        logger.info("   4. Você pode visualizar com: python token_logs_viewer.py")
        logger.info("\n🔍 Próximos passos:")
        logger.info("   1. Processe alguns posts de teste")
        logger.info("   2. Verifique os logs: logs/tokens/tokens_YYYY-MM-DD.jsonl")
        logger.info("   3. Execute: python token_logs_viewer.py")
    else:
        logger.error("❌ ERRO NA INTEGRAÇÃO - VERIFIQUE LOGS ACIMA")
        sys.exit(1)

if __name__ == "__main__":
    main()
