"""Tests for sanitize_final_title() — ensures no HTML or body-text leaks into published titles."""
import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.html_utils import sanitize_final_title


class TestSanitizeFinalTitle(unittest.TestCase):

    def test_clean_title_returns_ok(self):
        title = "Comédias dos Anos 80 Que Seriam Sucessos Atuais"
        result, status = sanitize_final_title(title)
        self.assertEqual(result, title)
        self.assertEqual(status, "OK")

    def test_strips_p_tag_returns_sanitized(self):
        dirty = "Melhores Filmes de 2026 <p>Os produtores afirmam"
        result, status = sanitize_final_title(dirty, fallback_title="Melhores Filmes de 2026")
        self.assertNotIn("<", result)
        self.assertNotIn(">", result)
        # May use sanitized or fallback, but must be clean and valid
        self.assertIn(status, ("SANITIZED", "FALLBACK_USED"))

    def test_body_text_leakage_uses_fallback(self):
        """Title with 'em <p>Os produzir' pattern must trigger fallback."""
        dirty = "Comédias dos Anos 80 Que Seriam Sucessos Atuais em <p>Os produzir"
        fallback = "Comédias dos Anos 80 Que Seriam Sucessos Atuais"
        result, status = sanitize_final_title(dirty, fallback_title=fallback)
        self.assertEqual(result, fallback)
        self.assertEqual(status, "FALLBACK_USED")

    def test_empty_title_uses_fallback(self):
        fallback = "Título de Fallback Válido Para Teste"
        result, status = sanitize_final_title("", fallback_title=fallback)
        self.assertEqual(result, fallback)
        self.assertEqual(status, "FALLBACK_USED")

    def test_both_invalid_returns_invalid(self):
        result, status = sanitize_final_title("<p>", fallback_title="<div>")
        self.assertEqual(result, "")
        self.assertEqual(status, "INVALID")

    def test_html_entities_decoded(self):
        title = "Her&ccedil;a do Passado &amp; Futuro dos Filmes Brasileiros"
        result, status = sanitize_final_title(title)
        # HTML entities must be decoded: &amp; → &, &ccedil; → ç
        self.assertNotIn("&amp;", result)
        self.assertNotIn("&ccedil;", result)
        self.assertIn(status, ("OK", "SANITIZED"))

    def test_multiple_spaces_normalized(self):
        title = "Filmes   de   Ação   em   2026"
        result, status = sanitize_final_title(title)
        self.assertNotIn("  ", result)
        self.assertIn(status, ("OK", "SANITIZED"))

    def test_title_exceeding_90_chars_truncated(self):
        long_title = "A" * 91
        result, status = sanitize_final_title(long_title)
        self.assertLessEqual(len(result), 90)

    def test_title_with_angle_brackets_invalid_goes_to_fallback(self):
        dirty = "Título <b>com HTML</b>"
        fallback = "Título Válido com Mais de Vinte e Cinco Chars"
        result, status = sanitize_final_title(dirty, fallback_title=fallback)
        # After stripping tags the text 'Título com HTML' is valid—SANITIZED expected
        self.assertNotIn("<", result)
        self.assertNotIn(">", result)
        self.assertIn(status, ("SANITIZED", "OK", "FALLBACK_USED"))

    def test_newlines_removed(self):
        title = "Séries de Drama\nQue Dominaram 2026"
        result, status = sanitize_final_title(title)
        self.assertNotIn("\n", result)
        self.assertIn(status, ("OK", "SANITIZED"))


class TestPipelineImportsSanitizer(unittest.TestCase):
    """Smoke test: pipeline.py must import and use sanitize_final_title."""

    def test_sanitize_imported_in_pipeline(self):
        import importlib
        import ast, pathlib
        source = pathlib.Path(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        ) / "app" / "pipeline.py"
        tree = ast.parse(source.read_text(encoding="utf-8-sig"))
        imported_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    imported_names.add(alias.name)
        self.assertIn(
            "sanitize_final_title",
            imported_names,
            "pipeline.py must import sanitize_final_title from html_utils",
        )

    def test_sanitize_called_in_pipeline(self):
        import ast, pathlib
        source = pathlib.Path(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        ) / "app" / "pipeline.py"
        source_text = source.read_text(encoding="utf-8-sig")
        self.assertIn(
            "sanitize_final_title(",
            source_text,
            "pipeline.py must call sanitize_final_title() after optimize_title",
        )

    def test_sanitize_before_wordpress_payload(self):
        """sanitize_final_title must appear before 'post_payload' construction."""
        import pathlib
        source = pathlib.Path(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        ) / "app" / "pipeline.py"
        text = source.read_text(encoding="utf-8-sig")
        idx_sanitize = text.find("sanitize_final_title(")
        idx_payload = text.find("post_payload = {")
        self.assertGreater(idx_payload, idx_sanitize,
            "sanitize_final_title must run before post_payload is assembled")


if __name__ == "__main__":
    unittest.main(verbosity=2)
