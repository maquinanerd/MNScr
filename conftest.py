"""Pytest collection policy for the repository's unit-test suite.

Ignored files are operational diagnostics or manual integration scripts. They
execute work at import time (including real Gemini/WordPress requests or local
state writes), so collecting them makes ``pytest`` non-deterministic. They
remain directly executable from the command line.
"""

collect_ignore = [
    "scratch/test_fixes.py",
    "test_3phase_pipeline.py",
    "test_token_system.py",
    "tests/test_api_key.py",
    "tests/test_api_keys.py",
    "tests/test_api_logging.py",
    "tests/test_complete_post.py",
    "tests/test_cta_removal.py",
    "tests/test_gutenberg.py",
    "tests/test_gutenberg_wp.py",
    "tests/test_image_blocks.py",
    "tests/test_image_gutenberg_wp.py",
    "tests/test_key_rotation.py",
    "tests/test_logging.py",
    "tests/test_nuclear_cta_removal.py",
    "tests/test_pipeline_cta_integration.py",
    "tests/test_quick.py",
    "tests/test_quota_exceeded.py",
    "tests/test_real_article_500.py",
    "tests/test_real_articles.py",
    "tests/test_systematic_wp_errors.py",
    "tests/test_wordpress_500.py",
    "tests/test_wp_500.py",
    "tests/test_wp_auth.py",
    "tests/test_wp_direct.py",
    "tests/test_wp_payload_fields.py",
    "tests/test_wp_read.py",
    "tests/test_wp_simple.py",
]
