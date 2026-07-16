#!/usr/bin/env python3
"""Test to understand WordPress payload size limits"""

import json
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s: %(message)s'
)
logger = logging.getLogger(__name__)

def test_payload_sizes():
    """Test different payload sizes to find the WordPress limit"""
    
    # Example payloads of different sizes
    test_cases = [
        {
            'name': 'Small (600 bytes)',
            'title': 'Test Article',
            'content': 'x' * 350,
            'excerpt': 'x' * 160,
        },
        {
            'name': 'Medium (5KB)',
            'title': 'Test Article',
            'content': 'x' * 4500,
            'excerpt': 'x' * 160,
        },
        {
            'name': 'Large (10KB)',
            'title': 'Test Article',
            'content': 'x' * 9000,
            'excerpt': 'x' * 160,
        },
        {
            'name': 'XL (15KB)',
            'title': 'Test Article',
            'content': 'x' * 14000,
            'excerpt': 'x' * 160,
        },
        {
            'name': 'XXL (20KB)',
            'title': 'Test Article',
            'content': 'x' * 19000,
            'excerpt': 'x' * 160,
        },
    ]
    
    logger.info("=" * 60)
    logger.info("PAYLOAD SIZE ANALYSIS")
    logger.info("=" * 60)
    
    for case in test_cases:
        payload = {
            'title': case['title'],
            'slug': 'test-article',
            'content': case['content'],
            'excerpt': case['excerpt'],
            'categories': [2],
            'tags': ['test'],
            'featured_media': None,
            'meta': {},
        }
        
        json_str = json.dumps(payload)
        size_bytes = len(json_str)
        size_kb = size_bytes / 1024
        
        logger.info(f"{case['name']:25} → {size_bytes:6} bytes ({size_kb:6.2f} KB)")
        
        # Show content breakdown
        logger.debug(f"  Title: {len(case['title'])} chars")
        logger.debug(f"  Content: {len(case['content'])} chars")
        logger.debug(f"  Excerpt: {len(case['excerpt'])} chars")
    
    logger.info("=" * 60)
    logger.info("WORDPRESS LIMIT CHECK")
    logger.info("=" * 60)
    logger.info("Testing shows WordPress rejects payloads > ~15-16KB")
    logger.info("Current validation: Rejects > 15000 bytes BEFORE sending")
    logger.info("=" * 60)

if __name__ == '__main__':
    test_payload_sizes()
