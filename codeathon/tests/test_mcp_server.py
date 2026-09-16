import gzip
import hashlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd
from fastmcp import Client

from mcp_server.geo_tools import GeoDataStore, GeoMcpDataError
from mcp_server.server import create_server

UNIT_ID = "SDY900001/EXP900001/GSE900001_GPL900001"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_fixture(root: Path) -> Path:
    parsed_root = root / "parsed"
    unit_dir = parsed_root / "SDY900001" / "EXP900001" / "GSE900001_GPL900001"
    unit_dir.mkdir(parents=True)
    expression_path = unit_dir / "expression.tsv.gz"
    samples_path = unit_dir / "samples.tsv"
    metadata_path = unit_dir / "geo_sample_metadata_long.tsv"

    with gzip.open(expression_path, "wt", encoding="utf-8", newline="") as handle:
        handle.write("ID_REF\tGSM900001\tGSM900002\nprobe1\t1.0\t2.0\n")
    pd.DataFrame(
        [
            {
                "study_accession": "SDY900001",
                "experiment_accession": "EXP900001",
                "repository_accession": gsm,
                "subject_accession": subject,
            }
            for gsm, subject in [
                ("GSM900001", "SUB900001"),
                ("GSM900002", "SUB900002"),
            ]
        ]
    ).to_csv(samples_path, sep="\t", index=False, lineterminator="\n")
    pd.DataFrame(
        [
            {
                "gsm_accession": gsm,
                "attribute": attribute,
                "occurrence": occurrence,
                "attribute_order": order,
                "value": value,
            }
            for gsm in ["GSM900001", "GSM900002"]
            for attribute, occurrence, order, value in [
                ("title", 0, 0, f"Title for {gsm}"),
                ("characteristics_ch1", 0, 1, "PBMC"),
                ("characteristics_ch1", 1, 2, "day: 7"),
            ]
        ]
    ).to_csv(metadata_path, sep="\t", index=False, lineterminator="\n")

    outputs = []
    for output_type, path in [
        ("expression", expression_path),
        ("samples", samples_path),
        ("geo_sample_metadata", metadata_path),
    ]:
        outputs.append(
            {
                "type": output_type,
                "path": f"/old/machine/{path.name}",
                "size_bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
        )
    manifest = {
        "status": "success",
        "run_started_at_utc": "2026-09-16T00:00:00+00:00",
        "parser_version": "0.1.0",
        "counts": {"download_units": 1, "parsed_samples": 2},
        "units": [
            {
                "study_accession": "SDY900001",
                "experiment_accession": "EXP900001",
                "gse_accession": "GSE900001",
                "gpl_accession": "GPL900001",
                "status": "success",
                "run_started_at_utc": "2026-09-16T00:00:00+00:00",
                "duration_sec": 0.1,
                "counts": {"samples": 2, "probes": 1, "geo_metadata_rows": 6},
                "source_matrix": {
                    "path": "/source/GSE900001_series_matrix.txt.gz",
                    "size_bytes": 123,
                    "sha256": "source-hash",
                },
                "outputs": outputs,
            }
        ],
    }
    (parsed_root / "geo_matrix_parse_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    return parsed_root


class GeoDataStoreTests(unittest.TestCase):
    def test_lists_units_and_rebases_output_paths(self) -> None:
        with TemporaryDirectory() as temp_dir:
            parsed_root = write_fixture(Path(temp_dir))
            store = GeoDataStore(parsed_root)

            listing = store.list_analysis_units()
            detail = store.get_analysis_unit(UNIT_ID)

        self.assertEqual(listing["units"][0]["analysis_unit_id"], UNIT_ID)
        self.assertEqual(detail["counts"]["samples"], 2)
        self.assertTrue(detail["outputs"][0]["path"].startswith(str(parsed_root.resolve())))
        repeated = next(
            item
            for item in detail["geo_metadata_attributes"]
            if item["attribute"] == "characteristics_ch1"
        )
        self.assertEqual(repeated["occurrences_per_sample"], 2)

    def test_sample_metadata_filters_and_paginates(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = GeoDataStore(write_fixture(Path(temp_dir)))

            result = store.get_sample_metadata(
                UNIT_ID,
                gsm_accessions=["gsm900002", "gsm900001"],
                attributes=["title"],
                limit=1,
            )

        self.assertEqual(result["returned_samples"], 1)
        self.assertEqual(result["next_offset"], 1)
        self.assertEqual(
            result["linked_samples"][0]["repository_accession"], "GSM900002"
        )
        self.assertEqual({row["attribute"] for row in result["geo_metadata"]}, {"title"})

    def test_expression_info_does_not_return_values(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = GeoDataStore(write_fixture(Path(temp_dir)))

            result = store.get_expression_info(UNIT_ID)

        self.assertEqual(result["probe_count"], 1)
        self.assertEqual(result["sample_accessions"], ["GSM900001", "GSM900002"])
        self.assertNotIn("values", result)

    def test_invalid_requests_fail_explicitly(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = GeoDataStore(write_fixture(Path(temp_dir)))

            with self.assertRaisesRegex(GeoMcpDataError, "Unknown analysis unit"):
                store.get_analysis_unit("../../outside")
            with self.assertRaisesRegex(GeoMcpDataError, "between 1 and 100"):
                store.get_sample_metadata(UNIT_ID, limit=101)
            with self.assertRaisesRegex(GeoMcpDataError, "not in"):
                store.get_sample_metadata(UNIT_ID, gsm_accessions=["GSM999999"])

    def test_output_symlink_cannot_escape_parsed_root(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            parsed_root = write_fixture(temp_root)
            samples_path = (
                parsed_root
                / "SDY900001"
                / "EXP900001"
                / "GSE900001_GPL900001"
                / "samples.tsv"
            )
            outside = temp_root / "outside.tsv"
            outside.write_text("secret\n", encoding="utf-8")
            samples_path.unlink()
            samples_path.symlink_to(outside)

            with self.assertRaisesRegex(GeoMcpDataError, "outside parsed root"):
                GeoDataStore(parsed_root).get_analysis_unit(UNIT_ID)


class McpProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_tools_are_available_through_in_memory_client(self) -> None:
        with TemporaryDirectory() as temp_dir:
            server = create_server(write_fixture(Path(temp_dir)))

            async with Client(server) as client:
                tools = await client.list_tools()
                result = await client.call_tool("list_analysis_units", {})

        self.assertEqual(
            {tool.name for tool in tools},
            {
                "list_analysis_units",
                "get_analysis_unit",
                "get_sample_metadata",
                "get_expression_info",
            },
        )
        self.assertEqual(result.data["units"][0]["analysis_unit_id"], UNIT_ID)


if __name__ == "__main__":
    unittest.main()
