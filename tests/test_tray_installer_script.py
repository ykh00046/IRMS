from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "tray_client" / "build" / "installer.iss"
VERSION_PY = ROOT / "tray_client" / "src" / "version.py"


def _installer_version() -> str:
    text = INSTALLER.read_text(encoding="utf-8")
    match = re.search(r'^\s*#define\s+MyAppVersion\s+"([^"]+)"', text, re.MULTILINE)
    assert match, "installer.iss 에서 MyAppVersion 정의를 찾지 못했습니다."
    return match.group(1)


def _source_version() -> str:
    text = VERSION_PY.read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert match, "version.py 에서 __version__ 정의를 찾지 못했습니다."
    return match.group(1)


class TrayInstallerScriptTests(unittest.TestCase):
    def test_installer_uses_per_user_localappdata_programs_dir(self) -> None:
        text = INSTALLER.read_text(encoding="utf-8")

        self.assertIn(r"DefaultDirName={localappdata}\Programs\IRMS-Notice", text)
        self.assertNotIn(r"DefaultDirName={autopf}\IRMS-Notice", text)

    def test_installer_uses_lowest_privileges_for_per_user_install(self) -> None:
        text = INSTALLER.read_text(encoding="utf-8")

        self.assertIn("PrivilegesRequired=lowest", text)
        self.assertNotIn("PrivilegesRequired=admin", text)

    def test_installer_version_matches_source_version(self) -> None:
        """설치 파일 버전과 src/version.py 를 한쪽만 올리는 사고 방지."""
        self.assertEqual(_installer_version(), _source_version())

    def test_installer_header_comment_shows_current_output_name(self) -> None:
        text = INSTALLER.read_text(encoding="utf-8")

        self.assertIn(f"IRMS-Notice-Setup-{_source_version()}.exe", text)
        self.assertNotIn("IRMS-Notice-Setup-2.0.0.exe", text)


if __name__ == "__main__":
    unittest.main()
