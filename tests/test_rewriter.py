import unittest
from app.rewriter import ContentRewriter

class TestContentRewriter(unittest.TestCase):

    def setUp(self):
        self.rewriter = ContentRewriter()

    def test_process_content(self):
        """Test the complete content processing workflow."""
        raw_ai_text = """
        Novo Título: This is the Title
        Novo Resumo: This is the excerpt.
        Novo Conteúdo: <p>This content talks about <b>Spider-Man</b> and <i>Marvel</i> movies.</p>
        <script>alert('xss');</script>
        <div>This should be unwrapped.</div>
        """
        tags = ["spider-man", "marvel"]
        domain = "https://example.com"

        result = self.rewriter.process_content(raw_ai_text, tags, domain)

        # Test title parsing
        self.assertEqual(result['title'], "This is the Title")

        # Test excerpt parsing
        self.assertEqual(result['excerpt'], "This is the excerpt.")

        # Test content sanitization and internal links
        content = result['content']
        expected_content = '<p>This content talks about <b><a href="https://example.com/tag/spider-man">Spider-Man</a></b> and <i><a href="https://example.com/tag/marvel">Marvel</a></i> movies.</p>\n\nThis should be unwrapped.'
        self.assertEqual(content.strip(), expected_content.strip())
        self.assertNotIn('<script>', content)
        self.assertNotIn('<div>', content)

if __name__ == '__main__':
    unittest.main()