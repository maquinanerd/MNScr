#!/usr/bin/env python3
"""
Tag extraction from article content.
"""

import logging
import re
from typing import List, Set

logger = logging.getLogger(__name__)

# A simple list of common words to ignore.
# This can be expanded or replaced with a more sophisticated library.
STOP_WORDS = {
    'a', 'about', 'above', 'after', 'again', 'against', 'all', 'am', 'an', 'and', 'any', 'are', "aren't", 'as', 'at',
    'be', 'because', 'been', 'before', 'being', 'below', 'between', 'both', 'but', 'by', 'can', "can't", 'cannot',
    'com', 'could', "couldn't", 'did', "didn't", 'do', 'does', "doesn't", 'doing', "don't", 'down', 'during', 'each',
    'few', 'for', 'from', 'further', 'had', "hadn't", 'has', "hasn't", 'have', "haven't", 'having', 'he', "he'd",
    "he'll", "he's", 'her', 'here', "here's", 'hers', 'herself', 'him', 'himself', 'his', 'how', "how's", 'i', "i'd",
    "i'll", "i'm", "i've", 'if', 'in', 'into', 'is', "isn't", 'it', "it's", 'its', 'itself', "let's", 'me', 'more',
    'most', "mustn't", 'my', 'myself', 'no', 'nor', 'not', 'of', 'off', 'on', 'once', 'only', 'or', 'other', 'ought',
    'our', 'ours', 'ourselves', 'out', 'over', 'own', 'same', "shan't", 'she', "she'd", "she'll", "she's", 'should',
    "shouldn't", 'so', 'some', 'such', 'than', 'that', "that's", 'the', 'their', 'theirs', 'them', 'themselves',
    'then', 'there', "there's", 'these', 'they', "they'd", "they'll", "they're", "they've", 'this', 'those', 'through',
    'to', 'too', 'under', 'until', 'up', 'very', 'was', "wasn't", 'we', "we'd", "we'll", "we're", "we've", 'were',
    "weren't", 'what', "what's", 'when', "when's", 'where', "where's", 'which', 'while', 'who', "who's", 'whom',
    'why', "why's", 'with', "won't", 'would', "wouldn't", 'you', "you'd", "you'll", "you're", "you've", 'your',
    'yours', 'yourself', 'yourselves', 'filme', 'série', 'jogo', 'personagem', 'ator', 'atriz', 'diretor', 'roteirista'
}


class TagExtractor:
    """Extracts relevant tags from text content."""

    def extract_tags(self, content: str, title: str, max_tags: int = 15) -> List[str]:
        """
        Extracts potential tags from the title and content.
        """
        if not content and not title:
            return []

        text_to_process = ' '.join([title] * 3) + ' ' + content
        proper_nouns = re.findall(r'\b[A-Z][a-zA-Z\'-+]+(?:\s+[A-Z][a-zA-Z\'-+]+)*\b', text_to_process)

        cleaned_tags: Set[str] = set()
        for tag in proper_nouns:
            tag = tag.strip().replace("'s", "")
            if self._is_valid_tag(tag):
                cleaned_tags.add(tag)

        sorted_tags = sorted(list(cleaned_tags), key=lambda t: (text_to_process.count(t), len(t)), reverse=True)
        
        final_tags = sorted_tags[:max_tags]
        logger.info(f"Extracted {len(final_tags)} tags: {', '.join(final_tags[:5])}...")
        return final_tags

    def _is_valid_tag(self, tag: str) -> bool:
        """Validates if a string is a good candidate for a tag."""
        if tag.lower() in STOP_WORDS or len(tag) < 3 or len(tag) > 50:
            return False
        if 'http' in tag or 'www' in tag or '.com' in tag or '/' in tag or '\\' in tag:
            return False
        if tag.isdigit() or not any(c.isalpha() for c in tag):
            return False
        return True