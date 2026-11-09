"""Offline publication contract tests; no real Actions or API publication."""

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import call, patch
from uuid import uuid4


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".github/scripts"))
SPEC = importlib.util.spec_from_file_location("publication_release", ROOT / ".github/scripts/release.py")
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)
SHA = "a" * 40
VERSION = release.read(ROOT)
TAG = f"v{VERSION}"
REPO = "owner/repo"


def entry(tag="unrelated-release", draft=False):
    return {"id": 9, "tag_name": tag, "draft": draft, "prerelease": False}


class AvailabilityTests(unittest.TestCase):
    def test_exact_tag_on_page_two_including_drafts(self):
        for draft in (True, False):
            with self.subTest(draft=draft), \
                    patch.object(release, "remote_ref", return_value=None), \
                    patch.object(release, "api", side_effect=[
                        [entry()] * 100, [entry(TAG, draft)],
                    ]) as api:
                with self.assertRaisesRegex(ValueError, "draft or published"):
                    release.available(REPO, TAG)
                self.assertEqual(api.call_args_list, [
                    call(f"repos/{REPO}/releases?per_page=100&page=1"),
                    call(f"repos/{REPO}/releases?per_page=100&page=2"),
                ])

    def test_absence_requires_all_pages_and_does_not_match_tag_prefix(self):
        with patch.object(release, "remote_ref", return_value=None), \
                patch.object(release, "api", side_effect=[
                    [entry(TAG + "-other")] * 100, [entry()] * 100, [],
                ]) as api:
            release.available(REPO, TAG)
            self.assertEqual(api.call_count, 3)

    def test_api_auth_404_network_and_malformed_every_page_fail_closed(self):
        errors = [
            *(subprocess.CalledProcessError(1, ["stub", str(code)])
              for code in (401, 403, 404, 422, 500)),
            OSError("network"), json.JSONDecodeError("bad", "?", 0),
        ]
        malformed = [
            {}, None, "[]", [None], [{}], [dict(entry(), id=True)],
            [dict(entry(), id=0)], [dict(entry(), id=-1)],
            [dict(entry(), tag_name=None)], [dict(entry(), tag_name="")],
            [dict(entry(), draft=0)], [dict(entry(), prerelease="false")],
            [entry()] * 101,
        ]
        for page in (1, 2, 3):
            for response in errors + malformed:
                with self.subTest(page=page, response=response), \
                        patch.object(release, "remote_ref", return_value=None), \
                        patch.object(release, "api", side_effect=(
                            [[entry()] * 100] * (page - 1) + [response]
                        )) as api:
                    with self.assertRaises((ValueError, OSError, subprocess.CalledProcessError)):
                        release.available(REPO, TAG)
                    self.assertEqual(api.call_count, page)

    def test_remote_exact_tag_errors_and_malformed_fail_closed(self):
        ref = f"refs/tags/{TAG}"
        with patch.object(release, "run", return_value=f"{SHA}\t{ref}") as run:
            self.assertEqual(release.remote_ref(ref), SHA)
            run.assert_called_once_with("git", "ls-remote", "--refs", "origin", ref)
        with patch.object(release, "run", return_value=""):
            self.assertIsNone(release.remote_ref(ref))
        for output in ("invalid", f"{SHA}\t{ref}-other", f"bad\t{ref}",
                       f"{SHA}\t{ref}\n{SHA}\t{ref}"):
            with self.subTest(output=output), patch.object(release, "run", return_value=output):
                with self.assertRaises(ValueError):
                    release.remote_ref(ref)
        with patch.object(release, "remote_ref", return_value=SHA), \
                patch.object(release, "api") as api:
            with self.assertRaisesRegex(ValueError, "already exists"):
                release.available(REPO, TAG)
            api.assert_not_called()
        for error in (OSError("network"), subprocess.CalledProcessError(1, ["stub", "git"])):
            with patch.object(release, "run", side_effect=error), \
                    patch.object(release, "api") as api:
                with self.assertRaises(type(error)):
                    release.available(REPO, TAG)
                api.assert_not_called()

    def test_transport_explicit_get_post_json_and_separate_asset_no_retry(self):
        with patch.object(release, "run", return_value="[]") as run:
            release.api("repos/o/r/releases?per_page=100&page=2")
            run.assert_called_once_with(
                "gh", "api", "--method", "GET",
                "repos/o/r/releases?per_page=100&page=2", input=None,
            )
            payload = {"ref": f"refs/tags/{TAG}", "sha": SHA}
            release.api("repos/o/r/git/refs", payload)
            self.assertEqual(run.call_args.args,
                             ("gh", "api", "--method", "POST", "repos/o/r/git/refs",
                              "--input", "-"))
            self.assertEqual(json.loads(run.call_args.kwargs["input"]), payload)
            release.api("https://uploads.github.com/repos/o/r/releases/123/assets",
                        asset=Path("asset.zip"))
            self.assertEqual(run.call_args.args, (
                "gh", "api", "--method", "POST",
                "https://uploads.github.com/repos/o/r/releases/123/assets",
                "--header", "Content-Type: application/octet-stream", "--input", "asset.zip",
            ))
        for error in (subprocess.CalledProcessError(1, ["stub", "gh"]), OSError("network")):
            with patch.object(release, "run", side_effect=error) as run:
                with self.assertRaises(type(error)):
                    release.api("repos/o/r/releases")
                self.assertEqual(run.call_count, 1)
        with patch.object(release, "run", return_value="not json"):
            with self.assertRaises(json.JSONDecodeError):
                release.api("repos/o/r/releases")

    def test_context_selected_checkout_source_and_repository_validation(self):
        with patch.dict(os.environ, GITHUB_REPOSITORY=REPO), \
                patch.object(release, "run", side_effect=[SHA, ""]), \
                patch.object(release, "read", return_value=VERSION):
            self.assertEqual(release.context(SHA), (REPO, VERSION, TAG))
        for sha in ("main", SHA.upper(), "a" * 39):
            with self.subTest(sha=sha), self.assertRaises(ValueError):
                release.selected_sha(sha)
        for repo in ("owner", "owner/../repo", "owner/repo?x"):
            with patch.dict(os.environ, GITHUB_REPOSITORY=repo), \
                    patch.object(release, "run", side_effect=[SHA, ""]), \
                    patch.object(release, "read", return_value=VERSION), \
                    self.assertRaises(ValueError):
                release.context(SHA)
        with patch.object(release, "run", return_value="b" * 40):
            with self.assertRaisesRegex(ValueError, "Checkout"):
                release.context(SHA)
        with patch.object(release, "run", side_effect=[SHA, "Info.plist"]):
            with self.assertRaisesRegex(ValueError, "metadata differs"):
                release.context(SHA)


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.directory = ROOT / ".github/tests" / (".release-fixture-" + uuid4().hex)
        self.directory.mkdir()
        self.addCleanup(shutil.rmtree, self.directory)
        previous = Path.cwd()
        os.chdir(self.directory)
        self.addCleanup(os.chdir, previous)
        self.assets = [Path(f"Trash-{VERSION}.{ext}") for ext in ("zip", "dmg")]
        for asset in self.assets:
            asset.write_bytes(b"fixture asset")
        self.calls = []
        self.tag = None
        self.published = None
        self.uploaded = []
        self.fail = None
        self.race = None
        self.bad_kind = None
        self.bad_result = None
        self.pages = [[]]
        self.start_patch(patch.object(release, "context", return_value=(REPO, VERSION, TAG)))
        self.start_patch(patch.object(release, "run", return_value=""))
        self.start_patch(patch.object(release, "remote_ref", side_effect=lambda ref: self.tag))
        self.start_patch(patch.object(release, "api", side_effect=self.api))
        self.start_patch(patch("sys.stdout", new_callable=io.StringIO))

    def start_patch(self, patcher):
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def api(self, endpoint, payload=None, asset=None):
        self.calls.append((endpoint, payload, asset))
        if "/releases?" in endpoint:
            page = int(endpoint.rsplit("=", 1)[1])
            result = self.pages[page - 1]
            if isinstance(result, Exception):
                raise result
            return result
        if endpoint.endswith("/git/refs"):
            kind = "tag"
            if self.race:
                self.tag = self.race
            if self.tag is not None or self.fail == kind:
                raise subprocess.CalledProcessError(1, ["stub", "422"])
            self.tag = payload["sha"]
            result = {"ref": payload["ref"], "object": {"sha": self.tag, "type": "commit"}}
        elif endpoint.endswith("/releases"):
            kind = "release"
            if self.fail == kind:
                raise subprocess.CalledProcessError(1, ["stub", "release"])
            self.published = {"id": 123, **payload}
            result = self.published
        elif asset is not None:
            kind = asset.suffix
            if self.fail == kind:
                raise subprocess.CalledProcessError(1, ["stub", "upload"])
            self.uploaded.append(asset.name)
            result = {"id": 456, "name": asset.name, "size": asset.stat().st_size,
                      "state": "uploaded"}
        else:
            raise AssertionError(endpoint)
        return self.bad_result if self.bad_kind == kind else result

    def publish(self):
        release.publish(SHA, self.assets)

    def test_new_tag_then_new_public_release_then_assets_to_returned_id(self):
        self.publish()
        self.assertEqual(self.tag, SHA)
        self.assertEqual(len(self.calls), 5)
        self.assertEqual(self.calls[1], (
            f"repos/{REPO}/git/refs", {"ref": f"refs/tags/{TAG}", "sha": SHA}, None,
        ))
        self.assertEqual(self.calls[2], (f"repos/{REPO}/releases", {
            "tag_name": TAG, "target_commitish": SHA, "name": TAG,
            "draft": False, "prerelease": False, "body": "",
        }, None))
        for endpoint, payload, asset in self.calls[3:]:
            self.assertTrue(endpoint.startswith(
                f"https://uploads.github.com/repos/{REPO}/releases/123/assets?name="))
            self.assertNotIn(" ", endpoint)
            self.assertNotIn("#", endpoint)
            self.assertIsNone(payload)
            self.assertIn(asset, self.assets)
        self.assertEqual(self.uploaded, [asset.name for asset in self.assets])

    def test_same_and_different_sha_tag_races_no_reuse(self):
        for sha in (SHA, "b" * 40):
            with self.subTest(sha=sha):
                self.calls.clear()
                self.tag = None
                self.race = sha
                with self.assertRaises(subprocess.CalledProcessError):
                    self.publish()
                self.assertEqual(self.tag, sha)
                self.assertEqual(len(self.calls), 2)
                self.assertIsNone(self.published)
                self.assertEqual(self.uploaded, [])

    def test_each_post_failure_retains_prior_state_without_retry_or_rollback(self):
        for kind, count, tag, published, uploaded in (
            ("tag", 2, None, False, []), ("release", 3, SHA, False, []),
            (".zip", 4, SHA, True, []), (".dmg", 5, SHA, True, [self.assets[0].name]),
        ):
            with self.subTest(kind=kind):
                self.calls.clear()
                self.tag, self.published, self.uploaded = None, None, []
                self.fail = kind
                with self.assertRaises(subprocess.CalledProcessError):
                    self.publish()
                self.assertEqual(len(self.calls), count)
                self.assertEqual(self.tag, tag)
                self.assertEqual(self.published is not None, published)
                self.assertEqual(self.uploaded, uploaded)
                self.assertFalse(any("DELETE" in c[0] or "PATCH" in c[0] for c in self.calls))

    def test_final_recheck_after_preflight_new_draft_and_later_page_failure(self):
        for result in ([entry(TAG, True)], {"message": "bad"},
                       subprocess.CalledProcessError(1, ["stub", "404"]),
                       OSError("network"), json.JSONDecodeError("bad", "?", 0)):
            with self.subTest(result=result):
                self.calls.clear()
                self.pages = [[]]
                release.preflight(SHA)
                self.calls.clear()
                self.pages = [[entry()] * 100, result]
                with self.assertRaises((ValueError, OSError, subprocess.CalledProcessError)):
                    self.publish()
                self.assertEqual(len(self.calls), 2)
                self.assertIsNone(self.tag)
                self.assertIsNone(self.published)

    def test_final_recheck_after_preflight_new_remote_tag(self):
        release.preflight(SHA)
        self.calls.clear()
        self.tag = SHA
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.publish()
        self.assertEqual(self.calls, [])

    def test_remote_verification_failure_never_creates_release(self):
        for response in (None, "b" * 40, OSError("network"), ValueError("malformed")):
            with self.subTest(response=response):
                self.tag = None
                self.calls.clear()
                with patch.object(release, "remote_ref", side_effect=[None, response]):
                    with self.assertRaises((ValueError, OSError)):
                        self.publish()
                self.assertEqual(self.tag, SHA)
                self.assertEqual(len(self.calls), 2)
                self.assertIsNone(self.published)

    def test_malformed_mutation_responses_stop_retaining_created_state(self):
        tag_response = {"ref": f"refs/tags/{TAG}", "object": {"sha": SHA}}
        release_response = {"id": 123, "tag_name": TAG, "target_commitish": SHA,
                            "name": TAG, "draft": False, "prerelease": False, "body": ""}
        asset_response = {"id": 456, "name": self.assets[0].name, "size": 13, "state": "uploaded"}
        for kind, responses, count in (
            ("tag", [None, {}, dict(tag_response, ref="refs/tags/wrong"),
                     dict(tag_response, object=None),
                     dict(tag_response, object={"sha": "b" * 40})], 2),
            ("release", [None, {}, dict(release_response, id=True),
                         dict(release_response, id=0), dict(release_response, id=-1),
                         dict(release_response, draft=True), dict(release_response, draft=0),
                         dict(release_response, prerelease=True),
                         dict(release_response, tag_name=TAG + "-wrong")], 3),
            (".zip", [None, {}, dict(asset_response, id=True), dict(asset_response, id=0),
                      dict(asset_response, size=True), dict(asset_response, size=1),
                      dict(asset_response, name="wrong"), dict(asset_response, state="new")], 4),
        ):
            for response in responses:
                with self.subTest(kind=kind, response=response):
                    self.tag, self.published, self.uploaded = None, None, []
                    self.calls.clear()
                    self.bad_kind, self.bad_result = kind, response
                    with self.assertRaises(ValueError):
                        self.publish()
                    self.assertEqual(len(self.calls), count)
                    self.assertEqual(self.tag, SHA)
                    self.assertEqual(self.published is not None, kind != "tag")
                    self.assertEqual(self.uploaded, [self.assets[0].name] if kind == ".zip" else [])

    def test_missing_empty_wrong_duplicate_assets_fail_before_remote_operations(self):
        for assets in ([], self.assets[::-1], [self.assets[0], self.assets[0]],
                       [self.directory / "missing.zip"], [self.directory]):
            with self.subTest(assets=assets), patch.object(release, "remote_ref") as remote:
                with self.assertRaises(ValueError):
                    release.publish(SHA, assets)
                remote.assert_not_called()
                self.assertEqual(self.calls, [])
        self.assets[0].unlink()
        with self.assertRaises(ValueError):
            self.publish()
        self.assets[0].write_bytes(b"")
        with self.assertRaises(ValueError):
            self.publish()
        self.assertEqual(self.calls, [])

    def test_boolean_upload_size_rejected_even_for_one_byte_asset(self):
        self.assets[0].write_bytes(b"x")
        self.bad_kind = ".zip"
        self.bad_result = {"id": 456, "name": self.assets[0].name,
                           "size": True, "state": "uploaded"}
        with self.assertRaisesRegex(ValueError, "Malformed upload response"):
            self.publish()
        self.assertEqual(len(self.calls), 4)
        self.assertEqual(self.tag, SHA)
        self.assertIsNotNone(self.published)
        self.assertEqual(self.uploaded, [self.assets[0].name])


class CLITests(unittest.TestCase):
    def test_preflight_check_publish_and_bump_interfaces(self):
        for command in ("preflight", "check"):
            with self.subTest(command=command), \
                    patch.object(release, "preflight", return_value=(VERSION, TAG)) as preflight, \
                    patch.dict(os.environ, GITHUB_OUTPUT="fixture-output"), \
                    patch("builtins.open", unittest.mock.mock_open()) as output:
                release.main([command, "--sha", SHA])
                preflight.assert_called_once_with(SHA)
                output.assert_called_once_with("fixture-output", "a")
                output().write.assert_called_once_with(f"version={VERSION}\ntag={TAG}\n")
        with patch.object(release, "publish") as publish:
            release.main(["publish", "--sha", SHA, "first.zip", "second.dmg"])
            publish.assert_called_once_with(SHA, [Path("first.zip"), Path("second.dmg")])
        with patch.object(release, "bump_branch") as bump:
            release.main(["bump", "--sha", SHA, "--branch", "selected", "--mode", "patch"])
            bump.assert_called_once_with(SHA, "selected", "patch")
        for args in (["bump", "--sha", SHA], ["bump", "--sha", SHA, "--branch", "selected"]):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    release.main(args)

    def test_explicit_error_retention_message_and_nonzero_status(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / ".github/scripts/release.py"),
             "publish", "--sha", "invalid"],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("Stopped without rollback/retry", result.stderr)
        self.assertIn("remote tag, release and uploaded assets are retained", result.stderr)

    def test_no_destructive_or_combined_publication_commands(self):
        helper = (ROOT / ".github/scripts/release.py").read_text()
        for prohibited in ('"DELETE"', '"PATCH"', "--clobber", "--force",
                           '"gh", "release", "create"', '"git", "tag"'):
            self.assertNotIn(prohibited, helper)
        self.assertIn('"push.followTags=false"', helper)
        self.assertIn('"--no-follow-tags"', helper)
        self.assertEqual(helper.count("not os.access(guard, os.X_OK)"), 2)


if __name__ == "__main__":
    unittest.main()
