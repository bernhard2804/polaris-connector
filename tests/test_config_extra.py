import unittest

from conftest_helpers import TempHome


class TestConfigKeepsForeignKeys(unittest.TestCase):
    def test_roots_add_keeps_memory_mirror(self):
        with TempHome() as home:
            from polaris import config

            root = home.make_project_root("A")
            config.CONFIG_FILE.write_text(
                config.CONFIG_FILE.read_text() + "memory_mirror: ~/Projekte/Polaris/Maschinengedaechtnis\n")
            other = home.tmp / "Weitere"
            other.mkdir()
            config.add_project_root(str(other))
            text = config.CONFIG_FILE.read_text()
            self.assertIn("memory_mirror: ~/Projekte/Polaris/Maschinengedaechtnis", text)
            self.assertIn(str(other), text)
            self.assertIn(str(root), text)


if __name__ == "__main__":
    unittest.main()
