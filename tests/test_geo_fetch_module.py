import gzip
import importlib.util
import io
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


MODULE_PATH = Path(__file__).parents[1] / "data" / "geo-fetch-module.py"
SPEC = importlib.util.spec_from_file_location("geo_fetch_module", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()


class GeoFetchModuleTests(unittest.TestCase):
    def test_url_construction_normalizes_accession(self):
        self.assertEqual(
            MODULE._gse_family_soft_url("gse13699"),
            "https://ftp.ncbi.nlm.nih.gov/geo/series/"
            "GSE13nnn/GSE13699/soft/GSE13699_family.soft.gz",
        )
        self.assertEqual(
            MODULE._gse_matrix_directory_url("GSE125921"),
            "https://ftp.ncbi.nlm.nih.gov/geo/series/"
            "GSE125nnn/GSE125921/matrix/",
        )

    def test_invalid_accession_is_rejected(self):
        with self.assertRaises(ValueError):
            MODULE._normalize_gse_id("GDS13699")

    def test_matrix_discovery_filters_and_sorts_links(self):
        listing = b"""
            <a href="../">parent</a>
            <a href="GSE13699-GPL6883_series_matrix.txt.gz">matrix 2</a>
            <a href="GSE13699_family.soft.gz">soft</a>
            <a href="GSE136990_series_matrix.txt.gz">other GSE</a>
            <a href="GSE13699-GPL6104_series_matrix.txt.gz">matrix 1</a>
            <a href="GSE13699-GPL6104_series_matrix.txt.gz">duplicate</a>
        """
        with patch.object(MODULE, "_open_url", return_value=FakeResponse(listing)):
            urls = MODULE._discover_series_matrix_urls("GSE13699")

        self.assertEqual(
            [Path(MODULE.urlparse(url).path).name for url in urls],
            [
                "GSE13699-GPL6104_series_matrix.txt.gz",
                "GSE13699-GPL6883_series_matrix.txt.gz",
            ],
        )

    def test_download_validates_and_reuses_cached_gzip(self):
        payload = gzip.compress(b"!Series_title = test\n")
        with tempfile.TemporaryDirectory() as tempdir:
            destination = Path(tempdir) / "GSE1_family.soft.gz"
            with patch.object(
                MODULE,
                "_open_url",
                return_value=FakeResponse(payload),
            ) as open_url:
                downloaded = MODULE._fetch_gzip_artifact(
                    "https://example.test/GSE1_family.soft.gz",
                    destination,
                    "family_soft",
                    force=False,
                )
                cached = MODULE._fetch_gzip_artifact(
                    "https://example.test/GSE1_family.soft.gz",
                    destination,
                    "family_soft",
                    force=False,
                )

        self.assertEqual(downloaded.status, "success")
        self.assertEqual(cached.status, "cached")
        self.assertEqual(downloaded.sha256, cached.sha256)
        self.assertEqual(open_url.call_count, 1)

    def test_corrupt_download_is_not_promoted_to_cache(self):
        with tempfile.TemporaryDirectory() as tempdir:
            destination = Path(tempdir) / "broken.txt.gz"
            with patch.object(
                MODULE,
                "_open_url",
                return_value=FakeResponse(b"not gzip"),
            ):
                artifact = MODULE._fetch_gzip_artifact(
                    "https://example.test/broken.txt.gz",
                    destination,
                    "series_matrix",
                    force=False,
                )

            self.assertEqual(artifact.status, "failed")
            self.assertFalse(destination.exists())
            self.assertFalse(destination.with_name("broken.txt.gz.part").exists())

    def test_fetch_one_records_soft_and_matrix_provenance(self):
        soft_payload = gzip.compress(b"^SERIES = GSE13699\n")
        matrix_payload = gzip.compress(b"ID_REF\tGSM1\nprobe1\t1.0\n")
        listing = b'<a href="GSE13699_series_matrix.txt.gz">matrix</a>'
        fake_geoparse = SimpleNamespace(
            get_GEO=lambda **kwargs: SimpleNamespace(gpls={"GPL1": 1}, gsms={"GSM1": 1})
        )

        with tempfile.TemporaryDirectory() as tempdir:
            responses = [
                FakeResponse(soft_payload),
                FakeResponse(listing),
                FakeResponse(matrix_payload),
            ]
            with (
                patch.object(MODULE, "GEOparse", fake_geoparse),
                patch.object(MODULE, "_open_url", side_effect=responses),
            ):
                record = MODULE.fetch_one("gse13699", Path(tempdir))

        self.assertEqual(record.status, "success")
        self.assertEqual(record.gse_id, "GSE13699")
        self.assertEqual(record.n_platforms, 1)
        self.assertEqual(record.n_samples, 1)
        self.assertEqual(len(record.artifacts), 2)
        self.assertEqual(
            [artifact.artifact_type for artifact in record.artifacts],
            ["family_soft", "series_matrix"],
        )
        self.assertTrue(record.family_soft_url.endswith("GSE13699_family.soft.gz"))
        self.assertTrue(record.matrix_index_url.endswith("GSE13699/matrix/"))


if __name__ == "__main__":
    unittest.main()
