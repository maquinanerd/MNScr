#!/usr/bin/env python3
"""Quick test of pipeline image handling"""

import logging
import sys
import os

logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s: %(message)s'
)
logger = logging.getLogger(__name__)

logger.info("=" * 60)
logger.info("DIAGNOSTIC TEST")
logger.info("=" * 60)

# Check if main pipeline is running
logger.info("Checking if app files exist...")

files_to_check = [
    'app/config.py',
    'app/wordpress.py',
    'app/pipeline.py',
    '.env'
]

for f in files_to_check:
    path = os.path.join(os.getcwd(), f)
    if os.path.exists(path):
        logger.info(f"✅ {f}")
    else:
        logger.warning(f"❌ {f} NOT FOUND")

# Check featured image validation in pipeline
logger.info("=" * 60)
logger.info("Checking pipeline code...")
logger.info("=" * 60)

with open('app/pipeline.py', 'r', encoding='utf-8') as f:
    content = f.read()
    if 'featured_media_id' in content:
        logger.info("✅ featured_media_id variable found")
    if 'FEATURED OK' in content:
        logger.info("✅ FEATURED OK log message found")
    if 'if media and media.get("id")' in content:
        logger.info("✅ Featured image validation found")
        # Find the context
        lines = content.split('\n')
        for i, line in enumerate(lines):
            if 'if media and media.get("id")' in line:
                logger.info(f"   Location: Line {i+1}")
                # Show context
                for j in range(max(0, i-2), min(len(lines), i+5)):
                    logger.info(f"   {j+1}: {lines[j]}")
                break
    else:
        logger.error("❌ Featured image validation NOT found (OLD CODE)")

logger.info("=" * 60)

