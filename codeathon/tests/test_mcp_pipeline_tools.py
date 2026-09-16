import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from mcp_server.pipeline_tools import PipelineToolError, PipelineTools


def artifact(artifact_type: str = "series_matrix") -> SimpleNamespace:
    return SimpleNamespace(
        artifact_type=artifact_type,
        status="cached",
        local_file="/cache/artifact.gz",
        size_bytes=123,
        sha256="abc123",
        error=None,
    )


class PipelineToolsTests(unittest.TestCase):
    def test_fetch_immport_uses_fixed_cache_and_hides_remote_urls(self) -> None:
        with TemporaryDirectory() as temp_dir:
            tools = PipelineTools(Path(temp_dir) / "data")
            record = SimpleNamespace(
                sdy_id="SDY900001",
                status="cached",
                n_files_listed=2,
                n_files_downloaded=2,
                duration_sec=0.1,
                error=None,
                artifacts=[artifact("result_file")],
            )
            with patch(
                "mcp_server.pipeline_tools.fetch_immport_datasets",
                return_value=[record],
            ) as fetch:
                result = tools.fetch_immport_studies(["sdy900001"], 5)

        fetch.assert_called_once_with(
            ["SDY900001"],
            destdir=str(tools.immport_cache),
            force=False,
            max_files_per_study=5,
            provenance_log_path=str(tools.immport_cache / "provenance_log.jsonl"),
        )
        self.assertEqual(result["status"], "success")
        self.assertNotIn("source_url", result["results"][0]["artifacts"][0])

    def test_fetch_planned_geo_uses_only_download_rows(self) -> None:
        with TemporaryDirectory() as temp_dir:
            tools = PipelineTools(Path(temp_dir) / "data")
            tools.geo_plan.mkdir(parents=True)
            pd.DataFrame(
                [
                    {
                        "gse_accession": "GSE900001",
                        "download_recommendation": "download",
                    },
                    {
                        "gse_accession": "GSE900002",
                        "download_recommendation": "skip_superseries",
                    },
                    {
                        "gse_accession": "GSE900001",
                        "download_recommendation": "download",
                    },
                ]
            ).to_csv(tools.geo_plan_path, sep="\t", index=False)
            record = SimpleNamespace(
                gse_id="GSE900001",
                status="cached",
                n_platforms=1,
                n_samples=2,
                duration_sec=0.1,
                error=None,
                artifacts=[artifact()],
            )
            with patch(
                "mcp_server.pipeline_tools.fetch_geo_datasets",
                return_value=[record],
            ) as fetch:
                result = tools.fetch_planned_geo()

        fetch.assert_called_once_with(
            ["GSE900001"],
            destdir=str(tools.geo_cache),
            force=False,
            provenance_log_path=str(tools.geo_cache / "provenance_log.jsonl"),
        )
        self.assertEqual(result["planned_accessions"], ["GSE900001"])

    def test_parse_and_plan_stages_use_only_configured_paths(self) -> None:
        with TemporaryDirectory() as temp_dir:
            tools = PipelineTools(Path(temp_dir) / "data")
            immport_provenance = {
                "status": "success",
                "counts": {"studies_succeeded": 1},
                "studies": [],
                "combined_output": {"path": str(tools.immport_manifest)},
                "duration_sec": 0.1,
            }
            plan_provenance = {
                "status": "success",
                "counts": {"download_plan_rows": 1},
                "resolution_status_counts": {"resolved": 2},
                "series_status_counts": {"resolved": 1},
                "duration_sec": 0.1,
            }
            geo_provenance = {
                "status": "success",
                "counts": {"units_succeeded": 1},
                "units": [
                    {
                        "study_accession": "SDY900001",
                        "experiment_accession": "EXP900001",
                        "gse_accession": "GSE900001",
                        "gpl_accession": "GPL900001",
                        "status": "success",
                        "counts": {"samples": 2},
                    }
                ],
                "duration_sec": 0.1,
            }
            with (
                patch(
                    "mcp_server.pipeline_tools.run_immport_batch_parser",
                    return_value=immport_provenance,
                ) as parse_immport,
                patch(
                    "mcp_server.pipeline_tools.plan_geo_downloads",
                    return_value=plan_provenance,
                ) as plan_geo,
                patch(
                    "mcp_server.pipeline_tools.run_geo_matrix_parser",
                    return_value=geo_provenance,
                ) as parse_geo,
            ):
                tools.parse_immport_studies()
                tools.plan_geo_retrieval(batch_size=10, timeout=20)
                result = tools.parse_geo_matrices()

        parse_immport.assert_called_once_with(tools.immport_cache)
        plan_geo.assert_called_once_with(
            tools.immport_manifest,
            tools.geo_plan,
            batch_size=10,
            timeout=20,
            force=False,
        )
        parse_geo.assert_called_once_with(
            tools.geo_plan_path,
            tools.immport_manifest,
            tools.geo_cache,
            tools.geo_parsed,
        )
        self.assertEqual(
            result["units"][0]["analysis_unit_id"],
            "SDY900001/EXP900001/GSE900001_GPL900001",
        )

    def test_invalid_or_unplanned_inputs_fail_before_network_calls(self) -> None:
        with TemporaryDirectory() as temp_dir:
            tools = PipelineTools(Path(temp_dir) / "data")

            with self.assertRaisesRegex(PipelineToolError, "valid accession"):
                tools.fetch_immport_studies(["invalid"])
            with self.assertRaisesRegex(PipelineToolError, "does not exist"):
                tools.fetch_planned_geo()


if __name__ == "__main__":
    unittest.main()
