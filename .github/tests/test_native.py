"""Execute literal workflow shells with closed-PATH native stubs, never native tools."""

import importlib.util
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = (ROOT / ".github/workflows/release.yml").read_text()
sys.dont_write_bytecode = True
SPEC = importlib.util.spec_from_file_location(
    "native_source_version", ROOT / ".github/scripts/source_version.py"
)
source = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(source)


def step(name):
    """Read the actual YAML run block rather than testing a shell copy."""
    matches = re.findall(
        rf"^      - name: {re.escape(name)}\n"
        r"(?:(?!      - ).*\n)*?        run: \|\n"
        r"((?:          .*(?:\n|$)|[ \t]*\n)+)",
        WORKFLOW, re.MULTILINE,
    )
    if len(matches) != 1:
        raise AssertionError(f"Expected one shell block for {name!r}")
    return textwrap.dedent(matches[0])


STUB = r'''
import json
import os
from pathlib import Path
import plistlib
import sys

tool = Path(sys.argv[0]).name
args = sys.argv[1:]
root = Path.cwd()
with open(os.environ["CALL_LOG"], "a") as log:
    log.write(json.dumps([tool, *args]) + "\n")
bundle = root / os.environ["TEST_BUNDLE"]
contents = bundle / "Contents"
mode = os.environ.get("BUILD_MODE", "complete")

if tool == "xcodebuild":
    for location in (contents, bundle.parent / "Default/Trash.bundle/Contents"):
        for path in ("MacOS/Trash", "Info.plist",
                     "Resources/English.lproj/InfoPlist.strings", "_CodeSignature"):
            assert not (location / path).exists(), "stale output survived: " + str(location / path)
    for path in (bundle.parent / "Trash.build", root / "build/Trash.build"):
        assert not path.exists(), "stale intermediate survived"
    if mode == "failed-build":
        sys.exit(70)
    if mode != "missing-binary":
        (contents / "MacOS").mkdir(parents=True, exist_ok=True)
        (contents / "MacOS/Trash").write_bytes(b"rebuilt-universal")
    if mode != "missing-plist":
        data = plistlib.loads((root / "Info.plist").read_bytes())
        data["CFBundleShortVersionString"] = os.environ["SOURCE_VERSION"]
        if mode == "stale-plist":
            data["CFBundleShortVersionString"] = os.environ["STALE_VERSION"]
        (contents / "Info.plist").write_bytes(plistlib.dumps(data))
    if mode != "missing-localized":
        target = contents / "Resources/English.lproj/InfoPlist.strings"
        target.parent.mkdir(parents=True, exist_ok=True)
        data = (root / "English.lproj/InfoPlist.strings").read_bytes()
        if mode == "stale-localized":
            data = data.replace(os.environ["SOURCE_VERSION"].encode(),
                                os.environ["STALE_VERSION"].encode())
        target.write_bytes(data)
elif tool == "lipo":
    assert args == ["-archs", os.environ["TEST_BUNDLE"] + "/Contents/MacOS/Trash"]
    assert Path(args[-1]).is_file()
    if os.environ.get("LIPO_FAIL"):
        sys.exit(75)
    print(os.environ.get("ARCHS", "arm64 x86_64"))
elif tool == "python3":
    assert args == [".github/scripts/source_version.py", "--verify-bundle",
                    os.environ["TEST_BUNDLE"]]
    os.execv(os.environ["REAL_PYTHON"], [os.environ["REAL_PYTHON"], *args])
elif tool == "security":
    assert args == ["find-identity", "-v", "-p", "codesigning", os.environ["KEYCHAIN_PATH"]]
    if os.environ.get("SIGN_MODE") != "missing-identity":
        print('1) FAKE "Developer ID Application: Fixture (TEST)"')
elif tool == "codesign":
    verify = args[0] == "--verify"
    expected = (["--verify", "--deep", "--strict", "--verbose=2"] if verify else
                ["--verbose", "--deep", "--timestamp", "--force", "--options=runtime",
                 "--sign", "Developer ID Application: Fixture (TEST)"])
    assert args == [*expected, os.environ["BUNDLE_PATH"]]
    if os.environ.get("SIGN_MODE") == ("verify" if verify else "sign"):
        sys.exit(76)
elif tool in ("ditto", "hdiutil"):
    assert Path(os.environ["BUNDLE_PATH"]).is_dir()
    expected = (["-c", "-k", "--keepParent", os.environ["BUNDLE_PATH"]] if tool == "ditto"
                else ["create", "-format", "UDBZ", "-volname", "Trash",
                      "-srcfolder", os.environ["BUNDLE_PATH"]])
    assert args[:-1] == expected
    if os.environ.get("PACKAGE_MODE") == tool:
        sys.exit(77)
    Path(args[-1]).write_bytes(b"stub archive")
elif tool == "xcrun":
    scenario = os.environ.get("NOTARY_MODE", "accepted")
    if args[:2] == ["notarytool", "submit"]:
        assert args[3:] == ["--keychain-profile", "notarytool-profile", "--keychain",
                            os.environ["KEYCHAIN_PATH"], "--wait", "--output-format", "json"]
        assert Path(args[2]).is_file()
        second = args[2].endswith(".dmg")
        active = scenario.removeprefix("second-") if second else scenario
        if active in ("transport", "accepted-nonzero"):
            if active == "accepted-nonzero":
                print(json.dumps({"id": "misleading", "status": "Accepted"}))
            sys.exit(71)
        if active == "malformed":
            print("not JSON")
        elif active == "missing-status":
            print(json.dumps({"id": "missing-status"}))
        else:
            print(json.dumps({"id": "submission-dmg" if second else "submission-zip",
                              "status": "Invalid" if active == "invalid" else "Accepted"}))
    elif args[:2] == ["notarytool", "log"]:
        assert args[3:] == ["--keychain-profile", "notarytool-profile",
                            "--keychain", os.environ["KEYCHAIN_PATH"]]
        sys.exit(72)
    elif args[:2] == ["stapler", "staple"]:
        assert args == ["stapler", "staple", "Trash-" + os.environ["SOURCE_VERSION"] + ".dmg"]
        if scenario == "staple":
            sys.exit(73)
    else:
        raise AssertionError(args)
elif tool == "plutil":
    assert args[0] == "-extract" and args[2:] == ["raw", "-o", "-", "-"]
    try:
        value = json.load(sys.stdin)[args[1]]
        assert isinstance(value, str)
        print(value)
    except (ValueError, KeyError, AssertionError):
        sys.exit(74)
else:
    raise AssertionError("Unexpected native command: " + tool)
'''


class NativeShellTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix=".native-tests-", dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for path in (*source.SOURCE_PATHS, Path(".github/scripts/source_version.py")):
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / path, target)
        self.version = source.read(self.root)
        major, minor, patch = map(int, self.version.split("."))
        self.stale = f"{major}.{minor}.{patch + 1}"
        self.relative_bundle = "test/Plugins/Trash.bundle"
        self.bundle = self.root / self.relative_bundle
        self.contents = self.bundle / "Contents"
        self.log = self.root / "calls.jsonl"
        self.env_file = self.root / "github-env"
        self.env_file.touch()
        self.bin = self.root / "stub-bin"
        self.bin.mkdir()
        for name in ("xcodebuild", "lipo", "python3", "security", "codesign",
                     "ditto", "hdiutil", "xcrun", "plutil"):
            path = self.bin / name
            path.write_text(f"#!{sys.executable}\n" + textwrap.dedent(STUB))
            path.chmod(0o755)
        # Only non-native filesystem/text utilities are allowed through.
        for name in ("rm", "ls", "grep", "head", "sed", "mkdir", "cp"):
            executable = shutil.which(name)
            self.assertIsNotNone(executable)
            (self.bin / name).symlink_to(executable)
        self.env = {
            "PATH": str(self.bin), "CALL_LOG": str(self.log),
            "REAL_PYTHON": sys.executable, "PYTHONDONTWRITEBYTECODE": "1",
            "SOURCE_VERSION": self.version, "STALE_VERSION": self.stale,
            "TEST_BUNDLE": self.relative_bundle, "BUNDLE_PATH": self.relative_bundle,
            "KEYCHAIN_PATH": str(self.root / "fake.keychain-db"),
            "GITHUB_ENV": str(self.env_file),
        }
        self.preserved = {}
        for relative in (
            "QuickLook/Thumbnail.png", "Windows/Trash.4DX", "Windows64/Trash.4DX",
            "Resources/manifest.json", "Resources/constants.xlf",
        ):
            data = (ROOT / self.relative_bundle / "Contents" / relative).read_bytes()
            self.write(relative, data)
            self.preserved[relative] = data
        for relative in (
            "MacOS/Dependency.dylib",
            "Frameworks/Dependency.framework/Versions/A/Dependency",
            "Frameworks/Dependency.framework/Versions/A/Resources/dependency.dat",
        ):
            self.write(relative, b"preserved dependency")
            self.preserved[relative] = b"preserved dependency"
        (self.contents / "Frameworks/Dependency.framework/Versions/Current").symlink_to("A")
        for location in (self.contents, self.bundle.parent / "Default/Trash.bundle/Contents"):
            for relative in ("MacOS/Trash", "Info.plist",
                             "Resources/English.lproj/InfoPlist.strings",
                             "_CodeSignature/CodeResources"):
                path = location / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"stale build output")
        for relative in ("test/Plugins/Trash.build/stale", "build/Trash.build/stale"):
            path = self.root / relative
            path.parent.mkdir(parents=True)
            path.write_bytes(b"stale intermediate")
        default_resource = self.bundle.parent / "Default/Trash.bundle/Contents/Resources/keep"
        default_resource.write_bytes(b"preserved Default resource")

    def write(self, relative, data):
        path = self.contents / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def run_step(self, name, prefix="", **env):
        script = step(name)
        for key, value in {
            "env.PRODUCT_NAME": "Trash", "env.BASE_DIR": "test",
            "env.MACOS_DEPLOYMENT_TARGET": "10.13",
            "needs.version.outputs.version": self.version,
        }.items():
            script = script.replace("${{ " + key + " }}", value)
        self.assertNotIn("${{", script)
        return subprocess.run(
            ["/bin/bash", "-c", prefix + "\n" + script],
            cwd=self.root, env={**self.env, **env}, text=True,
            capture_output=True, timeout=30,
        )

    def assert_ok(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def calls(self, tool):
        if not self.log.exists():
            return []
        return [call[1:] for line in self.log.read_text().splitlines()
                if (call := json.loads(line))[0] == tool]

    def build(self, **env):
        self.assert_ok(self.run_step("build macOS plugin (universal)", **env))

    def test_fresh_build_preserves_resources_dependencies_and_default_resources(self):
        self.build()
        for relative, data in self.preserved.items():
            self.assertEqual((self.contents / relative).read_bytes(), data, relative)
        link = self.contents / "Frameworks/Dependency.framework/Versions/Current"
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.readlink(), Path("A"))
        self.assertEqual((self.bundle.parent / "Default/Trash.bundle/Contents/Resources/keep")
                         .read_bytes(), b"preserved Default resource")
        self.assert_ok(self.run_step("verify universal binary"))
        self.assertEqual(self.calls("xcodebuild"), [[
            "-project", "Trash.xcodeproj", "-target", "Trash",
            "-configuration", "Default", "ARCHS=arm64 x86_64", "ONLY_ACTIVE_ARCH=NO",
            "MACOSX_DEPLOYMENT_TARGET=10.13", "CODE_SIGNING_ALLOWED=NO",
            f"CONFIGURATION_BUILD_DIR={self.root}/test/Plugins", "build",
        ]])

    def test_cleanup_error_blocks_build(self):
        result = self.run_step("build macOS plugin (universal)",
                               prefix="rm() { echo 'fixture cleanup failure' >&2; return 79; }")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.calls("xcodebuild"), [])
        self.assertEqual((self.contents / "MacOS/Trash").read_bytes(), b"stale build output")

    def test_failed_build_is_fatal(self):
        self.assertNotEqual(self.run_step("build macOS plugin (universal)",
                                         BUILD_MODE="failed-build").returncode, 0)
        self.assertEqual(len(self.calls("xcodebuild")), 1)

    def test_missing_rebuilt_binary_is_rejected(self):
        self.build(BUILD_MODE="missing-binary")
        result = self.run_step("verify universal binary")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Expected binary not found", result.stdout)
        self.assertEqual(self.calls("lipo"), [])

    def test_missing_and_stale_selected_metadata_are_rejected(self):
        for mode in ("missing-plist", "missing-localized", "stale-plist", "stale-localized"):
            with self.subTest(mode=mode):
                self.build(BUILD_MODE=mode)
                self.assertNotEqual(self.run_step("verify universal binary").returncode, 0)

    def test_each_architecture_requires_an_exact_token(self):
        self.build()
        for archs in ("arm64", "x86_64", "arm64e x86_64", "arm64 x86_64h",
                      "notarm64 x86_64", "arm64 x86_64_extra"):
            with self.subTest(archs=archs):
                self.assertNotEqual(self.run_step("verify universal binary", ARCHS=archs)
                                    .returncode, 0)
        self.assertEqual(self.calls("python3"), [])

    def test_extra_architectures_are_not_prohibited(self):
        self.build()
        self.assert_ok(self.run_step("verify universal binary", ARCHS="arm64 x86_64 i386 arm64e"))

    def test_lipo_failure_is_fatal(self):
        self.build()
        self.assertNotEqual(self.run_step("verify universal binary", LIPO_FAIL="1").returncode, 0)
        self.assertEqual(self.calls("python3"), [])

    def localized_values(self):
        text = source.text(self.root / source.LOCALIZED)
        return dict(re.findall(r'(?m)^(\w+)\s*=\s*"([^"]*)";', text))

    def test_binary_and_xml_plist_and_localization(self):
        self.build()
        values = self.localized_values()
        plist = plistlib.loads((self.contents / "Info.plist").read_bytes())
        self.assertNotIn("CFBundleVersion", plist)
        for fmt in (plistlib.FMT_BINARY, plistlib.FMT_XML):
            with self.subTest(fmt=fmt):
                self.write("Info.plist", plistlib.dumps(plist, fmt=fmt))
                self.write("Resources/English.lproj/InfoPlist.strings", plistlib.dumps(values, fmt=fmt))
                self.assert_ok(self.run_step("verify universal binary"))

    def test_optional_build_version_absence_and_present_value(self):
        self.build()
        plist = plistlib.loads((self.contents / "Info.plist").read_bytes())
        self.assertNotIn("CFBundleVersion", plist)
        self.assert_ok(self.run_step("verify universal binary"))
        for value in (self.version, self.stale, "$(CURRENT_PROJECT_VERSION)"):
            with self.subTest(value=value):
                self.write("Info.plist", plistlib.dumps({**plist, "CFBundleVersion": value}))
                result = self.run_step("verify universal binary")
                if value == self.version:
                    self.assert_ok(result)
                else:
                    self.assertNotEqual(result.returncode, 0)

    def test_localization_must_be_at_real_resources_copy_destination(self):
        self.build()
        localized = self.contents / "Resources/English.lproj/InfoPlist.strings"
        data = localized.read_bytes()
        localized.unlink()
        self.write("English.lproj/InfoPlist.strings", data)
        self.assertNotEqual(self.run_step("verify universal binary").returncode, 0)
        self.write("Resources/English.lproj/InfoPlist.strings", data)
        self.assert_ok(self.run_step("verify universal binary"))

    def test_stale_compiled_localized_displays(self):
        self.build()
        values = self.localized_values()
        for fmt in (plistlib.FMT_BINARY, plistlib.FMT_XML):
            for key in ("CFBundleShortVersionString", "CFBundleGetInfoString"):
                with self.subTest(fmt=fmt, key=key):
                    stale = {**values, key: values[key].replace(self.version, self.stale)}
                    self.write("Resources/English.lproj/InfoPlist.strings", plistlib.dumps(stale, fmt=fmt))
                    self.assertNotEqual(self.run_step("verify universal binary").returncode, 0)

    def test_utf16_localization(self):
        self.build()
        original = source.text(self.root / source.LOCALIZED)
        for encoding, prefix in (("utf-16", b""), ("utf-16-be", b"\xfe\xff")):
            for stale in (False, True):
                with self.subTest(encoding=encoding, stale=stale):
                    text = original.replace(self.version, self.stale) if stale else original
                    self.write("Resources/English.lproj/InfoPlist.strings", prefix + text.encode(encoding))
                    result = self.run_step("verify universal binary")
                    if stale:
                        self.assertNotEqual(result.returncode, 0)
                    else:
                        self.assert_ok(result)

    def test_exact_windows_artifact_merge(self):
        artifact = self.root / "winbuild/Trash.4DX"
        nested = artifact.parent / "stale/Trash.4DX"
        nested.parent.mkdir(parents=True)
        nested.write_bytes(b"wrong recursive match")
        for data in (None, b""):
            if data is not None:
                artifact.write_bytes(data)
            result = self.run_step("merge Windows binaries into the bundle")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("exact artifact path", result.stdout)
            self.assertEqual(self.env_file.read_text(), "")
        artifact.write_bytes(b"fresh selected-source Windows binary")
        self.assert_ok(self.run_step("merge Windows binaries into the bundle"))
        self.assertEqual((self.contents / "Windows64/Trash.4DX").read_bytes(), artifact.read_bytes())
        self.assertEqual(self.env_file.read_text(), f"BUNDLE_PATH={self.relative_bundle}\n")

    def test_identity_signing_and_strict_verification_are_required(self):
        self.assert_ok(self.run_step("codesign"))
        self.assertEqual(len(self.calls("codesign")), 2)
        for scenario, count in (("missing-identity", 0), ("sign", 1), ("verify", 2)):
            with self.subTest(scenario=scenario):
                self.log.unlink()
                self.assertNotEqual(self.run_step("codesign", SIGN_MODE=scenario).returncode, 0)
                self.assertEqual(len(self.calls("codesign")), count)
                self.assertEqual(self.calls("ditto"), [])
                self.assertEqual(self.calls("xcrun"), [])

    def assert_package_calls(self, submissions, staple=False, diagnostic=None):
        zip_path = f"Trash-{self.version}.zip"
        dmg_path = f"Trash-{self.version}.dmg"
        calls = self.calls("xcrun")
        self.assertEqual([c[2] for c in calls if c[:2] == ["notarytool", "submit"]],
                         [zip_path, dmg_path][:submissions])
        self.assertEqual([c for c in calls if c[:2] == ["stapler", "staple"]],
                         [["stapler", "staple", dmg_path]] if staple else [])
        self.assertEqual([c[2] for c in calls if c[:2] == ["notarytool", "log"]],
                         [diagnostic] if diagnostic else [])

    def test_both_accepted_submissions_then_staple_export_paths(self):
        self.assert_ok(self.run_step("package, notarize & staple"))
        self.assert_package_calls(2, staple=True)
        self.assertEqual(self.env_file.read_text().splitlines(), [
            f"ZIP_PATH=Trash-{self.version}.zip", f"DMG_PATH=Trash-{self.version}.dmg",
        ])

    def test_notary_failure_blocks_staple_and_path_export_for_either_asset(self):
        for prefix, count in (("", 1), ("second-", 2)):
            for mode in ("transport", "accepted-nonzero", "invalid", "malformed", "missing-status"):
                with self.subTest(mode=prefix + mode):
                    self.log.unlink(missing_ok=True)
                    result = self.run_step("package, notarize & staple", NOTARY_MODE=prefix + mode)
                    self.assertNotEqual(result.returncode, 0)
                    diagnostic = ("submission-dmg" if count == 2 else "submission-zip") if mode == "invalid" else None
                    self.assert_package_calls(count, diagnostic=diagnostic)
                    self.assertEqual(self.env_file.read_text(), "")

    def test_staple_failure_blocks_path_export(self):
        self.assertNotEqual(self.run_step("package, notarize & staple",
                                         NOTARY_MODE="staple").returncode, 0)
        self.assert_package_calls(2, staple=True)
        self.assertEqual(self.env_file.read_text(), "")

    def test_packaging_failure_blocks_notary_and_export(self):
        for tool in ("ditto", "hdiutil"):
            with self.subTest(tool=tool):
                self.log.unlink(missing_ok=True)
                self.assertNotEqual(self.run_step("package, notarize & staple",
                                                 PACKAGE_MODE=tool).returncode, 0)
                self.assertEqual(self.calls("xcrun"), [])
                self.assertEqual(self.env_file.read_text(), "")


class NativeConfigurationTests(unittest.TestCase):
    def test_exact_windows_release_output(self):
        ns = {"m": "http://schemas.microsoft.com/developer/msbuild/2003"}
        project = ET.parse(ROOT / "Trash.vcxproj")
        condition = "'$(Configuration)|$(Platform)'=='Release|x64'"
        outdirs = [n.text for n in project.findall(".//m:OutDir", ns) if n.get("Condition") == condition]
        extensions = [n.text for n in project.findall(".//m:TargetExt", ns) if n.get("Condition") == condition]
        self.assertEqual(len(outdirs), 1)
        self.assertEqual(extensions, [".4DX"])
        actual = outdirs[0].replace("$(ProjectDir)", "").replace("$(ProjectName)", "Trash").replace("\\", "/") + "Trash.4DX"
        normalize = step("normalize windows output to canonical case")
        output = re.search(r'^\$source = "([^"]+)"$', normalize, re.MULTILINE)[1]
        self.assertEqual(actual, output.replace("$base", "test").replace("$product", "Trash"))
        self.assertNotIn("-Recurse", normalize)
        self.assertNotIn("SilentlyContinue", WORKFLOW)
        self.assertIn("/p:Configuration=Release /p:Platform=x64 /p:PlatformToolset=v143 /p:WindowsTargetPlatformVersion=10.0", step("build x64"))

    def test_original_macos_resources_and_secret_names(self):
        project = (ROOT / "Trash.xcodeproj/project.pbxproj").read_text()
        for resource in ("Cocoa.framework in Frameworks", "InfoPlist.strings in Resources",
                         "manifest.json in CopyFiles", "constants.xlf in CopyFiles",
                         "English.lproj/InfoPlist.strings", "dstSubfolderSpec = 7;"):
            self.assertIn(resource, project)
        self.assertEqual(set(re.findall(r"\$\{\{ secrets\.([A-Z_]+) \}\}", WORKFLOW)), {
            "APPLE_DEVELOPER_ID_CERTIFICATE", "APPLE_DEVELOPER_ID_CERTIFICATE_PASSWORD",
            "KEYCHAIN_PASSWORD", "NOTARYTOOL_APPLE_ID", "NOTARYTOOL_TEAM_ID", "NOTARYTOOL_PASSWORD",
        })
        self.assertIn("MACOS_DEPLOYMENT_TARGET: '10.13'", WORKFLOW)


@unittest.skipUnless(shutil.which("pwsh"), "PowerShell required for Windows shell harness")
class WindowsShellTests(unittest.TestCase):
    def setUp(self):
        self.pwsh = shutil.which("pwsh")
        self.temp = tempfile.TemporaryDirectory(prefix=".windows-tests-", dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "empty-path"
        self.bin.mkdir()
        self.binary = self.root / "test/plugins/Trash.bundle/contents/windows64/Trash.4DX"
        self.binary.parent.mkdir(parents=True)

    def run_step(self, name, prefix=""):
        script = step(name)
        for key, value in (("PRODUCT_NAME", "Trash"), ("BASE_DIR", "test")):
            script = script.replace("${{ env." + key + " }}", value)
        self.assertNotIn("${{", script)
        return subprocess.run(
            [self.pwsh, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", prefix + "\n" + script],
            cwd=self.root, text=True, env={**os.environ, "PATH": str(self.bin)},
            capture_output=True, timeout=30,
        )

    def test_cleanup_invalidates_only_executable_and_intermediates(self):
        self.binary.write_bytes(b"stale")
        preserved = {}
        for relative in ("QuickLook/Thumbnail.png", "Resources/manifest.json",
                         "MacOS/Trash", "Windows/Trash.4DX"):
            path = self.binary.parent.parent / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"preserved")
            preserved[path] = path.read_bytes()
        intermediate = self.root / "Objs/Release/stale"
        intermediate.parent.mkdir(parents=True)
        intermediate.write_bytes(b"stale")
        for _ in range(2):
            result = self.run_step("clean stale outputs")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.binary.exists())
        self.assertFalse((self.root / "Objs").exists())
        for path, data in preserved.items():
            self.assertEqual(path.read_bytes(), data)

    def test_exact_output_required_then_normalized_without_self_copy(self):
        stale = self.root / "stale/Plugins/Trash.4DX"
        stale.parent.mkdir(parents=True)
        stale.write_bytes(b"wrong recursive match")
        result = self.run_step("normalize windows output to canonical case")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("exact MSBuild output", result.stderr)
        self.binary.write_bytes(b"rebuilt")
        result = self.run_step("normalize windows output to canonical case")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        canonical = self.root / "test/Plugins/Trash.bundle/Contents/Windows64/Trash.4DX"
        self.assertEqual(canonical.read_bytes(), b"rebuilt")
        if self.binary.samefile(canonical):
            self.assertIn("skipping copy", result.stdout)

    def test_empty_output_is_rejected(self):
        self.binary.touch()
        result = self.run_step("normalize windows output to canonical case")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("empty", result.stderr)

    def test_cleanup_errors_are_fatal(self):
        self.binary.write_bytes(b"stale")
        result = self.run_step("clean stale outputs",
                               "function Remove-Item { Write-Error 'fixture cleanup failure' }")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("fixture cleanup failure", result.stderr)
        self.assertEqual(self.binary.read_bytes(), b"stale")

    def test_directory_errors_are_fatal(self):
        self.binary.write_bytes(b"rebuilt")
        result = self.run_step("normalize windows output to canonical case",
                               "function New-Item { Write-Error 'fixture directory failure' }")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("fixture directory failure", result.stderr)


if __name__ == "__main__":
    unittest.main()
