from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "tray_client" / "build" / "installer.iss"


class TrayInstallerScriptTests(unittest.TestCase):
    def test_installer_uses_per_user_localappdata_programs_dir(self) -> None:
        text = INSTALLER.read_text(encoding="utf-8")

        self.assertIn(r"DefaultDirName={localappdata}\Programs\IRMS-Notice", text)
        self.assertNotIn(r"DefaultDirName={autopf}\IRMS-Notice", text)

    def test_installer_uses_lowest_privileges_for_per_user_install(self) -> None:
        text = INSTALLER.read_text(encoding="utf-8")

        self.assertIn("PrivilegesRequired=lowest", text)
        self.assertNotIn("PrivilegesRequired=admin", text)


if __name__ == "__main__":
    unittest.main()
