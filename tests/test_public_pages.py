"""Every public page renders: catches syntax slips in the copy modules and
templates that the backend tests never import."""
from tests import _util  # noqa: F401  (must come first)

import unittest

import main

PATHS = ["/", "/sitemap.xml", "/robots.txt", "/about", "/product", "/solutions", "/solutions/company-secretary", "/solutions/practising-cs",
         "/solutions/listed-company", "/solutions/risk-officer", "/pricing", "/security", "/faq", "/privacy",
         "/tools", "/blog", "/signin", "/signup"]


class PublicPages(unittest.TestCase):
    def setUp(self):
        main.app.config["TESTING"] = True
        self.c = main.app.test_client()

    def test_every_public_page_renders(self):
        for p in PATHS:
            with self.subTest(path=p):
                r = self.c.get(p)
                self.assertEqual(r.status_code, 200, p)
                t = r.get_data(as_text=True)
                for bad in ("Traceback", "Undefined", "{{", "{%"):
                    self.assertNotIn(bad, t, f"{p} contains {bad!r}")

    def test_unknown_solution_is_a_404_page(self):
        self.assertEqual(self.c.get("/solutions/nope").status_code, 404)

    def test_rule_count_on_product_page_matches_the_rulebook(self):
        import rulebook as rb
        t = self.c.get("/product").get_data(as_text=True)
        self.assertIn(f"{len(rb.RULES) + 1} rules", t)


if __name__ == "__main__":
    unittest.main()
