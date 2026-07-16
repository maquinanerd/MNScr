import unittest
from app.categorizer import Categorizer

class TestCategorizer(unittest.TestCase):

    def setUp(self):
        self.categorizer = Categorizer()
        self.wp_categories = {
            'futebol': 8,
            'futebol-internacional': 9,
            'Notícias': 1,
        }

    def test_map_category(self):
        test_cases = [
            ('lance', self.wp_categories.get('futebol')),
            ('globo_futebol', self.wp_categories.get('futebol')),
            ('globo_internacional', self.wp_categories.get('futebol-internacional')),
            ('unknown_feed', None),
        ]

        for source_id, expected_category_id in test_cases:
            with self.subTest(source_id=source_id):
                category_id = self.categorizer.map_category(source_id, self.wp_categories)
                self.assertEqual(category_id, expected_category_id)

if __name__ == '__main__':
    unittest.main()