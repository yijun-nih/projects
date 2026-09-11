import json
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from data import immport_batch_parse as module

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "immport_tab"


def write_tab_archive(path: Path, prefix: str) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for source in sorted(FIXTURE_DIR.iterdir()):
            archive.write(source, f"{prefix}/Tab/{source.name}")


class ImmportBatchParseTests(unittest.TestCase):
    def test_discovery_is_dynamic_and_selects_highest_release(self) -> None:
        with TemporaryDirectory() as temp_dir:
            cache_root = Path(temp_dir)
            study_dir = cache_root / "SDY900001"
            study_dir.mkdir()
            old_archive = study_dir / "SDY900001-DR7_Tab.zip"
            current_archive = study_dir / "SDY900001-DR12_Tab.zip"
            write_tab_archive(old_archive, "SDY900001-DR7_Tab")
            write_tab_archive(current_archive, "SDY900001-DR12_Tab")

            discovered = module.discover_tab_sources(cache_root)

        self.assertEqual(set(discovered), {"SDY900001"})
        self.assertEqual(discovered["SDY900001"][0], 12)
        self.assertEqual(discovered["SDY900001"][1].name, current_archive.name)

    def test_discovery_prefers_extracted_tables_for_same_release(self) -> None:
        with TemporaryDirectory() as temp_dir:
            cache_root = Path(temp_dir)
            study_dir = cache_root / "SDY900001"
            study_dir.mkdir()
            archive = study_dir / "SDY900001-DR12_Tab.zip"
            write_tab_archive(archive, "SDY900001-DR12_Tab")
            extracted = study_dir / "SDY900001-DR12_Tab" / "Tab"
            extracted.mkdir(parents=True)
            for source in FIXTURE_DIR.iterdir():
                (extracted / source.name).write_bytes(source.read_bytes())

            discovered = module.discover_tab_sources(cache_root)

        self.assertEqual(discovered["SDY900001"][1], extracted.resolve())

    def test_batch_continues_after_one_study_fails(self) -> None:
        with TemporaryDirectory() as temp_dir:
            cache_root = Path(temp_dir)
            valid_dir = cache_root / "SDY900001"
            invalid_dir = cache_root / "SDY900002"
            valid_dir.mkdir()
            invalid_dir.mkdir()
            write_tab_archive(
                valid_dir / "SDY900001-DR12_Tab.zip",
                "SDY900001-DR12_Tab",
            )
            with zipfile.ZipFile(invalid_dir / "SDY900002-DR3_Tab.zip", "w") as archive:
                archive.writestr("SDY900002-DR3_Tab/Tab/experiment.txt", "invalid\n")

            provenance = module.run_batch(cache_root)
            combined = pd.read_csv(
                cache_root / "parsed" / module.COMBINED_FILENAME,
                sep="\t",
                dtype=str,
                keep_default_na=False,
            )
            stored = json.loads(
                (cache_root / "parsed" / module.BATCH_PROVENANCE_FILENAME).read_text(
                    encoding="utf-8"
                )
            )

        self.assertEqual(provenance["status"], "partial")
        self.assertEqual(provenance["counts"]["studies_succeeded"], 1)
        self.assertEqual(provenance["counts"]["studies_failed"], 1)
        self.assertEqual(len(combined), 2)
        self.assertEqual(stored["studies"][0]["data_release"], 12)


if __name__ == "__main__":
    unittest.main()
