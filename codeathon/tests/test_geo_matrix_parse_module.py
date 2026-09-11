import gzip
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from data import geo_matrix_parse_module as module

FIXTURE = Path(__file__).parent / "fixtures" / "geo_matrix" / "series_matrix.txt"


def write_gzip(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as input_handle:
        with destination.open("wb") as output_handle:
            with gzip.GzipFile(fileobj=output_handle, mode="wb", mtime=0) as compressed:
                compressed.write(input_handle.read())


def write_inputs(root: Path, requested: str = "GSM900001;GSM900002") -> tuple[Path, Path]:
    plan = root / "geo_download_plan.tsv"
    manifest = root / "sample_manifest.tsv"
    pd.DataFrame(
        [
            {
                "study_accession": "SDY900001",
                "experiment_accession": "EXP900001",
                "gse_accession": "GSE900001",
                "gpl_accession": "GPL900001",
                "download_recommendation": "download",
                "requested_sample_count": len(requested.split(";")),
                "gsm_accessions": requested,
            },
            {
                "study_accession": "SDY900001",
                "experiment_accession": "EXP900001",
                "gse_accession": "GSE900002",
                "gpl_accession": "GPL900001",
                "download_recommendation": "skip_superseries",
                "requested_sample_count": 2,
                "gsm_accessions": "GSM900001;GSM900002",
            },
        ]
    ).to_csv(plan, sep="\t", index=False)
    pd.DataFrame(
        [
            {
                "study_accession": "SDY900001",
                "experiment_accession": "EXP900001",
                "repository_name": "GEO",
                "repository_accession": accession,
                "subject_accession": f"SUB{index}",
            }
            for index, accession in enumerate(["GSM900001", "GSM900002"], start=1)
        ]
    ).to_csv(manifest, sep="\t", index=False)
    return plan, manifest


class GeoMatrixParseModuleTests(unittest.TestCase):
    def test_parser_filters_matrix_and_preserves_repeated_metadata(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan, manifest = write_inputs(root)
            cache = root / "geo_cache"
            matrix = cache / "GSE900001" / "GSE900001_series_matrix.txt.gz"
            write_gzip(FIXTURE, matrix)

            provenance = module.parse_geo_matrices(plan, manifest, cache, root / "parsed")
            unit_dir = (
                root
                / "parsed"
                / "SDY900001"
                / "EXP900001"
                / "GSE900001_GPL900001"
            )
            expression = pd.read_csv(
                unit_dir / module.EXPRESSION_FILENAME,
                sep="\t",
                dtype=str,
                keep_default_na=False,
            )
            samples = pd.read_csv(unit_dir / module.SAMPLES_FILENAME, sep="\t", dtype=str)
            metadata = pd.read_csv(unit_dir / module.GEO_METADATA_FILENAME, sep="\t", dtype=str)
            stored = json.loads(
                (root / "parsed" / module.RUN_MANIFEST_FILENAME).read_text(encoding="utf-8")
            )

        self.assertEqual(provenance["status"], "success")
        self.assertEqual(list(expression.columns), ["ID_REF", "GSM900001", "GSM900002"])
        self.assertEqual(expression.shape, (3, 3))
        self.assertEqual(len(samples), 2)
        characteristics = metadata.loc[metadata["attribute"].eq("characteristics_ch1")]
        self.assertEqual(set(characteristics["occurrence"]), {"0", "1"})
        self.assertEqual(stored["counts"]["skipped_plan_rows"], 1)
        self.assertEqual(stored["counts"]["parsed_samples"], 2)

    def test_missing_requested_gsm_is_recorded_as_failure(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan, manifest = write_inputs(root, "GSM900001;GSM900099")
            cache = root / "geo_cache"
            matrix = cache / "GSE900001" / "GSE900001_series_matrix.txt.gz"
            write_gzip(FIXTURE, matrix)

            provenance = module.parse_geo_matrices(plan, manifest, cache, root / "parsed")
            unit = provenance["units"][0]
            stored = json.loads(
                (
                    root
                    / "parsed"
                    / "SDY900001"
                    / "EXP900001"
                    / "GSE900001_GPL900001"
                    / module.UNIT_PROVENANCE_FILENAME
                ).read_text(encoding="utf-8")
            )

        self.assertEqual(provenance["status"], "failed")
        self.assertEqual(unit["status"], "failed")
        self.assertIn("No matrix contains", unit["error"])
        self.assertEqual(stored["status"], "failed")

    def test_platform_selects_one_of_multiple_matrix_files(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan, manifest = write_inputs(root)
            cache = root / "geo_cache"
            desired = cache / "GSE900001" / "GSE900001-GPL900001_series_matrix.txt.gz"
            write_gzip(FIXTURE, desired)
            other_text = FIXTURE.read_text(encoding="utf-8").replace("GPL900001", "GPL900002")
            other_source = root / "other_matrix.txt"
            other_source.write_text(other_text, encoding="utf-8")
            write_gzip(
                other_source,
                cache / "GSE900001" / "GSE900001-GPL900002_series_matrix.txt.gz",
            )

            provenance = module.parse_geo_matrices(plan, manifest, cache, root / "parsed")

        self.assertEqual(provenance["status"], "success")
        self.assertEqual(provenance["units"][0]["source_matrix"]["path"], str(desired.resolve()))

    def test_invalid_unit_is_recorded_without_stopping_valid_unit(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan, manifest = write_inputs(root)
            plan_frame = pd.read_csv(plan, sep="\t", dtype=str)
            invalid = plan_frame.iloc[[0]].copy()
            invalid["gse_accession"] = "invalid"
            pd.concat([plan_frame, invalid], ignore_index=True).to_csv(
                plan,
                sep="\t",
                index=False,
            )
            cache = root / "geo_cache"
            write_gzip(
                FIXTURE,
                cache / "GSE900001" / "GSE900001_series_matrix.txt.gz",
            )

            provenance = module.parse_geo_matrices(plan, manifest, cache, root / "parsed")

        self.assertEqual(provenance["status"], "partial")
        self.assertEqual(provenance["counts"]["units_succeeded"], 1)
        self.assertEqual(provenance["counts"]["units_failed"], 1)
        self.assertIn("invalid gse_accession", provenance["units"][1]["error"])


if __name__ == "__main__":
    unittest.main()
