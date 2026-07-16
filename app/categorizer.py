import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class Categorizer:
    """Maps a feed source ID to a WordPress category ID."""

    def map_category(self, source_id: str, wp_categories: Dict[str, int]) -> Optional[int]:
        """
        Determines the WordPress category ID based on the source_id suffix.

        Args:
            source_id: The identifier for the feed source (e.g., 'screenrant_movies').
            wp_categories: A dictionary mapping category names to their WordPress IDs.

        Returns:
            The corresponding WordPress category ID, or None if no match is found.
        """
        if source_id in ('lance', 'globo_futebol'):
            return wp_categories.get('futebol')
        if source_id == 'globo_internacional':
            return wp_categories.get('futebol-internacional')

        logger.warning(f"Could not map source_id '{source_id}' to a known category.")
        return None