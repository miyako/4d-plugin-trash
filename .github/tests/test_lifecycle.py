"""Offline source-version and bare-origin lifecycle regression tests."""

import contextlib
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / ".github/scripts"))
import release
import source_version as source


@contextlib.contextmanager
def cwd(path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def git(path, *args):
    location = ["--git-dir", str(path)] if path.name.endswith(".git") else ["-C", str(path)]
    return subprocess.run(
        ["git", *location, *args], check=True, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ).stdout.strip()


def copy_source(destination):
    for path in source.SOURCE_PATHS:
        (destination / path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / path, destination / path)


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix=".lifecycle-source-", dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        copy_source(self.root)
        self.current = source.read(self.root)
        self.drift = f"{int(self.current.split('.')[0]) + 1}.9.7"

    def plist(self, **values):
        path = self.root / source.PLIST
        data = plistlib.loads(path.read_bytes())
        data.update(values)
        path.write_bytes(plistlib.dumps(data))

    def test_source_contract_and_all_bump_modes(self):
        self.assertEqual(tuple(map(str, source.SOURCE_PATHS)), (
            "Trash.xcodeproj/project.pbxproj", "Info.plist", "English.lproj/InfoPlist.strings",
        ))
        self.assertEqual(source.SETTING_COUNT, 6)
        parts = list(map(int, self.current.split(".")))
        for mode, expected in (
            ("patch", [parts[0], parts[1], parts[2] + 1]),
            ("minor", [parts[0], parts[1] + 1, 0]),
            ("major", [parts[0] + 1, 0, 0]),
        ):
            with self.subTest(mode=mode):
                copy_source(self.root)
                expected = ".".join(map(str, expected))
                before = {path: (self.root / path).read_bytes() for path in source.SOURCE_PATHS}
                self.assertEqual(source.bump(mode, self.root), expected)
                self.assertEqual(source.read(self.root), expected)
                for path, original in before.items():
                    self.assertEqual((self.root / path).read_bytes(),
                                     original.replace(self.current.encode(), expected.encode()))
                self.assertNotIn("CFBundleVersion",
                                 plistlib.loads((self.root / source.PLIST).read_bytes()))
                for key in source.KEYS:
                    self.assertEqual((self.root / source.PROJECT).read_text().count(
                        f"{key} = {expected};"), source.SETTING_COUNT)

    def test_project_invalid_missing_drifted_extra_and_cyclic_settings(self):
        path = self.root / source.PROJECT
        original = path.read_text()
        for key in source.KEYS:
            for bad in ("1.2", "v1.2.3", "01.2.3", "1.2.3-beta", "$(UNKNOWN)", "", self.drift):
                with self.subTest(key=key, bad=bad):
                    path.write_text(original.replace(f"{key} = {self.current};",
                                                     f"{key} = {bad};", 1))
                    with self.assertRaises(ValueError):
                        source.bump("patch", self.root)
            for value in (original.replace(f"{key} = {self.current};", "", 1),
                          original + f"\n{key} = {self.current};\n"):
                path.write_text(value)
                with self.assertRaises(ValueError):
                    source.read(self.root)
        for bad in ("14.3", "77", self.drift, "$(CURRENT_PROJECT_VERSION)"):
            for count in (1, source.SETTING_COUNT):
                with self.subTest(bad=bad, count=count):
                    path.write_text(original.replace(
                        f"CURRENT_PROJECT_VERSION = {self.current};",
                        f"CURRENT_PROJECT_VERSION = {bad};", count))
                    with self.assertRaises(ValueError):
                        source.read(self.root)

    def test_plist_invalid_drift_and_wrong_root(self):
        for key in ("CFBundleShortVersionString", "CFBundleVersion"):
            for value in ("$(UNKNOWN)", "$(PRODUCT_NAME)", "", "invalid", 123,
                          self.drift, "1.2", "14.3", "7"):
                with self.subTest(key=key, value=value):
                    copy_source(self.root)
                    self.plist(**{key: value})
                    with self.assertRaises(ValueError):
                        source.read(self.root)
        (self.root / source.PLIST).write_bytes(plistlib.dumps([]))
        with self.assertRaisesRegex(ValueError, "dictionary"):
            source.read(self.root)

    def test_supported_refs_preserved_optional_literal_build_co_bumped(self):
        for reference in source.REFERENCES:
            with self.subTest(reference=reference):
                copy_source(self.root)
                self.plist(CFBundleShortVersionString=reference, CFBundleVersion=reference)
                project = self.root / source.PROJECT
                project.write_text(source.SETTING.sub(
                    lambda m: f'{m[1]}"$(MARKETING_VERSION)"{m[4]}'
                    if m[2] == "CURRENT_PROJECT_VERSION" else m[0], project.read_text(),
                ))
                new = source.bump("patch", self.root)
                data = plistlib.loads((self.root / source.PLIST).read_bytes())
                self.assertEqual(data["CFBundleShortVersionString"], reference)
                self.assertEqual(data["CFBundleVersion"], reference)
                self.assertEqual(project.read_text().count('"$(MARKETING_VERSION)"'),
                                 source.SETTING_COUNT)
                self.assertEqual(source.read(self.root), new)
        copy_source(self.root)
        self.plist(CFBundleShortVersionString=self.current, CFBundleVersion=self.current)
        new = source.bump("minor", self.root)
        data = plistlib.loads((self.root / source.PLIST).read_bytes())
        self.assertEqual(data["CFBundleShortVersionString"], new)
        self.assertEqual(data["CFBundleVersion"], new)

    def test_localized_missing_duplicate_invalid_and_drifted(self):
        path = self.root / source.LOCALIZED
        original = source.text(path)
        for malformed in (
            original.replace(f'CFBundleShortVersionString = "{self.current}"',
                             'CFBundleShortVersionString = "1.0"'),
            original.replace(f"Trash version {self.current},", f"Trash version {self.drift},"),
            original + f'\nCFBundleShortVersionString = "{self.current}";\n',
            original.replace("CFBundleShortVersionString", "UnknownKey"),
            original.replace("CFBundleGetInfoString", "UnknownKey"),
        ):
            with self.subTest(malformed=malformed):
                path.write_text(malformed, encoding="utf-8-sig")
                with self.assertRaises(ValueError):
                    source.read(self.root)

    def test_all_metadata_preserves_bom_endings_modes_and_unrelated_bytes(self):
        for newline in ("\n", "\r", "\r\n", "mixed"):
            for bom in (b"", b"\xef\xbb\xbf"):
                with self.subTest(newline=newline, bom=bom):
                    copy_source(self.root)
                    originals = {}
                    for path in source.SOURCE_PATHS:
                        target = self.root / path
                        lines = source.text(target).splitlines(keepends=True)
                        value = "".join(
                            line.replace("\n", ("\r", "\r\n", "\n")[i % 3]
                                         if newline == "mixed" else newline)
                            for i, line in enumerate(lines)
                        )
                        target.write_bytes(bom + value.encode())
                        target.chmod(0o755)
                        originals[path] = target.read_bytes()
                    new = source.bump("minor", self.root)
                    for path, original in originals.items():
                        self.assertEqual((self.root / path).read_bytes(),
                                         original.replace(self.current.encode(), new.encode()))
                        self.assertEqual((self.root / path).stat().st_mode & 0o777, 0o755)

    def test_quoted_and_whitespace_settings_preserved(self):
        path = self.root / source.PROJECT
        original = path.read_bytes()
        for quoted in (False, True):
            copy_source(self.root)
            old = self.current.encode()
            value = b'"' + old + b'"' if quoted else old
            before = original
            for key in source.KEYS:
                before = before.replace(key.encode() + b" = " + old + b";",
                                        key.encode() + b" = \t" + value + b" \t;")
            path.write_bytes(before)
            new = source.bump("patch", self.root)
            self.assertEqual(path.read_bytes(), before.replace(old, new.encode()))

    def test_prebuilt_4d_resources_and_windows_inputs_unchanged(self):
        excluded = (
            "test/Plugins/Trash.bundle/Contents/Info.plist",
            "test/Plugins/Trash.bundle/Contents/Resources/English.lproj/InfoPlist.strings",
            "test/Plugins/Trash.bundle/Contents/Resources/manifest.json",
            "test/Plugins/Trash.bundle/Contents/MacOS/Trash",
            "test/Plugins/Trash.bundle/Contents/Windows/Trash.4DX",
            "test/Plugins/Trash.bundle/Contents/Windows64/Trash.4DX",
            "test/Data/data.4DD", "test/Project/Sources/catalog.4DCatalog",
            "constants.xlf", "manifest.json", "4DPlugin-Trash.h",
            "Trash.vcxproj", "Trash.vcxproj.filters", "Trash.sln",
        )
        for name in excluded:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((ROOT / name).read_bytes())
        before = {name: (self.root / name).read_bytes() for name in excluded}
        source.bump("major", self.root)
        self.assertEqual(before, {name: (self.root / name).read_bytes() for name in excluded})


class GitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix=".lifecycle-git-", dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.remote = self.root / "remote.git"
        self.local = self.root / "local"
        self.peer = self.root / "peer"
        git(self.root, "init", "--bare", str(self.remote))
        git(self.root, "init", "-b", "selected", str(self.local))
        for key, value in (("user.name", "Lifecycle Test"), ("user.email", "test@example.invalid"),
                           ("push.followTags", "true"), ("commit.gpgsign", "false")):
            git(self.local, "config", key, value)
        git(self.local, "commit", "--allow-empty", "-m", "ancestor")
        self.ancestor = git(self.local, "rev-parse", "HEAD")
        copy_source(self.local)
        shutil.copytree(ROOT / ".github/scripts", self.local / ".github/scripts",
                        ignore=shutil.ignore_patterns("__pycache__"))
        git(self.local, "add", ".")
        git(self.local, "commit", "-m", "fixture")
        git(self.local, "remote", "add", "origin", str(self.remote))
        git(self.local, "push", "origin", "HEAD:refs/heads/selected")
        self.sha = git(self.local, "rev-parse", "HEAD")
        self.version = source.read(self.local)
        self.tag = f"v{self.version}"
        git(self.root, "clone", "--branch", "selected", str(self.remote), str(self.peer))
        for key, value in (("user.name", "Peer"), ("user.email", "peer@example.invalid"),
                           ("commit.gpgsign", "false")):
            git(self.peer, "config", key, value)
        self.enter = cwd(self.local)
        self.enter.__enter__()
        self.addCleanup(self.enter.__exit__, None, None, None)
        self.start_patch(patch.object(release, "__file__", str(
            self.local / ".github/scripts/release.py")))
        self.start_patch(patch.object(release, "api", side_effect=AssertionError("API must be mocked")))

    def start_patch(self, patcher):
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def advance_peer(self):
        (self.peer / "peer.txt").write_text("concurrent branch update")
        git(self.peer, "add", ".")
        git(self.peer, "commit", "-m", "peer")
        git(self.peer, "push", "origin", "HEAD:refs/heads/selected")

    def test_bump_explicit_branch_nonempty_source_commit_and_no_follow_tags(self):
        git(self.local, "tag", "-a", "unrelated-annotated", "-m", "must not follow")
        git(self.remote, "update-ref", "refs/heads/other", self.sha)
        real_run = release.run
        with patch.object(release, "run", wraps=real_run) as run:
            release.bump_branch(self.sha, "selected", "patch")
        new = git(self.local, "rev-parse", "HEAD")
        self.assertEqual(git(self.remote, "rev-parse", "refs/heads/selected"), new)
        self.assertNotEqual(new, self.sha)
        self.assertEqual(git(self.remote, "rev-parse", "refs/heads/other"), self.sha)
        self.assertEqual(git(self.remote, "tag", "--list"), "")
        self.assertEqual(git(self.local, "rev-list", "--parents", "-n", "1", new),
                         f"{new} {self.sha}")
        self.assertEqual(git(self.local, "status", "--porcelain"), "")
        changed = set(git(self.local, "diff-tree", "--no-commit-id", "--name-only",
                          "-r", new).splitlines())
        self.assertTrue(changed)
        self.assertTrue(changed.issubset(set(map(str, source.SOURCE_PATHS))))
        self.assertIn("Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>",
                      git(self.local, "log", "-1", "--format=%B"))
        pushes = [call for call in run.call_args_list if "push" in call.args]
        self.assertEqual(len(pushes), 1)
        self.assertIn("push.followTags=false", pushes[0].args)
        self.assertIn("--no-follow-tags", pushes[0].args)
        self.assertEqual(pushes[0].args[-2:], ("origin", f"{new}:refs/heads/selected"))
        self.assertNotIn("--force", pushes[0].args)

    def test_cli_no_bytecode_keeps_checkout_clean(self):
        env = os.environ.copy()
        env.pop("PYTHONDONTWRITEBYTECODE", None)
        subprocess.run(
            [sys.executable, ".github/scripts/release.py", "bump", "--sha", self.sha,
             "--branch", "selected", "--mode", "patch"],
            env=env, check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.assertEqual(git(self.local, "status", "--porcelain"), "")
        self.assertEqual(git(self.remote, "tag", "--list"), "")

    def test_selected_branch_moved_before_preparation(self):
        self.advance_peer()
        with self.assertRaisesRegex(ValueError, "moved since"):
            release.bump_branch(self.sha, "selected", "patch")
        self.assertEqual(git(self.local, "rev-parse", "HEAD"), self.sha)

    def test_branch_moved_during_preparation(self):
        real_bump = release.bump

        def concurrent(mode):
            new = real_bump(mode)
            self.advance_peer()
            return new

        with patch.object(release, "bump", side_effect=concurrent):
            with self.assertRaisesRegex(ValueError, "moved during"):
                release.bump_branch(self.sha, "selected", "minor")
        self.assertEqual(release.remote_ref("refs/heads/selected"),
                         git(self.peer, "rev-parse", "HEAD"))

    def advertised_race(self, kind):
        real_run = release.run
        expected = None
        pushes = 0
        reads = 0

        def concurrent(*args, **kwargs):
            nonlocal expected, pushes, reads
            if args[:2] == ("git", "ls-remote"):
                reads += 1
            # Mutate only after the actual final remote read, at push advertisement.
            if "push" in args:
                self.assertEqual(reads, 2)
                pushes += 1
                if kind == "rewind":
                    expected = self.ancestor
                    git(self.remote, "update-ref", "refs/heads/selected", expected)
                elif kind == "delete":
                    git(self.remote, "update-ref", "-d", "refs/heads/selected")
                elif kind == "advance":
                    self.advance_peer()
                    expected = git(self.peer, "rev-parse", "HEAD")
                elif kind == "same-result":
                    expected = git(self.local, "rev-parse", "HEAD")
                    git(self.remote, "fetch", "--no-tags", str(self.local), "HEAD")
                    git(self.remote, "update-ref", "refs/heads/selected", expected)
                else:
                    self.fail(kind)
            return real_run(*args, **kwargs)

        with patch.object(release, "run", side_effect=concurrent):
            with self.assertRaises(subprocess.CalledProcessError):
                release.bump_branch(self.sha, "selected", "patch")
        self.assertEqual(pushes, 1)
        self.assertEqual(release.remote_ref("refs/heads/selected"), expected)
        self.assertEqual(git(self.remote, "tag", "--list"), "")

    def test_advertised_advance_after_final_read(self):
        self.advertised_race("advance")

    def test_advertised_rewind_after_final_read(self):
        self.advertised_race("rewind")

    def test_advertised_deletion_after_final_read(self):
        self.advertised_race("delete")

    def test_advertised_same_result_zero_updates_after_final_read(self):
        self.advertised_race("same-result")

    def test_initial_missing_and_nonexecutable_guard(self):
        guard = self.local / ".github/scripts/bump-hooks/pre-push"
        guard.chmod(0o644)
        for missing in (False, True):
            if missing:
                guard.unlink()
            with self.subTest(missing=missing), self.assertRaisesRegex(
                    ValueError, "guard is missing or not executable"):
                release.bump_branch(self.sha, "selected", "patch")
            self.assertEqual(git(self.local, "rev-parse", "HEAD"), self.sha)
            self.assertEqual(release.remote_ref("refs/heads/selected"), self.sha)

    def guard_loss_after_final_read(self, missing):
        runtime = self.root / "runtime"
        shutil.copytree(self.local / ".github/scripts", runtime)
        guard = runtime / "bump-hooks/pre-push"
        real_remote = release.remote_ref
        reads = 0

        def lose_guard(ref):
            nonlocal reads
            result = real_remote(ref)
            reads += 1
            if reads == 2:
                if missing:
                    guard.unlink()
                else:
                    guard.chmod(0o644)
            return result

        with patch.object(release, "__file__", str(runtime / "release.py")), \
                patch.object(release, "remote_ref", side_effect=lose_guard), \
                patch.object(release.os, "access", wraps=os.access) as access:
            with self.assertRaisesRegex(ValueError, "guard is missing or not executable"):
                release.bump_branch(self.sha, "selected", "patch")
            self.assertEqual(access.call_count, 1 if missing else 2)
        self.assertEqual(reads, 2)
        self.assertEqual(real_remote("refs/heads/selected"), self.sha)

    def test_late_missing_guard(self):
        self.guard_loss_after_final_read(True)

    def test_late_nonexecutable_guard(self):
        self.guard_loss_after_final_read(False)

    def test_dirty_checkout_invalid_sha_and_invalid_branch(self):
        for sha in ("selected", "a" * 40, "a" * 39, "a" * 41):
            with self.subTest(sha=sha), self.assertRaises(ValueError):
                release.selected_sha(sha)
        with self.assertRaises(subprocess.CalledProcessError):
            release.bump_branch(self.sha, "invalid..branch", "patch")
        (self.local / "dirty.txt").write_text("dirty")
        with self.assertRaisesRegex(ValueError, "clean checkout"):
            release.bump_branch(self.sha, "selected", "patch")
        self.assertEqual(git(self.local, "rev-parse", "HEAD"), self.sha)

    def test_bump_rejects_multiple_parent_commit(self):
        real_run = release.run

        def merge_after_commit(*args, **kwargs):
            result = real_run(*args, **kwargs)
            if args[:2] == ("git", "commit"):
                new = self.merge_commit()
                git(self.local, "update-ref", "HEAD", new)
            return result

        with patch.object(release, "run", side_effect=merge_after_commit) as run:
            with self.assertRaisesRegex(ValueError, "single-parent direct child"):
                release.bump_branch(self.sha, "selected", "patch")
            self.assertFalse(any("push" in call.args for call in run.call_args_list))
        self.assertEqual(release.remote_ref("refs/heads/selected"), self.sha)

    def merge_commit(self):
        return subprocess.run(
            ["git", "commit-tree", git(self.local, "rev-parse", "HEAD^{tree}"),
             "-p", self.sha, "-p", self.ancestor],
            input="merge fixture\n", check=True, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        ).stdout.strip()

    def test_non_source_commit_rejected_before_push(self):
        real_run = release.run

        def extra_change(*args, **kwargs):
            if args[:2] == ("git", "commit"):
                (self.local / "unrelated.txt").write_text("not metadata")
                git(self.local, "add", "unrelated.txt")
            return real_run(*args, **kwargs)

        with patch.object(release, "run", side_effect=extra_change) as run:
            with self.assertRaisesRegex(ValueError, "source version metadata only"):
                release.bump_branch(self.sha, "selected", "patch")
            self.assertFalse(any("push" in call.args for call in run.call_args_list))
        self.assertEqual(release.remote_ref("refs/heads/selected"), self.sha)

    def test_empty_commit_rejected_before_push(self):
        real_run = release.run

        def empty_commit(*args, **kwargs):
            if args[:2] == ("git", "commit"):
                git(self.local, "reset", "--hard", self.sha)
                git(self.local, "commit", "--allow-empty", "-m", "empty")
                return ""
            return real_run(*args, **kwargs)

        with patch.object(release, "run", side_effect=empty_commit):
            with self.assertRaisesRegex(ValueError, "source version metadata only"):
                release.bump_branch(self.sha, "selected", "patch")
        self.assertEqual(release.remote_ref("refs/heads/selected"), self.sha)

    def test_push_zero_or_multiple_or_wrong_updates_not_success(self):
        real_run = release.run

        def fake_push(*args, **kwargs):
            if "push" in args:
                commit = git(self.local, "rev-parse", "HEAD")
                row = f" \t{commit}:refs/heads/selected\told..new"
                if self.result == "zero":
                    return "To origin\nDone"
                if self.result == "same":
                    return f"To origin\n=\t{commit}:refs/heads/selected\t[up to date]\nDone"
                if self.result == "multiple":
                    return f"To origin\n{row}\n{row}\nDone"
                return f"To origin\n \tHEAD:refs/heads/selected\told..new\nDone"
            return real_run(*args, **kwargs)

        for result in ("zero", "same", "multiple", "wrong"):
            with self.subTest(result=result):
                self.result = result
                git(self.local, "reset", "--hard", self.sha)
                with patch.object(release, "run", side_effect=fake_push):
                    with self.assertRaisesRegex(ValueError, "exactly one new update"):
                        release.bump_branch(self.sha, "selected", "patch")
                self.assertEqual(release.remote_ref("refs/heads/selected"), self.sha)

    def test_postpush_remote_verification_failure_retains_commit(self):
        real_remote = release.remote_ref
        reads = 0

        def wrong_verification(ref):
            nonlocal reads
            result = real_remote(ref)
            reads += 1
            return self.sha if reads == 3 else result

        with patch.object(release, "remote_ref", side_effect=wrong_verification):
            with self.assertRaisesRegex(ValueError, "Pushed branch failed exact-SHA"):
                release.bump_branch(self.sha, "selected", "patch")
        self.assertEqual(reads, 3)
        self.assertEqual(real_remote("refs/heads/selected"), git(self.local, "rev-parse", "HEAD"))

    def test_guard_exact_advertisement_literal_sha_head_and_one_update(self):
        guard = self.local / ".github/scripts/bump-hooks/pre-push"
        git(self.local, "commit", "--allow-empty", "-m", "direct child")
        new = git(self.local, "rev-parse", "HEAD")
        env = {**os.environ, "BUMP_EXPECTED_SHA": self.sha,
               "BUMP_EXPECTED_REF": "refs/heads/selected", "BUMP_NEW_SHA": new}
        valid = f"{new} {new} refs/heads/selected {self.sha}\n"
        for row, accepted in (
            (valid, True),
            (f"{new} {new} refs/heads/wrong {self.sha}\n", False),
            (f"HEAD {new} refs/heads/selected {self.sha}\n", False),
            (f"{new} {self.ancestor} refs/heads/selected {self.sha}\n", False),
            (f"{new} {new} refs/heads/selected {self.ancestor}\n", False),
            (f"{new} {new} refs/heads/selected {'0' * 40}\n", False),
            (valid * 2, False), ("", False),
        ):
            with self.subTest(row=row):
                result = subprocess.run(
                    [str(guard)], input=row, env=env, text=True,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                )
                self.assertEqual(result.returncode == 0, accepted, result.stderr)
        result = subprocess.run([str(guard)], input=valid, env={}, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertNotEqual(result.returncode, 0)
        git(self.local, "update-ref", "HEAD", self.sha)
        result = subprocess.run([str(guard)], input=valid, env=env, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertNotEqual(result.returncode, 0)

    def test_guard_and_actual_push_reject_multiple_parents_selected_first(self):
        guard = self.local / ".github/scripts/bump-hooks/pre-push"
        new = self.merge_commit()
        git(self.local, "update-ref", "HEAD", new)
        env = {**os.environ, "BUMP_EXPECTED_SHA": self.sha,
               "BUMP_EXPECTED_REF": "refs/heads/selected", "BUMP_NEW_SHA": new}
        for command, input in (
            ([str(guard)], f"{new} {new} refs/heads/selected {self.sha}\n"),
            (["git", "-c", f"core.hooksPath={guard.parent}", "-c", "push.followTags=false",
              "push", "--porcelain", "--no-follow-tags", "origin",
              f"{new}:refs/heads/selected"], None),
        ):
            result = subprocess.run(command, input=input, env=env, text=True,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertNotEqual(result.returncode, 0)
        self.assertEqual(release.remote_ref("refs/heads/selected"), self.sha)

    def assets(self):
        paths = [Path(f"Trash-{self.version}.{ext}") for ext in ("zip", "dmg")]
        for asset in paths:
            asset.write_bytes(b"fixture asset")
        return paths

    def test_preflight_selected_clean_source_and_final_drift(self):
        with patch.dict(os.environ, GITHUB_REPOSITORY="owner/repo"), \
                patch.object(release, "api", return_value=[]) as api:
            self.assertEqual(release.preflight(self.sha), (self.version, self.tag))
            api.assert_called_once_with("repos/owner/repo/releases?per_page=100&page=1")
        (self.local / source.PLIST).write_text("dirty")
        with self.assertRaisesRegex(ValueError, "clean source"):
            release.preflight(self.sha)
        copy_source(self.local)
        assets = self.assets()
        source.bump("patch")
        with self.assertRaisesRegex(ValueError, "differs from the selected commit"):
            release.publish(self.sha, assets)
        self.assertEqual(git(self.remote, "tag", "--list"), "")

    def publication_api(self, calls, race=None, fail=None):
        def api(endpoint, payload=None, asset=None):
            calls.append((endpoint, payload, asset))
            if "/releases?" in endpoint:
                return []
            if endpoint.endswith("/git/refs"):
                if race:
                    git(self.remote, "update-ref", payload["ref"], race)
                git(self.remote, "update-ref", payload["ref"], payload["sha"], "0" * 40)
                return {"ref": payload["ref"], "object": {"sha": payload["sha"]}}
            if endpoint.endswith("/releases"):
                if fail == "release":
                    raise subprocess.CalledProcessError(1, ["stub", "release"])
                self.published = {"id": 123, **payload}
                return self.published
            if asset is not None:
                if fail == asset.suffix:
                    raise subprocess.CalledProcessError(1, ["stub", "upload"])
                self.uploaded.append(asset.name)
                return {"id": 456, "name": asset.name, "size": asset.stat().st_size,
                        "state": "uploaded"}
            raise AssertionError(endpoint)
        return api

    def test_bare_origin_publication_exact_tag_new_release_and_separate_assets(self):
        calls = []
        self.uploaded = []
        with patch.dict(os.environ, GITHUB_REPOSITORY="owner/repo"), \
                patch.object(release, "api", side_effect=self.publication_api(calls)):
            release.publish(self.sha, self.assets())
        self.assertEqual(git(self.remote, "rev-parse", f"refs/tags/{self.tag}"), self.sha)
        self.assertEqual(calls[2][1], {
            "tag_name": self.tag, "target_commitish": self.sha, "name": self.tag,
            "draft": False, "prerelease": False, "body": "",
        })
        self.assertEqual(len(calls), 5)
        self.assertEqual(self.uploaded, [f"Trash-{self.version}.zip", f"Trash-{self.version}.dmg"])
        self.assertTrue(all("/releases/123/assets?name=" in c[0] for c in calls[3:]))

    def test_bare_origin_same_and_different_sha_creation_races(self):
        for raced_sha in (self.sha, self.ancestor):
            with self.subTest(raced_sha=raced_sha):
                calls = []
                with patch.dict(os.environ, GITHUB_REPOSITORY="owner/repo"), \
                        patch.object(release, "api", side_effect=self.publication_api(
                            calls, race=raced_sha)):
                    with self.assertRaises(subprocess.CalledProcessError):
                        release.publish(self.sha, self.assets())
                self.assertEqual(len(calls), 2)
                self.assertEqual(git(self.remote, "rev-parse", f"refs/tags/{self.tag}"), raced_sha)
                git(self.remote, "update-ref", "-d", f"refs/tags/{self.tag}")

    def test_bare_origin_partial_publication_is_retained(self):
        for failure, count, published, uploaded in (
            ("release", 3, False, []), (".zip", 4, True, []),
            (".dmg", 5, True, [f"Trash-{self.version}.zip"]),
        ):
            with self.subTest(failure=failure):
                calls = []
                self.published, self.uploaded = None, []
                with patch.dict(os.environ, GITHUB_REPOSITORY="owner/repo"), \
                        patch.object(release, "api", side_effect=self.publication_api(
                            calls, fail=failure)):
                    with self.assertRaises(subprocess.CalledProcessError):
                        release.publish(self.sha, self.assets())
                self.assertEqual(len(calls), count)
                self.assertEqual(git(self.remote, "rev-parse", f"refs/tags/{self.tag}"), self.sha)
                self.assertEqual(self.published is not None, published)
                self.assertEqual(self.uploaded, uploaded)
                git(self.remote, "update-ref", "-d", f"refs/tags/{self.tag}")


if __name__ == "__main__":
    unittest.main()
