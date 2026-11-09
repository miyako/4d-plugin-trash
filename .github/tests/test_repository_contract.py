"""Trash-specific preservation evidence, including after a genuine source bump."""

from pathlib import Path
import re
import stat
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[2]
BASE = "9c10eb97f3c38b8abfa4dcaf58912e9cc8561eb2"
PROJECT = "Trash.xcodeproj/project.pbxproj"
LOCALIZED = "English.lproj/InfoPlist.strings"
SOURCE = (PROJECT, "Info.plist", LOCALIZED)


def original(path):
    return subprocess.check_output(["git", "-C", str(ROOT), "show", f"{BASE}:{path}"])


class RepositoryContractTests(unittest.TestCase):
    def test_original_coordinated_header_and_trigger_inventory(self):
        bump = original(".github/workflows/bump-version.yml").decode()
        header, body = bump.split("name: Bump version", 1)
        self.assertIn("MARKETING_VERSION/CURRENT_PROJECT_VERSION AND Info.plist", header)
        self.assertIn("CFBundleShortVersionString/CFBundleVersion", header)
        self.assertIn('KEY="MARKETING_VERSION"', body)
        self.assertIn('KEY="CURRENT_PROJECT_VERSION"', body)
        release = original(".github/workflows/release.yml").decode()
        self.assertIn("  push:\n    tags:\n      - 'v*.*.*'", release)
        self.assertNotIn("pull_request:", release)
        self.assertNotIn("branches:", release)

    def test_source_has_only_authorized_version_token_changes(self):
        project = (ROOT / PROJECT).read_bytes()
        versions = re.findall(rb"MARKETING_VERSION = ([0-9]+\.[0-9]+\.[0-9]+);", project)
        self.assertEqual(len(versions), 6)
        self.assertEqual(len(set(versions)), 1)
        version = versions[0]
        self.assertEqual(
            project,
            original(PROJECT).replace(b"MARKETING_VERSION = 1.0.2;",
                                      b"MARKETING_VERSION = " + version + b";")
            .replace(b"CURRENT_PROJECT_VERSION = 1.0.1;",
                     b"CURRENT_PROJECT_VERSION = " + version + b";"),
        )
        self.assertEqual((ROOT / "Info.plist").read_bytes(), original("Info.plist"))
        localized = original(LOCALIZED)
        self.assertNotIn(b"\n", localized)
        self.assertIn(b"\r", localized)
        self.assertEqual(
            (ROOT / LOCALIZED).read_bytes(),
            localized.replace(b'CFBundleShortVersionString = "1.0.1";',
                              b'CFBundleShortVersionString = "' + version + b'";')
            .replace(b"Trash version 1.0.1, Copyright 2020 miyako.",
                     b"Trash version " + version + b", Copyright 2020 miyako."),
        )
        for path in SOURCE:
            with self.subTest(path=path):
                self.assertEqual(stat.S_IMODE((ROOT / path).stat().st_mode), 0o755)
        self.assertNotIn(b"<key>CFBundleVersion</key>", (ROOT / "Info.plist").read_bytes())
        self.assertIn(b"<string>$(MARKETING_VERSION)</string>",
                      (ROOT / "Info.plist").read_bytes())

    def test_all_unrelated_original_files_preserved(self):
        paths = subprocess.check_output(
            ["git", "-C", str(ROOT), "ls-tree", "-r", "--name-only", BASE], text=True,
        ).splitlines()
        allowed = {*SOURCE, "README.md", ".github/workflows/bump-version.yml",
                   ".github/workflows/release.yml"}
        for name in paths:
            if name in allowed:
                continue
            with self.subTest(path=name):
                self.assertEqual((ROOT / name).read_bytes(), original(name))

    def test_original_native_commands_and_secret_names(self):
        before = original(".github/workflows/release.yml").decode()
        after = (ROOT / ".github/workflows/release.yml").read_text()

        def block(workflow, name):
            return workflow.split(f"      - name: {name}\n", 1)[1].split(
                "\n      - name:", 1
            )[0].rstrip()

        for name in ("build x64", "import Developer ID certificate",
                     "store notarytool credentials", "codesign"):
            with self.subTest(step=name):
                self.assertEqual(block(before, name), block(after, name))
        secrets = r"\$\{\{ secrets\.([A-Z_]+) \}\}"
        self.assertEqual(re.findall(secrets, before), re.findall(secrets, after))
        for required in (
            'xcrun stapler staple "$DMG_PATH"', 'ARCHS="arm64 x86_64"',
            "-configuration Default", "CODE_SIGNING_ALLOWED=NO",
            "MACOS_DEPLOYMENT_TARGET: '10.13'", "ONLY_ACTIVE_ARCH=NO",
            'CONFIGURATION_BUILD_DIR="$PWD/$PLUGINS_DIR"', "if-no-files-found: error",
            'ditto -c -k --keepParent "$BUNDLE_PATH" "$ZIP_PATH"',
            'hdiutil create -format UDBZ -volname "$PRODUCT" -srcfolder "$BUNDLE_PATH" "$DMG_PATH"',
        ):
            with self.subTest(command=required):
                self.assertIn(required, before)
                self.assertIn(required, after)
        for required in ('notarize "$ZIP_PATH"', 'notarize "$DMG_PATH"',
                         'if [ "$STATUS" != "Accepted" ]; then'):
            self.assertIn(required, after)


if __name__ == "__main__":
    unittest.main()
