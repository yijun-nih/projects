import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

from data import geo_plan_module as module


def write_manifest(path: Path) -> None:
    pd.DataFrame(
        [
            {
                "study_accession": "SDY900001",
                "experiment_accession": "EXP1",
                "experiment_name": "Expression experiment",
                "measurement_technique": "Transcription profiling by array",
                "repository_name": "GEO",
                "repository_accession": "GSM101",
            },
            {
                "study_accession": "SDY900001",
                "experiment_accession": "EXP1",
                "experiment_name": "Expression experiment",
                "measurement_technique": "Transcription profiling by array",
                "repository_name": "GEO",
                "repository_accession": "GSM102",
            },
            {
                "study_accession": "SDY900001",
                "experiment_accession": "EXP1",
                "experiment_name": "Expression experiment",
                "measurement_technique": "Transcription profiling by array",
                "repository_name": "GEO",
                "repository_accession": "not-a-gsm",
            },
            {
                "study_accession": "SDY900001",
                "experiment_accession": "EXP2",
                "experiment_name": "Other repository experiment",
                "measurement_technique": "Other",
                "repository_name": "ArrayExpress",
                "repository_accession": "E-MTAB-1",
            },
        ]
    ).to_csv(path, sep="\t", index=False)


def fake_resolve_batch(accessions, timeout, api_key):
    records = {
        accession: {
            "status": "resolved",
            "gse_accessions": ["GSE500"],
            "gpl_accessions": ["GPL600"],
            "sample_title": f"Sample {accession}",
            "resolved_at_utc": "2026-01-01T00:00:00+00:00",
            "source": "test",
        }
        for accession in accessions
    }
    query = {
        "started_at_utc": "2026-01-01T00:00:00+00:00",
        "requested_accessions": accessions,
        "status": "success",
    }
    return records, query


def fake_resolve_series_batch(accessions, timeout, api_key):
    records = {
        accession: {
            "status": "resolved",
            "series_title": f"Series {accession}",
            "series_type": "series",
            "series_sample_count": 2,
            "resolved_at_utc": "2026-01-01T00:00:00+00:00",
            "source": "test",
        }
        for accession in accessions
    }
    query = {
        "query_type": "gse_metadata",
        "started_at_utc": "2026-01-01T00:00:00+00:00",
        "requested_accessions": accessions,
        "status": "success",
    }
    return records, query


class GeoPlanModuleTests(unittest.TestCase):
    def test_resolve_batch_filters_summary_to_requested_gsms(self) -> None:
        responses = [
            {"esearchresult": {"idlist": ["30101", "30102"]}},
            {
                "result": {
                    "uids": ["30101", "30102"],
                    "30101": {
                        "accession": "GSM101",
                        "entrytype": "GSM",
                        "gse": "500",
                        "gpl": "600",
                        "title": "Sample one",
                    },
                    "30102": {
                        "accession": "GSE101",
                        "entrytype": "GSE",
                        "gse": "101",
                    },
                }
            },
        ]
        with patch.object(module, "_post_json", side_effect=responses) as post_json:
            records, query = module._resolve_batch(["GSM101"], 30, None)

        self.assertEqual(records["GSM101"]["status"], "resolved")
        self.assertEqual(records["GSM101"]["gse_accessions"], ["GSE500"])
        self.assertEqual(records["GSM101"]["gpl_accessions"], ["GPL600"])
        self.assertEqual(query["returned_uids"], 2)
        self.assertEqual(post_json.call_count, 2)

    def test_plan_groups_samples_and_reuses_cache(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest = root / "sample_manifest.tsv"
            output_dir = root / "plan"
            write_manifest(manifest)

            with (
                patch.object(module, "_resolve_batch", side_effect=fake_resolve_batch) as resolve,
                patch.object(
                    module,
                    "_resolve_series_batch",
                    side_effect=fake_resolve_series_batch,
                ) as resolve_series,
            ):
                provenance = module.plan_geo_downloads(
                    manifest,
                    output_dir,
                    batch_size=1,
                )
            plan = pd.read_csv(
                output_dir / module.PLAN_FILENAME,
                sep="\t",
                dtype=str,
                keep_default_na=False,
            )
            stored = json.loads(
                (output_dir / module.PROVENANCE_FILENAME).read_text(encoding="utf-8")
            )
            with (
                patch.object(module, "_resolve_batch") as cached_resolve,
                patch.object(module, "_resolve_series_batch") as cached_series_resolve,
            ):
                cached = module.plan_geo_downloads(manifest, output_dir, batch_size=1)

        self.assertEqual(resolve.call_count, 2)
        self.assertEqual(resolve_series.call_count, 1)
        self.assertEqual(provenance["status"], "partial")
        self.assertEqual(provenance["counts"]["resolved_gsm_accessions"], 2)
        self.assertEqual(provenance["counts"]["unresolved_gsm_accessions"], 1)
        resolved_plan = plan.loc[plan["resolution_status"].eq("resolved")].iloc[0]
        self.assertEqual(resolved_plan["requested_sample_count"], "2")
        self.assertEqual(resolved_plan["gsm_accessions"], "GSM101;GSM102")
        self.assertEqual(resolved_plan["download_recommendation"], "download")
        self.assertEqual(stored["resolution_status_counts"]["invalid_accession"], 1)
        cached_resolve.assert_not_called()
        cached_series_resolve.assert_not_called()
        self.assertEqual(cached["queries"], [])

    def test_series_resolution_marks_explicit_superseries(self) -> None:
        responses = [
            {"esearchresult": {"idlist": ["20500", "20501"]}},
            {
                "result": {
                    "uids": ["20500", "20501"],
                    "20500": {
                        "accession": "GSE500",
                        "entrytype": "GSE",
                        "title": "Direct series",
                        "summary": "A direct expression study.",
                        "n_samples": 2,
                    },
                    "20501": {
                        "accession": "GSE501",
                        "entrytype": "GSE",
                        "title": "Combined series",
                        "summary": "This SuperSeries is composed of the SubSeries listed below.",
                        "n_samples": 3,
                    },
                }
            },
        ]
        with patch.object(module, "_post_json", side_effect=responses):
            records, query = module._resolve_series_batch(["GSE500", "GSE501"], 30, None)

        self.assertEqual(records["GSE500"]["series_type"], "series")
        self.assertEqual(records["GSE501"]["series_type"], "superseries")
        self.assertEqual(records["GSE501"]["series_sample_count"], 3)
        self.assertEqual(query["query_type"], "gse_metadata")

    def test_query_failure_is_explicit(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest = root / "sample_manifest.tsv"
            write_manifest(manifest)
            with patch.object(module, "_resolve_batch", side_effect=OSError("offline")):
                provenance = module.plan_geo_downloads(manifest, root / "plan")

        self.assertEqual(provenance["status"], "failed")
        self.assertEqual(provenance["resolution_status_counts"]["query_failed"], 2)
        self.assertEqual(provenance["resolution_status_counts"]["invalid_accession"], 1)


if __name__ == "__main__":
    unittest.main()
