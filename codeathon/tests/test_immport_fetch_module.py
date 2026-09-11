import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).parents[1] / "data" / "immport_fetch_module.py"
SPEC = importlib.util.spec_from_file_location("immport_fetch_module", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()


class ImmportFetchModuleTests(unittest.TestCase):
    def test_api_key_file_is_loaded(self):
        with tempfile.TemporaryDirectory() as tempdir:
            key_path = Path(tempdir) / "key.json"
            key_path.write_text(json.dumps({"api_key": "test-key"}), encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                session = MODULE.ImmportSession(api_key_file=str(key_path))

        self.assertEqual(session.auth_header(), {"Authorization": "Bearer test-key"})

    def test_missing_api_key_is_rejected(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(MODULE.ImmportAuthError):
                MODULE.ImmportSession()

    def test_study_manifest_uses_current_endpoint_and_deduplicates(self):
        payload = [
            {"fileUUID": "uuid-1", "path": "SDY1/StudyFiles/a.txt"},
            {"fileUUID": "uuid-1", "path": "SDY1/StudyFiles/a.txt"},
            {"fileUUID": "uuid-2", "path": "SDY1/ResultFiles/b.txt"},
        ]
        session = MODULE.ImmportSession(api_key="test-key")
        with patch.object(
            MODULE,
            "_authed_get",
            return_value=FakeResponse(json.dumps(payload).encode()),
        ) as authed_get:
            result = MODULE._fetch_filepath_manifest("SDY1", session)

        self.assertEqual(len(result), 2)
        self.assertEqual(
            authed_get.call_args.args[0],
            "https://www.immport.org/data/query/api/study/manifest/SDY1"
            "?fileType=all&format=json",
        )

    def test_download_resolves_drs_url_and_reuses_valid_cache(self):
        file_payload = b"test file contents"
        resolver_payload = json.dumps({"url": "https://download.example.test/file"}).encode()
        session = MODULE.ImmportSession(api_key="test-key")

        with tempfile.TemporaryDirectory() as tempdir:
            destination = Path(tempdir) / "a.txt"
            with (
                patch.object(
                    MODULE,
                    "_authed_get",
                    return_value=FakeResponse(resolver_payload),
                ) as authed_get,
                patch.object(
                    MODULE,
                    "urlopen",
                    return_value=FakeResponse(file_payload),
                ) as open_url,
            ):
                downloaded = MODULE._download_file(
                    "SDY1/StudyFiles/a.txt",
                    "uuid-1",
                    destination,
                    session,
                    force=False,
                    artifact_type="study_file",
                    expected_size=len(file_payload),
                )
                cached = MODULE._download_file(
                    "SDY1/StudyFiles/a.txt",
                    "uuid-1",
                    destination,
                    session,
                    force=False,
                    artifact_type="study_file",
                    expected_size=len(file_payload),
                )

        self.assertEqual(downloaded.status, "success")
        self.assertEqual(cached.status, "cached")
        self.assertEqual(downloaded.sha256, cached.sha256)
        self.assertEqual(open_url.call_count, 1)
        self.assertEqual(
            authed_get.call_args.args[0],
            "https://www.immport.org/data/query/drs/download/s3/uuid-1",
        )

    def test_capped_fetch_is_reported_as_partial(self):
        entries = [
            {
                "fileUUID": "uuid-1",
                "path": "SDY1/StudyFiles/a.txt",
                "fileType": "study_file",
                "filesizeBytes": 1,
            },
            {
                "fileUUID": "uuid-2",
                "path": "SDY1/StudyFiles/b.txt",
                "fileType": "study_file",
                "filesizeBytes": 1,
            },
        ]
        session = MODULE.ImmportSession(api_key="test-key")
        downloaded = MODULE.ArtifactRecord(
            artifact_type="study_file",
            status="success",
            source_path=entries[0]["path"],
            local_file="a.txt",
            retrieval_tool="urllib.request",
            retrieval_tool_version="test",
            size_bytes=1,
        )

        with tempfile.TemporaryDirectory() as tempdir:
            with (
                patch.object(MODULE, "_fetch_filepath_manifest", return_value=entries),
                patch.object(MODULE, "_download_file", return_value=downloaded),
            ):
                record = MODULE.fetch_one(
                    "SDY1", Path(tempdir), session, max_files=1
                )

        self.assertEqual(record.status, "partial")
        self.assertEqual(record.n_files_listed, 2)
        self.assertEqual(record.n_files_downloaded, 1)


if __name__ == "__main__":
    unittest.main()
