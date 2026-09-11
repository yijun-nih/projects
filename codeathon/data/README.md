# Dataset acquisition

Run both fetchers from the repository root after installing dependencies with `uv sync`. Generated
data and provenance are cached locally in directories excluded from Git.

## Default candidates

| Source | Accessions used when none are supplied |
|---|---|
| GEO | `GSE125921`, `GSE136163`, `GSE13485`, `GSE82152`, `GSE13699` |
| ImmPort | `SDY1529`, `SDY1264`, `SDY1294`, `SDY1289` |

Passing accessions on the command line replaces the corresponding default list.

## GEO

GEO requires internet access but no account or credentials.

```bash
# Fetch the default candidates.
uv run python data/geo_fetch_module.py

# Fetch selected studies only.
uv run python data/geo_fetch_module.py GSE13699 GSE125921
```

## ImmPort

ImmPort requires a personal API key with `browse` and `download` scopes. Create one on the
[ImmPort API Keys page](https://www.immport.org/auth/api/keys) and save the downloaded JSON file
under `data/immport_cache/`. Never commit or share it.

```bash
# Fetch selected studies.
uv run python data/immport_fetch_module.py SDY1529 SDY1264 \
  --api-key-file data/immport_cache/immport-key-REPLACE_ME.json

# Limit a smoke test to one file per study.
uv run python data/immport_fetch_module.py SDY1529 \
  --api-key-file data/immport_cache/immport-key-REPLACE_ME.json \
  --max-files-per-study 1
```

You may instead set `IMMPORT_API_KEY_FILE` to the JSON path or `IMMPORT_API_KEY` to the raw key.
Avoid placing a raw key in a command because shell history may retain it.

## Python API

```python
from data.geo_fetch_module import fetch_geo_datasets
from data.immport_fetch_module import fetch_immport_datasets

geo_records = fetch_geo_datasets(
    ["GSE13699"],
    provenance_log_path="data/geo_cache/provenance_log.jsonl",
)
immport_records = fetch_immport_datasets(
    ["SDY1529"],
    api_key_file="data/immport_cache/immport-key-REPLACE_ME.json",
    max_files_per_study=1,
    provenance_log_path="data/immport_cache/provenance_log.jsonl",
)
```

Each call returns one structured record per accession, including status, source and local paths,
file sizes, SHA-256 checksums, errors, and timing.

## Outputs and options

```text
data/
├── geo_cache/
│   ├── <GSE_ID>/
│   ├── manifest.json
│   └── provenance_log.jsonl
└── immport_cache/
    ├── <SDY_ID>/
    ├── manifest.json
    └── provenance_log.jsonl
```

Command-line runs write `manifest.json` and append to `provenance_log.jsonl`. Existing valid files
are reused. Use `--force` to download them again or `--destdir` to select another cache root. Run
either script with `--help` for all options.
