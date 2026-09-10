# Hypothesis2Omics: Agentic Multi-Omics Validation

**NIAID-BRC AI Codeathon 2.0 — Project 10**

## 1. What this is

Hypothesis2Omics is an AI-agent prototype that takes a biological hypothesis, finds public datasets that
could test it, judges whether each dataset is actually fit for that purpose, runs a real analysis on the
eligible ones, and produces a provenance-linked evidence report — including an honest account of every
judgment call and data-quality problem it ran into along the way.

**MVP scope:** the hypothesis "GCN2 (EIF2AK4) modulates the magnitude of the CD8+ T cell response to
yellow fever vaccination (YF-17D)," tested against public GEO transcriptomic datasets.

The point of the MVP is not to prove or disprove this specific hypothesis. It's to prove the *loop* works:
hypothesis → eligible datasets → real numbers → evidence table → transparent report. Once that loop is
solid, extending it to more hypotheses, more omics types, and more repositories (ImmPort, SRA,
MetaboLights, etc.) is mostly a matter of adding tools, not rethinking the architecture.

## 2. Design principle: code decides mechanics, the agent decides judgment

This split came out of manually inspecting a real candidate dataset (GSE13699 — see Section 5) and hitting
real inconsistencies that a naive "just parse it" approach would silently mishandle. The lesson generalizes:

- **Deterministic code** handles anything mechanical and repeatable: fetching metadata, parsing sample
  labels, computing correlations, detecting anomalies (missing values, conflicting fields, format drift).
  This should be boring, tested, and reused across every dataset the pipeline touches.
- **The LLM agent layer** handles anything that requires judgment: deciding whether a dataset's design
  actually matches the hypothesis, deciding which of several available signals should stand in for
  "CD8+ T cell response" when there's no direct annotation, deciding how much confidence a verdict
  deserves, and narrating *why* it made each call.

Concretely: the agent doesn't write the regex that splits `"YF014_NS_0; Montreal Cohort"` into donor ID and
day. It does decide that Montreal and Lausanne are different enough (different vaccine brand, different
platform, different metadata conventions) to warrant separate treatment in the final report, and it writes
the sentence explaining that decision.

## 3. Architecture

See `architecture-diagram` (rendered separately). Nine stages, grouped into four kinds of work:

| Stage | Kind | Does |
|---|---|---|
| 1. Hypothesis input | — | Free-text hypothesis from the user |
| 2. Hypothesis specification | LLM | Normalizes gene, aliases, outcome, context, and species |
| 3. Dataset discovery | Tool call | Searches GEO for candidate series (MVP: pre-seeded list; stretch: live E-utilities search) |
| 4. Metadata fetch | Tool call (deterministic) | Pulls series-level summary, design text, platform(s), sample characteristics — no full expression data yet |
| 5. Eligibility assessment | LLM + rules | Judges species/vaccine/design match; classifies what kind of CD8 signal (if any) the dataset offers; assigns confidence; decides whether sub-cohorts/platforms need separate handling |
| 6. Metadata consolidation | Deterministic | Parses donor ID + timepoint from whatever label format each sub-cohort uses; flags anomalies (conflicting values, inconsistent formatting, missing fields) |
| 7. Analysis execution | Tool call (deterministic) | Fetches full expression matrix; computes correlation between gene of interest and CD8 signature |
| 8. Cross-dataset synthesis | LLM | Combines per-dataset results into an overall supportive/contradictory/inconclusive call |
| 9. Evidence report | Output | Structured table + narrated reasoning log, both provenance-linked back to source data |

## 4. Tools needed

### MCP server tools (thin wrappers, deterministic)

| Tool | Input | Output | Notes |
|---|---|---|---|
| `geo_search` | free-text query | list of GSE accessions + titles | Stretch goal; MVP can hardcode candidate list from charter |
| `geo_fetch_summary` | GSE accession | title, summary, design text, platform ID(s), sample characteristics block | Fast — metadata only, no expression values |
| `geo_fetch_matrix` | GSE accession (+ platform, if multi-platform) | sample × probe expression DataFrame | Slow-ish (MBs), only called after eligibility passes |
| `correlate` | matrix, gene symbol, CD8 marker gene(s) or annotated CD8 metric, optional grouping | r/rho, p-value, n, method used | Handles both "proxy via marker genes" and "direct annotation" cases |

### Supporting libraries

- **GEOparse** (Python) — series matrix + metadata parsing; handles gzip, platform annotation lookup
- **pandas** — tabular manipulation
- **scipy.stats** — correlation (Pearson/Spearman depending on distribution)
- **NCBI E-utilities** (esearch/esummary) — fallback/live discovery if `geo_search` goes beyond the hardcoded list

### Stretch integrations (post-MVP)

- **BRC Analytics / Galaxy** — hand off the correlation/DE step as a reproducible workflow run, rather than a local script
- **FastMCP** (Python MCP SDK) — used to expose the above tools as an actual MCP server other agents/hosts can call

## 5. Worked example: what GSE13699 taught us

GSE13699 (Querec et al. 2009, Nature Immunology) is one of the charter's candidate datasets. Manually
inspecting its series matrix header surfaced every category of problem this pipeline needs to handle
gracefully:

- **Multi-branch design.** The series has three branches: Montreal cohort (15 donors, in vivo), Lausanne
  cohort (11 donors, in vivo, different vaccine brand), and a VaxDesign MIMIC in vitro co-culture arm.
  The in vitro arm doesn't test a real vaccination response and should be excluded — a call the eligibility
  step should make and state, not silently apply.
- **Multi-platform.** Two platform IDs (`GPL6104`, `GPL6883`) are listed for one series. GEO splits
  multi-platform series into separate matrix files; probe-to-gene mapping must happen per-platform before
  any cross-cohort comparison is valid.
- **Inconsistent label formatting between sub-cohorts.** Montreal uses `"Day0"` (no space) in
  `Sample_source_name_ch1`; Lausanne uses `"Day 0"` (with space) — same field, same series, different
  convention, entirely capable of silently breaking a naive parser.
- **Inconsistent characteristics formatting.** Montreal: `"Vaccinated Volunteer : YF015; Gender: M; Age: 26"`.
  Lausanne: `"Non Vaccinated Volunteer :YF10; Gender;M : Age:27"` — different capitalization, different
  separator placement, no fixed schema.
- **A field that looks like a covariate but isn't.** "Vaccinated"/"Non vaccinated" in `characteristics_ch1`
  is not an independent treatment-arm label — it's redundant with the timepoint (every sample before Day 0
  is "non vaccinated," every sample after is "vaccinated" for the same donor). A pipeline that treated this
  as an independent grouping variable would be double-counting information already in the day field.
- **A real data anomaly.** One Lausanne donor (YF21) has three samples; two list Age 24, one lists Age 26.
  Either a typo in the original 2008 submission or a mislabeled sample — the pipeline should flag this and
  exclude it from analysis by default rather than silently averaging over the discrepancy.

None of this invalidates the dataset — it's still a legitimate, well-known, published YF-17D cohort. But
it's a clear demonstration of why "fetch and correlate" can't be the whole pipeline: the eligibility and
consolidation stages exist specifically to catch and report issues like these before they corrupt a result.

## 6. Evidence table (shape TBD — pending data analysis)

**Open decision:** should each row represent one GEO accession (GSE-level), or one cohort/platform within
an accession (e.g. GSE13699's Montreal and Lausanne cohorts as separate rows, since they differ in vaccine
brand, platform, and sample size)? GSE13699 alone shows why this isn't obvious — a single GSE can bundle
sub-studies that arguably shouldn't be pooled into one verdict.

This will be settled once we've run real correlations and can see whether pooling vs. splitting changes
the result meaningfully. Rough shape either way:

| Accession (+ cohort/platform, if split) | N (eligible) | CD8 readout type | Verdict | r / p | Confidence | Caveats |
|---|---|---|---|---|---|---|
| TBD | TBD | TBD | TBD | TBD | TBD | TBD |

## 7. Open questions for the team

- **Evidence table granularity** (Section 6): one row per GSE, or per cohort/platform within a GSE?
  Pending hands-on analysis of GSE13699's Montreal vs. Lausanne data to see if pooling changes the result.
- How many of the charter's other 4 GEO accessions (GSE125921, GSE136163, GSE82152, GSE13699 covered above)
  have direct CD8 annotations vs. requiring marker-gene proxies? (Needs the same manual header inspection
  done for GSE13699.)
- Marker-gene proxy set: fixed panel (CD8A, CD8B, GZMB, PRF1, IFNG) vs. dataset-dependent, using whatever
  the dataset itself annotates?
- Do we build live `geo_search` before the event, or is the hardcoded candidate list sufficient for the MVP demo?
- BRC Analytics handoff: real integration during the event, or documented as a "next steps" architecture note?

## 8. Getting Started

The root [`pyproject.toml`](pyproject.toml) defines the required Python version and project dependencies.
Install [uv](https://docs.astral.sh/uv/getting-started/installation/) if it is not already available, then
run the following commands from the repository root:

```bash
# Install the exact Python version required by pyproject.toml.
uv python install 3.12.13

# Resolve the dependencies and install them into a new .venv directory.
# uv reads both the Python requirement and dependencies from pyproject.toml.
uv sync --python 3.12.13

# Activate the uv-managed environment for the current shell session.
source .venv/bin/activate
```

After activation, `python --version` should report `Python 3.12.13`. Run `deactivate` when you are
finished. 

## 9. Repo structure (proposed)

```
hypothesis2omics/
├── README.md
├── mcp_server/
│   ├── server.py              # FastMCP server exposing the 4 tools
│   ├── geo_tools.py           # geo_search, geo_fetch_summary, geo_fetch_matrix
│   └── analysis_tools.py      # correlate
├── agent/
│   ├── spec_builder.py        # hypothesis -> structured test spec
│   ├── eligibility.py         # per-dataset eligibility + confidence judgment
│   └── synthesis.py           # cross-dataset evidence table + narrative
├── data/
│   └── candidate_datasets.json # hardcoded GSE list from project charter
└── reports/
    └── evidence_table.md      # generated output
```
