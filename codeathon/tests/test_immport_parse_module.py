import importlib.util
import json
import shutil
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

MODULE_PATH = Path(__file__).parents[1] / "data" / "immport_parse_module.py"
SPEC = importlib.util.spec_from_file_location("immport_parse_module", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "immport_tab"


def write_tab_archive(path: Path, prefix: str = "SDY900001-DR99_Tab") -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for source in sorted(FIXTURE_DIR.iterdir()):
            archive.write(source, f"{prefix}/Tab/{source.name}")


class ImmportParseModuleTests(unittest.TestCase):
    def test_parser_preserves_samples_without_repository_links(self) -> None:
        frame = MODULE.parse_immport_sample_links(FIXTURE_DIR)

        self.assertEqual(frame["expsample_accession"].tolist(), ["ES1", "ES2"])
        self.assertEqual(frame.loc[0, "repository_accession"], "GSM1")
        self.assertEqual(frame.loc[1, "repository_accession"], "")
        self.assertEqual(frame.loc[1, "study_time_collected"], "7")

    def test_run_parser_writes_manifest_and_provenance(self) -> None:
        with TemporaryDirectory() as temp_dir:
            provenance = MODULE.run_parser(FIXTURE_DIR, temp_dir)
            output_dir = Path(temp_dir)
            output = pd.read_csv(
                output_dir / MODULE.OUTPUT_FILENAME,
                sep="\t",
                dtype=str,
                keep_default_na=False,
            )
            stored = json.loads(
                (output_dir / MODULE.PROVENANCE_FILENAME).read_text(encoding="utf-8")
            )

        self.assertEqual(len(output), 2)
        self.assertEqual(provenance["status"], "success")
        self.assertEqual(stored["counts"]["repository_linked_samples"], 1)
        self.assertEqual(len(stored["inputs"]), 8)
        self.assertEqual(len(stored["output"]["sha256"]), 64)

    def test_parser_reads_tab_tables_directly_from_zip(self) -> None:
        with TemporaryDirectory() as temp_dir:
            archive = Path(temp_dir) / "SDY900001-DR99_Tab.zip"
            output_dir = Path(temp_dir) / "parsed"
            write_tab_archive(archive)

            frame = MODULE.parse_immport_sample_links(archive)
            provenance = MODULE.run_parser(archive, output_dir)

        self.assertEqual(len(frame), 2)
        self.assertEqual(provenance["input_source"]["type"], "zip")
        self.assertEqual(len(provenance["input_source"]["sha256"]), 64)
        self.assertEqual(len(provenance["inputs"]), 8)
        self.assertTrue(all("member" in record for record in provenance["inputs"]))

    def test_duplicate_join_key_fails(self) -> None:
        with TemporaryDirectory() as temp_dir:
            copied_fixture = Path(temp_dir) / "Tab"
            shutil.copytree(FIXTURE_DIR, copied_fixture)
            repository = copied_fixture / "expsample_public_repository.txt"
            with repository.open("a", encoding="utf-8") as handle:
                handle.write("ES1\tGSM2\tGEO\n")

            with self.assertRaisesRegex(MODULE.ImmportParseError, "duplicate"):
                MODULE.parse_immport_sample_links(copied_fixture)

    def test_missing_required_column_fails(self) -> None:
        with TemporaryDirectory() as temp_dir:
            copied_fixture = Path(temp_dir) / "Tab"
            shutil.copytree(FIXTURE_DIR, copied_fixture)
            experiment = copied_fixture / "experiment.txt"
            experiment.write_text(
                "EXPERIMENT_ACCESSION\tNAME\tSTUDY_ACCESSION\n"
                "EXP1\tExpression experiment\tSDY1\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(MODULE.ImmportParseError, "measurement_technique"):
                MODULE.parse_immport_sample_links(copied_fixture)


if __name__ == "__main__":
    unittest.main()
