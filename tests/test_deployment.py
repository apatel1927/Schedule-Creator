"""Static checks for the GitHub registry and Unraid-native update path."""

import subprocess
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
IMAGE = "ghcr.io/apatel1927/schedule-creator:latest"


class UnraidDeploymentTests(unittest.TestCase):
    def test_native_template_matches_persistent_volume_and_registry_image(self):
        root = ET.parse(ROOT / "templates/my-shiftledger.xml").getroot()
        self.assertEqual(root.tag, "Container")
        self.assertEqual(root.findtext("Name"), "shiftledger")
        self.assertEqual(root.findtext("Repository"), IMAGE)
        config = {item.attrib["Target"]: item.attrib for item in root.findall("Config")}
        self.assertEqual(config["/data"]["Default"], "/mnt/user/appdata/shiftledger/data")
        self.assertEqual(config["8080"]["Default"], "8787")
        self.assertIn("ADMIN_PASSWORD", config)
        self.assertEqual(config["ADMIN_PASSWORD"]["Mask"], "true")

    def test_migration_checks_registry_before_stopping_legacy(self):
        script = (ROOT / "MIGRATE-UNRAID-NATIVE.sh").read_text()
        self.assertIn("docker pull \"$image\"", script)
        self.assertLess(script.index("docker pull \"$image\""), script.index("docker stop shiftledger"))
        self.assertLess(script.index("docker exec shiftledger python /app/backup.py"),
                        script.index("docker stop shiftledger"))
        self.assertIn("docker rename shiftledger \"$legacy_name\"", script)

    def test_native_scripts_have_valid_posix_shell_syntax(self):
        for script in ("MIGRATE-UNRAID-NATIVE.sh", "ROLLBACK-UNRAID-NATIVE.sh"):
            with self.subTest(script=script):
                subprocess.run(["sh", "-n", str(ROOT / script)], check=True)

    def test_workflow_publishes_after_tests(self):
        workflow = (ROOT / ".github/workflows/publish-container.yml").read_text()
        self.assertIn("needs: test", workflow)
        self.assertIn("packages: write", workflow)
        self.assertIn(IMAGE, workflow)
        self.assertIn("python -m unittest discover -s tests -q", workflow)


if __name__ == "__main__":
    unittest.main()
