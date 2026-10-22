"""Descoberta do servidor instalado sem executar scrcpy ou acessar dispositivos."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from core.config import _find_installed_server, _find_server


class ScrcpyPathsTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.binary = self.root / "bin" / "scrcpy"
        self.binary.parent.mkdir()
        self.binary.touch()

    def server(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        return path

    def test_finds_ubuntu_server_in_share(self):
        server = self.server(self.root / "share" / "scrcpy" / "scrcpy-server")
        self.assertEqual(_find_installed_server(self.binary), server)

    def test_finds_server_jar_in_lib(self):
        server = self.server(self.root / "lib" / "scrcpy" / "scrcpy-server.jar")
        self.assertEqual(_find_installed_server(self.binary), server)

    def test_prefers_portable_server_next_to_binary(self):
        server = self.server(self.binary.parent / "scrcpy-server-v4.1.jar")
        self.server(self.root / "share" / "scrcpy" / "scrcpy-server")
        self.assertEqual(_find_installed_server(self.binary), server)

    def test_uses_resolved_binary_installation(self):
        actual_binary = self.root / "actual" / "bin" / "scrcpy"
        server = self.server(self.root / "actual" / "share" / "scrcpy" / "scrcpy-server")
        with patch.object(Path, "resolve", return_value=actual_binary):
            self.assertEqual(_find_installed_server(self.binary), server)

    def test_does_not_search_unrelated_binary_subdirectories(self):
        self.server(self.binary.parent / "other" / "scrcpy-server")
        self.assertIsNone(_find_installed_server(self.binary))

    def test_no_executable_has_no_installed_server(self):
        self.assertIsNone(_find_installed_server(""))

    def test_bundled_server_still_allows_nested_directories(self):
        server = self.server(self.root / "tools" / "scrcpy" / "portable" / "scrcpy-server.jar")
        self.assertEqual(_find_server(self.root / "tools" / "scrcpy"), server)


if __name__ == "__main__":
    unittest.main()
