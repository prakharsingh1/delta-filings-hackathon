"""roles.py: stored role, default for users onboarded before roles, and
the per-role top nav."""
from tests import _util  # noqa: F401  (must come first)

import unittest

import roles


class Roles(unittest.TestCase):
    def setUp(self):
        self.user = _util.uid()

    def test_default_for_unknown_or_missing_user(self):
        self.assertEqual(roles.get_role(self.user), roles.DEFAULT_ROLE)
        self.assertEqual(roles.get_role(""), roles.DEFAULT_ROLE)
        self.assertEqual(roles.get_role(None), roles.DEFAULT_ROLE)
        self.assertEqual(roles.DEFAULT_ROLE, "compliance")
        self.assertEqual(roles.home_for(self.user), "/dashboard")

    def test_set_and_replace(self):
        self.assertTrue(roles.set_role(self.user, "cro"))
        self.assertEqual(roles.get_role(self.user), "cro")
        self.assertEqual(roles.home_for(self.user), "/risk")
        self.assertTrue(roles.set_role(self.user, "pcs"))
        self.assertEqual(roles.get_role(self.user), "pcs")
        conn = _util.connect()
        n = conn.execute("SELECT COUNT(*) FROM user_roles WHERE user_id = ?", (self.user,)).fetchone()[0]
        conn.close()
        self.assertEqual(n, 1)

    def test_invalid_role_or_user_is_refused(self):
        roles.set_role(self.user, "cs")
        self.assertFalse(roles.set_role(self.user, "ceo"))
        self.assertFalse(roles.set_role(self.user, ""))
        self.assertFalse(roles.set_role("", "cro"))
        self.assertFalse(roles.set_role(None, "cro"))
        self.assertEqual(roles.get_role(self.user), "cs")

    def test_stale_stored_role_falls_back(self):
        conn = _util.connect()
        conn.execute("INSERT INTO user_roles (user_id, role, updated_at) VALUES (?, 'auditor', '2026-01-01T00:00:00')",
                     (self.user,))
        conn.commit()
        conn.close()
        self.assertEqual(roles.get_role(self.user), roles.DEFAULT_ROLE)

    def test_roles_are_per_user(self):
        other = _util.uid("other")
        roles.set_role(self.user, "cro")
        self.assertEqual(roles.get_role(other), roles.DEFAULT_ROLE)


class Nav(unittest.TestCase):
    def test_nav_for(self):
        self.assertEqual(roles.nav_for("cro")[0], "risk")
        self.assertEqual(roles.nav_for("cs")[0], "workspace")
        self.assertEqual(roles.nav_for("pcs")[0], "workspace")
        self.assertEqual(roles.nav_for("compliance")[0], "dashboard")
        self.assertEqual(roles.nav_for("nonsense"), roles.NAV[roles.DEFAULT_ROLE])
        self.assertEqual(roles.nav_for(None), roles.NAV[roles.DEFAULT_ROLE])

    def test_every_role_has_a_nav_of_known_modules(self):
        self.assertEqual(set(roles.NAV), set(roles.ROLES))
        for role, keys in roles.NAV.items():
            with self.subTest(role=role):
                self.assertEqual(len(keys), len(set(keys)))
                for k in keys:
                    self.assertIn(k, roles.MODULES)

    def test_homes_and_groups(self):
        hrefs = {m["href"] for m in roles.MODULES.values()}
        for role, r in roles.ROLES.items():
            with self.subTest(role=role):
                self.assertIn(r["home"], hrefs)
                self.assertTrue(r["label"] and r["blurb"])
        for key, m in roles.MODULES.items():
            with self.subTest(module=key):
                self.assertIn(m["group"], roles.MODULE_GROUPS)
                self.assertTrue(m["href"] is None or m["href"].startswith("/"))


if __name__ == "__main__":
    unittest.main()
