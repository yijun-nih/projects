import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError
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

    def test_resolver_http_error_is_labeled_redacted_and_cleans_partial_file(self):
        session = MODULE.ImmportSession(api_key="test-key")
        error = HTTPError(
            "https://resolver.example.test",
            403,
            "Forbidden",
            {},
            FakeResponse(b'{"message":"key=test-key"}'),
        )

        with tempfile.TemporaryDirectory() as tempdir:
            destination = Path(tempdir) / "a.txt"
            partial = destination.with_name("a.txt.part")
            partial.write_bytes(b"stale")
            with patch.object(MODULE, "_authed_get", side_effect=error):
                artifact = MODULE._download_file(
                    "SDY1/StudyFiles/a.txt",
                    "uuid-1",
                    destination,
                    session,
                    force=False,
                    artifact_type="study_file",
                )

            self.assertFalse(partial.exists())

        self.assertEqual(artifact.status, "failed")
        self.assertIn("DRS resolver failed: HTTP 403", artifact.error)
        self.assertIn("[REDACTED]", artifact.error)
        self.assertNotIn("test-key", artifact.error)

    def test_download_http_error_is_labeled_and_hides_signed_url(self):
        signed_url = (
            "https://download.example.test/file?"
            "X-Amz-Algorithm=AWS4-HMAC-SHA256&"
            "X-Amz-Credential=credential-secret&"
            "X-Amz-Date=20260911T153754Z&"
            "X-Amz-Expires=300&"
            "X-Amz-SignedHeaders=host&"
            "X-Amz-Signature=signature-secret"
        )
        resolver_payload = json.dumps({"url": signed_url}).encode()
        session = MODULE.ImmportSession(api_key="test-key")
        error = HTTPError(
            signed_url,
            403,
            "Forbidden",
            {},
            FakeResponse(f"denied {signed_url} test-key".encode()),
        )

        with tempfile.TemporaryDirectory() as tempdir:
            destination = Path(tempdir) / "a.txt"
            with (
                patch.object(
                    MODULE,
                    "_authed_get",
                    return_value=FakeResponse(resolver_payload),
                ),
                patch.object(MODULE, "urlopen", side_effect=error),
            ):
                artifact = MODULE._download_file(
                    "SDY1/StudyFiles/a.txt",
                    "uuid-1",
                    destination,
                    session,
                    force=False,
                    artifact_type="study_file",
                )

        self.assertEqual(artifact.status, "failed")
        self.assertIn("Signed download failed: HTTP 403", artifact.error)
        self.assertIn("[REDACTED]", artifact.error)
        self.assertIn("host=download.example.test", artifact.error)
        self.assertIn("signed_headers=host", artifact.error)
        self.assertIn("issued_at=20260911T153754Z", artifact.error)
        self.assertIn("expires_sec=300", artifact.error)
        self.assertNotIn(signed_url, artifact.error)
        self.assertNotIn("test-key", artifact.error)
        self.assertNotIn("credential-secret", artifact.error)
        self.assertNotIn("signature-secret", artifact.error)

    def test_stream_probe_classifies_confirmed_missing_source(self):
        signed_url = "https://download.example.test/file?token=signed-secret"
        stream_url = "https://drs.example.test/stream?token=stream-secret"
        session = MODULE.ImmportSession(api_key="test-key")
        signed_error = HTTPError(
            signed_url,
            403,
            "Forbidden",
            {},
            FakeResponse(b"<Error><Code>AccessDenied</Code></Error>"),
        )
        stream_error = HTTPError(
            stream_url,
            500,
            "Internal Server Error",
            {},
            FakeResponse(b'{"message":"The specified key does not exist."}'),
        )

        with tempfile.TemporaryDirectory() as tempdir:
            destination = Path(tempdir) / "a.txt"
            with (
                patch.object(
                    MODULE,
                    "_authed_get",
                    side_effect=[
                        FakeResponse(json.dumps({"url": signed_url}).encode()),
                        FakeResponse(json.dumps({"url": stream_url}).encode()),
                    ],
                ),
                patch.object(MODULE, "urlopen", side_effect=[signed_error, stream_error]),
            ):
                artifact = MODULE._download_file(
                    "SDY1/ResultFiles/a.txt",
                    "uuid-1",
                    destination,
                    session,
                    force=False,
                    artifact_type="result_file",
                )

        self.assertEqual(artifact.status, "source_missing")
        self.assertIn("stream probe=HTTP 500", artifact.error)
        self.assertIn("specified key does not exist", artifact.error)
        self.assertNotIn("signed-secret", artifact.error)
        self.assertNotIn("stream-secret", artifact.error)

    def test_direct_no_such_key_response_is_source_missing(self):
        signed_url = "https://download.example.test/file"
        session = MODULE.ImmportSession(api_key="test-key")
        error = HTTPError(
            signed_url,
            403,
            "Forbidden",
            {},
            FakeResponse(b"<Error><Code>NoSuchKey</Code></Error>"),
        )

        with tempfile.TemporaryDirectory() as tempdir:
            with (
                patch.object(
                    MODULE,
                    "_authed_get",
                    return_value=FakeResponse(json.dumps({"url": signed_url}).encode()),
                ),
                patch.object(MODULE, "urlopen", side_effect=error),
            ):
                artifact = MODULE._download_file(
                    "SDY1/ResultFiles/a.txt",
                    "uuid-1",
                    Path(tempdir) / "a.txt",
                    session,
                    force=False,
                    artifact_type="result_file",
                )

        self.assertEqual(artifact.status, "source_missing")

    def test_source_missing_artifact_makes_study_partial(self):
        artifacts = [
            MODULE.ArtifactRecord(
                artifact_type="filepath_manifest",
                status="success",
                source_path="manifest",
                local_file="manifest.json",
                retrieval_tool="urllib.request",
                retrieval_tool_version="test",
            ),
            MODULE.ArtifactRecord(
                artifact_type="result_file",
                status="source_missing",
                source_path="SDY1/ResultFiles/a.txt",
                local_file="a.txt",
                retrieval_tool="urllib.request",
                retrieval_tool_version="test",
            ),
        ]

        self.assertEqual(MODULE._summarize_status(artifacts), "partial")

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
