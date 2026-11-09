import importlib.util
from pathlib import Path
import plistlib
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("source_version", ROOT / ".github/scripts/source_version.py")
source = importlib.util.module_from_spec(spec)
spec.loader.exec_module(source)


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix=".source-tests-", dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.copy_source()
        self.current = source.read(self.root)
        self.drift = f"{int(self.current.split('.')[0]) + 1}.9.7"

    def copy_source(self):
        for name in source.SOURCE_PATHS:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, path)

    def plist(self, **values):
        path = self.root / source.PLIST
        data = plistlib.loads(path.read_bytes())
        data.update(values)
        path.write_bytes(plistlib.dumps(data))

    def snapshot(self):
        return {name: ((self.root / name).read_bytes(),
                       stat.S_IMODE((self.root / name).stat().st_mode))
                for name in source.SOURCE_PATHS if (self.root / name).exists()}

    def assert_rejected(self, error=ValueError):
        before = self.snapshot()
        with patch.object(Path, "write_bytes") as write:
            with self.assertRaises(error):
                source.read(self.root)
            with self.assertRaises(error):
                source.bump("patch", self.root)
            write.assert_not_called()
        self.assertEqual(before, self.snapshot())

    def test_live_paths_and_all_bump_modes(self):
        self.assertEqual(source.PROJECT, Path("Trash.xcodeproj/project.pbxproj"))
        self.assertEqual(source.SETTING_COUNT, 6)
        major, minor, patch_version = map(int, self.current.split("."))
        for mode, expected in (
            ("patch", f"{major}.{minor}.{patch_version + 1}"),
            ("minor", f"{major}.{minor + 1}.0"),
            ("major", f"{major + 1}.0.0"),
        ):
            with self.subTest(mode=mode):
                self.copy_source()
                before = self.snapshot()
                self.assertEqual(source.bump(mode, self.root), expected)
                self.assertEqual(source.read(self.root), expected)
                for name, (original, permissions) in before.items():
                    path = self.root / name
                    self.assertEqual(path.read_bytes(), original.replace(
                        self.current.encode(), expected.encode()))
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), permissions)
                plist = plistlib.loads((self.root / source.PLIST).read_bytes())
                self.assertEqual(plist["CFBundleShortVersionString"], "$(MARKETING_VERSION)")
                self.assertNotIn("CFBundleVersion", plist)

    def test_coordinated_stable_fixture(self):
        (self.root / source.PROJECT).write_text(
            ("MARKETING_VERSION = 3.2.1;\nCURRENT_PROJECT_VERSION = 3.2.1;\n")
            * source.SETTING_COUNT)
        self.plist(CFBundleShortVersionString="3.2.1", CFBundleVersion="3.2.1")
        (self.root / source.LOCALIZED).write_text(
            'CFBundleName = "Trash";\nCFBundleShortVersionString = "3.2.1";\n'
            'CFBundleGetInfoString = "Trash version 3.2.1, Copyright 2020 miyako.";\n')
        before = self.snapshot()
        self.assertEqual(source.bump("minor", self.root), "3.3.0")
        for name, (data, _) in before.items():
            self.assertEqual((self.root / name).read_bytes(), data.replace(b"3.2.1", b"3.3.0"))

    def test_strict_semver_and_drift_before_any_write(self):
        project = self.root / source.PROJECT
        original = source.text(project)
        for key in source.KEYS:
            for value in ("1.2", "77", "v1.2.3", "01.2.3", "1.02.3", "1.2.03",
                          "1.2.3-beta", "1.2.3+build", "1.2.3.4", "-1.2.3",
                          "$(UNKNOWN)", "", self.drift, "$(CURRENT_PROJECT_VERSION)",
                          "${CURRENT_PROJECT_VERSION}"):
                for count in (1, source.SETTING_COUNT):
                    with self.subTest(key=key, value=value, count=count):
                        project.write_text(original.replace(
                            f"{key} = {self.current};", f"{key} = {value};", count))
                        self.assert_rejected()
        for reference in source.REFERENCES:
            project.write_text(original.replace(
                f"MARKETING_VERSION = {self.current};",
                f'MARKETING_VERSION = "{reference}";'))
            self.assert_rejected()

    def test_missing_extra_and_malformed_project_settings(self):
        project = self.root / source.PROJECT
        original = source.text(project)
        for key in source.KEYS:
            project.write_text(original.replace(f"{key} = {self.current};", "", 1))
            self.assert_rejected()
            for extra in (f"{key} = {self.current};", f'"{key}" = "{self.current}";',
                          f"{key} = {self.current}", f"{key} = ;", f"{key} = invalid;"):
                project.write_text(original + "\n" + extra + "\n")
                self.assert_rejected()

    def test_inline_project_duplicates_after_any_assignment(self):
        project = self.root / source.PROJECT
        original = source.text(project)
        for key in source.KEYS:
            for quoted in (False, True):
                name = f'"{key}"' if quoted else key
                for value in (self.current, f'"{self.current}"', self.drift,
                              f'"{self.drift}"', "$(UNKNOWN)", '"$(UNKNOWN)"', ""):
                    for location in ("version", "other"):
                        with self.subTest(key=key, quoted=quoted, value=value, location=location):
                            extra = f"{name} = {value};"
                            if location == "version":
                                assignment = f"{key} = {self.current};"
                                mutated = original.replace(assignment, assignment + " " + extra, 1)
                            else:
                                mutated = original + f'\nOTHER = "unchanged"; {extra}\n'
                            project.write_text(mutated)
                            self.assert_rejected()

    def test_lexically_scoped_comments_strings_and_unrelated_versions(self):
        project = self.root / source.PROJECT
        noise = (
            f'\n/* MARKETING_VERSION = {self.current}; */\n'
            f'/*\nMARKETING_VERSION = {self.current};\n'
            f'CURRENT_PROJECT_VERSION = {self.current};\n*/\n'
            f'// "MARKETING_VERSION" = {self.current}; CURRENT_PROJECT_VERSION = bad;\n'
            f'OTHER = "MARKETING_VERSION = {self.current}; '
            f'\\"CURRENT_PROJECT_VERSION\\" = {self.current};";\n'
            'MARKETING_VERSION_SUFFIX = unchanged; PREFIX_CURRENT_PROJECT_VERSION = unchanged;\n'
        ).encode()
        project.write_bytes(project.read_bytes() + noise)
        self.plist(UnrelatedVersion=self.current)
        localized = self.root / source.LOCALIZED
        extra = f'\rUnrelatedVersion = "{self.current}";\r'.encode()
        localized.write_bytes(localized.read_bytes() + extra)
        new = source.bump("patch", self.root)
        self.assertEqual(source.read(self.root), new)
        self.assertTrue(project.read_bytes().endswith(noise))
        self.assertTrue(localized.read_bytes().endswith(extra))
        self.assertEqual(plistlib.loads((self.root / source.PLIST).read_bytes())["UnrelatedVersion"],
                         self.current)

    def test_valid_inline_assignments_are_scoped_and_rewritten(self):
        path = self.root / source.PROJECT
        value = source.text(path)
        for key in source.KEYS:
            value = value.replace(f"{key} = {self.current};",
                                  f'OTHER = "unchanged"; {key} = {self.current};')
        path.write_text(value)
        new = source.bump("patch", self.root)
        self.assertEqual(path.read_bytes(), value.replace(self.current, new).encode())

    def test_quoted_project_keys_fail_closed(self):
        path = self.root / source.PROJECT
        for key in source.KEYS:
            self.copy_source()
            path.write_text(source.text(path).replace(key + " =", f'"{key}" ='))
            self.assert_rejected()

    def test_supported_references_preserved(self):
        for reference in source.REFERENCES:
            for marketing_ref in ("$(MARKETING_VERSION)", "${MARKETING_VERSION}"):
                with self.subTest(reference=reference, marketing_ref=marketing_ref):
                    self.copy_source()
                    self.plist(CFBundleShortVersionString=reference, CFBundleVersion=reference)
                    project = self.root / source.PROJECT
                    project.write_text(source.SETTING.sub(
                        lambda m: f'{m[1]}"{marketing_ref}"{m[4]}'
                        if m[2] == "CURRENT_PROJECT_VERSION" else m[0], source.text(project)))
                    before = self.snapshot()
                    new = source.bump("patch", self.root)
                    self.assertEqual(source.read(self.root), new)
                    for name, (data, _) in before.items():
                        self.assertEqual((self.root / name).read_bytes(), data.replace(
                            self.current.encode(), new.encode()))

    def test_optional_plist_versions_are_not_invented(self):
        for short in (False, True):
            for build in (False, True):
                self.copy_source()
                path = self.root / source.PLIST
                data = plistlib.loads(path.read_bytes())
                data.pop("CFBundleShortVersionString", None)
                data.pop("CFBundleVersion", None)
                if short:
                    data["CFBundleShortVersionString"] = self.current
                if build:
                    data["CFBundleVersion"] = self.current
                path.write_bytes(plistlib.dumps(data))
                new = source.bump("minor", self.root)
                result = plistlib.loads(path.read_bytes())
                for key, present in (("CFBundleShortVersionString", short),
                                     ("CFBundleVersion", build)):
                    self.assertEqual(key in result, present)
                    if present:
                        self.assertEqual(result[key], new)

    def test_plist_invalid_values_and_types(self):
        for key in ("CFBundleShortVersionString", "CFBundleVersion"):
            for value in ("$(UNKNOWN)", "$(PRODUCT_NAME)", "", "invalid", 123, True, [], {},
                          self.drift, "1.2", "77", "01.2.3", "v1.2.3", "1.2.3-beta"):
                with self.subTest(key=key, value=value):
                    self.copy_source()
                    self.plist(**{key: value})
                    self.assert_rejected()

    def test_duplicate_plist_keys_and_invalid_serialization(self):
        path = self.root / source.PLIST
        for key in ("CFBundleShortVersionString", "CFBundleVersion", "CFBundleExecutable"):
            self.copy_source()
            self.plist(**{key: self.current})
            data = source.text(path)
            path.write_text(data.replace(
                "</dict>", f"<key>{key}</key><string>{self.drift}</string>\n</dict>"))
            self.assert_rejected()
        self.copy_source()
        original = path.read_bytes()
        for malformed in (b"not a plist", b"", original.replace(b"</dict>", b""),
                          original.replace(b"$(MARKETING_VERSION)", b"&#36;(MARKETING_VERSION)"),
                          plistlib.dumps(plistlib.loads(original), fmt=plistlib.FMT_BINARY),
                          plistlib.dumps([])):
            path.write_bytes(malformed)
            self.assert_rejected()

    def test_localized_version_validation(self):
        path = self.root / source.LOCALIZED
        original = source.text(path)
        for pattern in (source.LOCALIZED_SHORT, source.LOCALIZED_INFO):
            for bad in ("1.0", self.drift, "invalid", "", "$(UNKNOWN)", "$(MARKETING_VERSION)",
                        "${CURRENT_PROJECT_VERSION}", "01.2.3", "1.2.3-beta"):
                path.write_text(pattern.sub(lambda m: f"{m[1]}{bad}{m[3]}", original))
                self.assert_rejected()
            path.write_text(pattern.sub("", original))
            self.assert_rejected()
        for old, new in (("Trash", "Other Product"), ("Copyright 2020", "Copyright 2023"),
                         ("Copyright 2020 miyako", "Copyright 2020 someone")):
            path.write_text(original.replace(old, new))
            self.assert_rejected()

    def test_localized_duplicates_quoted_inline_and_malformed(self):
        path = self.root / source.LOCALIZED
        original = source.text(path)
        for key in ("CFBundleShortVersionString", "CFBundleGetInfoString"):
            for name in (key, f'"{key}"'):
                for prefix in ("", 'OTHER = "unchanged"; '):
                    path.write_text(original + f'\n{prefix}{name} = "{self.current}";\n')
                    self.assert_rejected()
                path.write_text(original + f'\n{name} = "unterminated\n')
                self.assert_rejected()

    def test_quoted_localization_and_comments(self):
        path = self.root / source.LOCALIZED
        value = source.text(path)
        for key in ("CFBundleShortVersionString", "CFBundleGetInfoString"):
            value = value.replace(key + " =", f'"{key}" =')
        noise = ('// CFBundleShortVersionString = "not assignment";\n'
                 '/* "CFBundleGetInfoString" = "not assignment"; */\n')
        path.write_text(value + noise)
        new = source.bump("patch", self.root)
        self.assertEqual(path.read_bytes(), (value.replace(self.current, new) + noise).encode())

    def test_missing_sources_do_not_partially_write(self):
        for name in source.SOURCE_PATHS:
            self.copy_source()
            (self.root / name).unlink()
            self.assert_rejected(FileNotFoundError)

    def test_unsupported_source_encoding_before_any_write(self):
        for name in source.SOURCE_PATHS:
            self.copy_source()
            path = self.root / name
            path.write_bytes(source.text(path).encode("utf-16"))
            self.assert_rejected(UnicodeError if name != source.PLIST else ValueError)

    def test_byte_preserving_bom_mixed_endings_and_modes(self):
        for newline in ("\n", "\r", "\r\n", "mixed"):
            for bom in (b"", b"\xef\xbb\xbf"):
                with self.subTest(newline=newline, bom=bom):
                    self.copy_source()
                    for i, name in enumerate(source.SOURCE_PATHS):
                        path = self.root / name
                        lines = source.text(path).splitlines(keepends=True)
                        data = "".join(line.replace(
                            "\n", ("\r", "\r\n", "\n")[j % 3] if newline == "mixed" else newline)
                            for j, line in enumerate(lines))
                        path.write_bytes(bom + data.encode())
                        path.chmod((0o755, 0o640, 0o644)[i])
                    before = self.snapshot()
                    new = source.bump("minor", self.root)
                    for name, (data, mode) in before.items():
                        path = self.root / name
                        self.assertEqual(path.read_bytes(), data.replace(
                            self.current.encode(), new.encode()))
                        self.assertEqual(stat.S_IMODE(path.stat().st_mode), mode)

    def test_whitespace_and_quoted_values_preserved(self):
        for quoted in (False, True):
            self.copy_source()
            project = self.root / source.PROJECT
            value = f'"{self.current}"' if quoted else self.current
            original = project.read_bytes()
            for key in source.KEYS:
                original = original.replace(
                    f"{key} = {self.current};".encode(), f"{key} \t= \t{value} \t;".encode())
            project.write_bytes(original)
            path = self.root / source.LOCALIZED
            path.write_bytes(path.read_bytes().replace(b" = ", b" \t= \t")
                             .replace(b'";', b'" \t; \t'))
            before = self.snapshot()
            new = source.bump("patch", self.root)
            for name, (data, _) in before.items():
                self.assertEqual((self.root / name).read_bytes(), data.replace(
                    self.current.encode(), new.encode()))

    def test_prebuilt_native_and_4d_files_are_excluded(self):
        tracked = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, check=True,
                                 capture_output=True).stdout.decode().split("\0")
        excluded = [name for name in tracked if name.startswith((
            "test/Plugins/", "test/Data/", "test/Project/", "4D Plugin API/"))]
        excluded += ["Trash.sln", "Trash.vcxproj", "Trash.vcxproj.filters",
                     "4DPlugin-Trash.cpp", "4DPlugin-Trash.h", "manifest.json", "constants.xlf",
                     "constants.json", "include/json/version.h", "Trash.dmg", "Trash.zip"]
        for name in excluded:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, path)
        before = {name: ((self.root / name).read_bytes(), (self.root / name).stat().st_mode)
                  for name in excluded}
        source.bump("major", self.root)
        self.assertEqual(before, {
            name: ((self.root / name).read_bytes(), (self.root / name).stat().st_mode)
            for name in excluded})

    def test_original_authority_and_exact_source_only_repair(self):
        baseline = "9c10eb97f3c38b8abfa4dcaf58912e9cc8561eb2"
        workflow = subprocess.run(
            ["git", "show", f"{baseline}:.github/workflows/bump-version.yml"],
            cwd=ROOT, check=True, capture_output=True, text=True).stdout
        header = workflow.split("name: Bump version", 1)[0]
        self.assertIn("MARKETING_VERSION/CURRENT_PROJECT_VERSION", header)
        self.assertIn("CFBundleShortVersionString/CFBundleVersion", header)
        for name in source.SOURCE_PATHS:
            original = subprocess.run(["git", "show", f"{baseline}:{name}"], cwd=ROOT,
                                      check=True, capture_output=True).stdout
            if name == source.PROJECT:
                expected = original.replace(
                    b"CURRENT_PROJECT_VERSION = 1.0.1;",
                    f"CURRENT_PROJECT_VERSION = {self.current};".encode(),
                ).replace(
                    b"MARKETING_VERSION = 1.0.2;",
                    f"MARKETING_VERSION = {self.current};".encode(),
                )
            elif name == source.LOCALIZED:
                expected = original.replace(
                    b'"1.0.1"', f'"{self.current}"'.encode(),
                ).replace(b"version 1.0.1,", f"version {self.current},".encode())
            else:
                expected = original
            self.assertEqual((ROOT / name).read_bytes(), expected)
            mode = subprocess.run(["git", "ls-tree", baseline, "--", str(name)], cwd=ROOT,
                                  check=True, capture_output=True, text=True).stdout.split()[0]
            self.assertEqual(stat.S_IMODE((ROOT / name).stat().st_mode), int(mode, 8) & 0o777)

    def test_invalid_mode_and_cli_read_never_write(self):
        before = self.snapshot()
        with self.assertRaises(ValueError):
            source.bump("other", self.root)
        output = subprocess.run([sys.executable, str(ROOT / ".github/scripts/source_version.py")],
                                cwd=self.root, check=True, capture_output=True, text=True)
        self.assertEqual(output.stdout.strip(), self.current)
        self.assertEqual(before, self.snapshot())

    def built_bundle(self):
        contents = self.root / "built.bundle/Contents"
        localized = contents / "Resources/English.lproj/InfoPlist.strings"
        localized.parent.mkdir(parents=True)
        shutil.copy2(self.root / source.LOCALIZED, localized)
        (contents / "Info.plist").write_bytes(plistlib.dumps({
            "CFBundleShortVersionString": self.current}))
        return contents, localized

    def test_bundle_display_and_optional_build_validation(self):
        contents, _ = self.built_bundle()
        source.verify_bundle(contents.parent, self.root)
        built = contents / "Info.plist"
        for value in ([], {"CFBundleShortVersionString": self.drift}):
            built.write_bytes(plistlib.dumps(value))
            with self.assertRaisesRegex(ValueError, "display version"):
                source.verify_bundle(contents.parent, self.root)
        built.write_bytes(plistlib.dumps({"CFBundleShortVersionString": self.current}))
        self.plist(CFBundleVersion=self.current)
        with self.assertRaisesRegex(ValueError, "build version"):
            source.verify_bundle(contents.parent, self.root)
        built.write_bytes(plistlib.dumps({"CFBundleShortVersionString": self.current,
                                         "CFBundleVersion": self.current}))
        source.verify_bundle(contents.parent, self.root)

    def test_bundle_localization_utf16_xml_and_binary(self):
        contents, localized = self.built_bundle()
        value = source.text(self.root / source.LOCALIZED)
        localized.write_bytes(value.encode("utf-16"))
        source.verify_bundle(contents.parent, self.root)
        fields = {"CFBundleShortVersionString": self.current,
                  "CFBundleGetInfoString":
                      f"Trash version {self.current}, Copyright 2020 miyako."}
        for fmt in (plistlib.FMT_XML, plistlib.FMT_BINARY):
            localized.write_bytes(plistlib.dumps(fields, fmt=fmt))
            source.verify_bundle(contents.parent, self.root)
        localized.write_bytes(value.replace(self.current, self.drift).encode("utf-16"))
        with self.assertRaises(ValueError):
            source.verify_bundle(contents.parent, self.root)


if __name__ == "__main__":
    unittest.main()
