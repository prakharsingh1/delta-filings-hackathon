"""Desktop backend helpers: install secrets, backup folders, log redaction.

The request gate and the frozen build are covered by desktop/smoke_test.py.
"""
import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import _util  # noqa: F401  (temp database, outbound network blocked)

sys.path.insert(0, os.path.join(_util.ROOT, "desktop"))
import server  # noqa: E402


class InstallSecrets(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix="delta-desktop-"))
        p = mock.patch.dict(os.environ, {"DELTA_KEYCHAIN": "0"})
        p.start()
        self.addCleanup(p.stop)

    def test_created_once_then_reused(self):
        first = server.install_secret(self.folder, "sdd")
        self.assertEqual(server.install_secret(self.folder, "sdd"), first)
        self.assertEqual(oct((self.folder / "sdd.key").stat().st_mode & 0o777), "0o600")

    def test_lost_sdd_key_stops_instead_of_resealing(self):
        server.install_secret(self.folder, "sdd")
        (self.folder / "sdd.key").unlink()
        with self.assertRaises(server.MissingSecret):
            server.install_secret(self.folder, "sdd")

    def test_encrypted_database_without_its_key_stops(self):
        (self.folder / server.DB_NAME).write_bytes(os.urandom(4096))  # not a plaintext header
        with self.assertRaises(server.MissingSecret):
            server.install_secret(self.folder, "database")

    def test_session_secret_is_simply_remade(self):
        server.install_secret(self.folder, "session")
        (self.folder / "session.key").unlink()
        self.assertTrue(server.install_secret(self.folder, "session"))

    def test_keychain_failure_never_creates_a_new_database_key(self):
        server.install_secret(self.folder, "database")
        (self.folder / "database.key").unlink()
        broken = mock.Mock(get_password=mock.Mock(side_effect=RuntimeError("locked")))
        with mock.patch.dict(os.environ, {"DELTA_KEYCHAIN": "1"}), mock.patch.dict(sys.modules, {"keyring": broken}):
            with self.assertRaises(server.MissingSecret):
                server.install_secret(self.folder, "database")


class Backups(unittest.TestCase):
    def test_shared_backup_folder_gets_a_subfolder_per_computer(self):
        folder = Path(tempfile.mkdtemp())
        self.assertEqual(server.backup_dir(folder), folder / "backups")
        with mock.patch.dict(os.environ, {"DELTA_BACKUP_DIR": "/srv/share"}), \
                mock.patch("socket.gethostname", return_value="CS Desk #2"):
            self.assertEqual(server.backup_dir(folder), Path("/srv/share/CS_Desk_2"))


class Logs(unittest.TestCase):
    def test_launch_token_is_not_written_to_the_request_log(self):
        record = logging.LogRecord("werkzeug", logging.INFO, "", 0, '"%s" %s %s',
                                   ("GET /__desktop/enter?t=SECRET-TOKEN&next=/ HTTP/1.1", 200, "-"), None)
        server._RedactToken().filter(record)
        self.assertNotIn("SECRET-TOKEN", record.getMessage())
        self.assertIn("next=/", record.getMessage())


if __name__ == "__main__":
    unittest.main()
