"""
Unit tests for the tags module
"""

import unittest
from app.tags import TagExtractor


class TestTagExtractor(unittest.TestCase):
    """Test cases for the TagExtractor class"""

    def setUp(self):
        """Set up test fixtures"""
        self.extractor = TagExtractor()

    def test_extract_tags_comprehensive(self):
        """Test comprehensive tag extraction from title and content."""
        title = "Marvel's Spider-Man: No Way Home Gets New Trailer"
        content = """
        Marvel Studios has released a new trailer for "Spider-Man: No Way Home"
        starring Tom Holland. The movie will be available on Disney+ and Netflix
        after its theatrical release. Director Jon Watts confirmed that this
        Spider-Man movie will feature multiple villains from previous films.
        """

        tags = self.extractor.extract_tags(content, title)

        # Expected tags are proper nouns, excluding common stop words
        expected_tags = [
            'Spider', 'Man', 'No Way Home', 'Director Jon Watts', 'Tom Holland', 'Netflix', 'Disney'
        ]

        # Check that the most important tags are present
        for expected_tag in expected_tags:
            self.assertIn(expected_tag, tags, f"Expected tag '{expected_tag}' not found in {tags}")

        # Test that the number of tags is limited
        self.assertLessEqual(len(tags), 15)

        # Test that short or invalid tags are not included
        self.assertNotIn("a", tags)
        self.assertNotIn("the", tags)

    def test_empty_input(self):
        """Test behavior with empty title and content."""
        tags = self.extractor.extract_tags("", "")
        self.assertEqual(tags, [])

    def test_no_specific_tags(self):
        """Test with generic content that shouldn't produce many tags."""
        title = "Generic News Article"
        content = "This is a generic article about something unrelated to entertainment. It discusses regular things."

        tags = self.extractor.extract_tags(content, title)

        # Should return a list, likely with generic proper nouns if any
        self.assertIsInstance(tags, list)
        # Check that it doesn't contain common stop words
        self.assertNotIn("Generic", tags, "Should filter out generic terms that start with capital letters but are common.")


if __name__ == '__main__':
    unittest.main()