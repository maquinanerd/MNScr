"""Pytest configuration for MNScr.

The suite must never reach the network, a real CMS, a real model provider or a
production database. Everything is mocked or written to ``tmp_path``.
"""

collect_ignore: list[str] = []
