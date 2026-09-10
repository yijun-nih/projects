# Judge Interface + MCP Server: Build Spec + Doc Updates

## 1. Why this exists

We do not have programmatic (API) access to an LLM right now — only browser
chat access to Claude and ChatGPT (claude.ai / chatgpt.com in a browser tab).
The original architecture assumed an in-context agentic loop (LLM calls MCP
tools directly, iteratively). The team's current browser-chat workflow cannot
spawn this repository's local stdio MCP server. A local MCP-capable host or a
separately deployed remote MCP server would be needed for live tool-calling, so
the current access constraint shapes two separate decisions below.

**Decision 1 (Judge/LLM-judgment calls): Option C.** Every place the pipeline
needs LLM *judgment* (not computation) goes through one fixed interface, the
`Judge`. Two implementations exist behind it:
- `ManualRelayJudge` — usable today. Writes a prompt to a file, a human pastes
  it into the Claude or ChatGPT browser chat window, pastes the reply back, it
  gets parsed and validated.
- `APIJudge` — drop-in once API keys exist. Same interface, no upstream code
  changes required.

**Decision 2 (MCP server for deterministic tools): build now, test standalone.**
The MCP server (`geo_fetch_summary`, `correlate`, etc.) can and should be built
now even though nothing can drive it interactively yet. It's tested with a
plain MCP test client / direct function calls, not a live LLM chat session.
Live interactive testing (an LLM actually calling these tools mid-conversation)
requires a compatible MCP host or remote deployment, neither of which is part
of the team's current workflow. When one becomes available, the same business
logic should work unchanged; transport/deployment configuration may differ.

Everything else in the pipeline (GEO fetch, metadata parsing, correlation) is
deterministic code and is unaffected by either decision.

---

## 2. What Codex should build

### 2.1 `agent/judge.py` — the interface and both implementations

```python
from abc import ABC, abstractmethod
from pathlib import Path
from datetime import datetime, timezone
import json

class JudgeError(Exception):
    """Raised when a judge cannot produce a schema-valid response."""

class Judge(ABC):
    @abstractmethod
    def ask(
        self,
        prompt: str,
        schema: dict,
        *,
        stage: str,
        prompt_version: str,
        max_retries: int = 2,
    ) -> dict:
        """
        Send `prompt` to the LLM, requesting a response matching `schema`
        (JSON Schema dict). Must validate the response before returning.
        Raises JudgeError if validation fails after retries.
        Must log every prompt/response pair (see provenance requirements, 2.3).
        """

class ManualRelayJudge(Judge):
    """
    File-based relay for browser-only LLM access.

    Workflow per call:
      1. Write prompt + schema + instructions to `pending/<call_id>_prompt.md`
      2. Block on input() until the operator confirms that they saved
         `pending/<call_id>_response_0.json`
      3. Load, validate against schema (jsonschema or pydantic)
      4. On invalid: write an attempt-specific correction prompt and wait for
         `pending/<call_id>_response_<attempt>.json`, up to max_retries
      5. Append one call record containing every attempt to
         provenance/judgments.jsonl, including terminal failures
    """
    def __init__(
        self,
        pending_dir: str = "pending",
        provenance_log: str = "provenance/judgments.jsonl",
        model: str = "browser-manual-unspecified",
    ):
        ...

class APIJudge(Judge):
    """
    Direct API call. Same signature and same provenance logging as
    ManualRelayJudge, with judge_type: "api" and model name/version recorded.
    Not implemented until API keys are available — stub with NotImplementedError
    for now, matching the Judge interface exactly so it's a true drop-in.
    """
    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        provenance_log: str = "provenance/judgments.jsonl",
    ):
        ...
```

Requirements:
- `ask()` signature must be identical across both classes — nothing upstream
  should ever branch on which Judge is active. `stage` is required so the Judge,
  which owns provenance logging, can produce a complete provenance record.
- `prompt_version` is required because the rendered prompt alone cannot identify
  which versioned template produced it. Each schema must declare `$id` and
  `x-version`; the Judge records those values and rejects schemas missing them.
- Every call, regardless of implementation, is logged to
  `provenance/judgments.jsonl` in the same format (this feeds Section 9's
  reasoning log, and keeps manual and API judgments equally auditable).
- Schema validation must actually reject malformed responses, not just log a
  warning — re-prompt on failure rather than silently passing through.
- `max_retries` means retries after the initial attempt. For example,
  `max_retries=2` permits at most three attempts. A negative value is invalid.
- Manual responses must contain one bare JSON object. Markdown fences,
  commentary around the object, arrays, and partially valid JSON are rejected.
- Every attempt, including invalid responses and terminal failures, must be
  recorded. Retrying must never overwrite evidence from an earlier attempt.
- Config (which Judge to use) should be one switch, e.g. an env var or a single
  line in a config file: `JUDGE_IMPL=manual_relay` vs `JUDGE_IMPL=api`.

### 2.2 Three call sites, with schema + prompt template each

These map onto architecture.md stages 2, 5, and 8. Each needs its own prompt
template (versioned, plain text/markdown file under `prompts/`) and JSON Schema
(under `schemas/`).

**`build_hypothesis_spec(hypothesis: str) -> dict`** (Stage 2)
Output schema: `{gene, aliases: [], outcome, context, species}`

**`judge_eligibility(dataset_metadata: dict, spec: dict) -> dict`** (Stage 5)
Input: the parsed metadata contract produced after artifact fetching: title,
summary, design, platforms, sample characteristics, and deterministic anomaly
flags. The current fetch module downloads and inventories artifacts but does
not yet produce this contract; metadata extraction is a separate increment.
Output schema:
```
{
  gse_id: str,
  eligible: bool,
  cd8_signal_type: "direct" | "proxy" | "none",
  confidence: float,          # 0-1
  needs_splitting: bool,      # e.g. GSE13699's Montreal/Lausanne case
  split_rationale: str | null,
  exclusion_reason: str | null,
  rationale: str              # free text, goes straight into the reasoning log
}
```
This is the one that has to interpret the GSE13699 edge cases documented in
README.md Section 5. Deterministic code first detects multi-cohort/platform
structure, inconsistent labels, non-independent covariates, and anomalies such
as the YF21 age conflict. The resulting structured flags are surfaced in the
prompt so the judge can assess eligibility and splitting without being asked to
discover mechanical inconsistencies from prose.

**`synthesize_evidence(dataset_results: list[dict], spec: dict) -> dict`** (Stage 8)
Output schema: `{overall_verdict, per_dataset, narrative}`, where the verdict is
`"supportive"`, `"contradictory"`, or `"inconclusive"`.

Each `per_dataset` item represents one analysis unit, not necessarily one GEO
accession. It contains `analysis_unit_id`, `gse_id`, nullable `cohort` and
`platform`, `verdict`, `confidence`, `evidence_summary`, `rationale`, and
`caveats`. This permits GSE13699's cohorts/platforms to remain separate without
forcing every accession to split. `analysis_unit_id` must match the stable ID in
the deterministic Stage 7 result supplied to synthesis.

The synthesis judge must use only the supplied deterministic results. It must
not invent missing effect sizes, p-values, sample sizes, eligibility rules, or
statistical thresholds. When supplied results are insufficient to determine a
direction, it must return `inconclusive`. The exact statistical analysis policy
remains a separate Stage 7 decision and is not encoded in the Judge schemas.

### 2.3 Provenance format (single source of truth for all three call sites)

```json
{
  "call_id": "uuid",
  "stage": "eligibility | hypothesis_spec | synthesis",
  "judge_type": "manual_relay | api",
  "model": "claude-web-manual | ...",
  "prompt": "fully rendered prompt",
  "prompt_version": "1.0.0",
  "schema": {},
  "schema_id": "https://hypothesis2omics.local/schemas/eligibility.schema.json",
  "schema_version": "1.0.0",
  "attempts": [
    {
      "attempt": 0,
      "raw_response": "...",
      "validation_errors": [],
      "correction_prompt": null,
      "timestamp_utc": "..."
    }
  ],
  "validated_response": {},
  "status": "success | failed",
  "error": null,
  "timestamp_utc": "...",
  "retries": 0
}
```

`retries` is the number of retries actually consumed, so it is always one less
than the number of `attempts`. `validated_response` is null when `status` is
`failed`; `error` is non-null for failed calls. Prompt and schema versions are
recorded alongside their full contents so later reports can identify both the
declared contract and the exact material presented to the model. The complete
contract is `schemas/judgment_provenance.schema.json`.

Judgments remain in `provenance/judgments.jsonl`; deterministic fetch events
remain in the existing GEO provenance log. A later report layer reads both logs
and joins them through stable dataset/analysis-unit identifiers. Merging unlike
event payloads into one JSONL file is out of scope until a common event envelope
is designed.

### 2.4 `mcp_server/` — build now, test standalone (no live LLM client required)

Wrap the existing fetch module as a FastMCP tool. This is buildable and
testable today with zero LLM access of any kind:

```python
# mcp_server/server.py
from fastmcp import FastMCP
from mcp_server.geo_tools import fetch_geo_datasets

mcp = FastMCP(name="hypothesis2omics")

@mcp.tool()
def geo_fetch_artifacts(gse_ids: list[str]) -> dict:
    """Fetch GEO family SOFT + series-matrix artifacts and return per-accession
    status, platform/sample counts, and provenance. Thin wrapper around
    fetch_geo_datasets() from geo_tools.py."""
    records = fetch_geo_datasets(gse_ids)
    return {"results": [r.to_dict() for r in records]}

# Add geo_fetch_summary after metadata extraction is implemented. Register
# geo_search and correlate only after they are implemented and tested.

if __name__ == "__main__":
    mcp.run(transport="stdio")
```

**How to test this without any LLM client:**
- Call the underlying Python functions directly in a test script — this
  validates business logic and is the primary test path.
- Use FastMCP's in-memory `Client` as the automated protocol test. Optionally run
  `fastmcp dev mcp_server/server.py` for interactive MCP Inspector testing.
- Do not use the team's current browser-chat workflow as the MCP test path: it
  cannot spawn this local stdio server. Live interactive testing requires a
  compatible local MCP host or a deployed remote MCP endpoint. Build and
  validate locally now; transport/deployment wiring is a later increment.

`mcp_server/geo_tools.py` is `geo-fetch-module.py`, relocated and lightly
adapted: strip or isolate the `argparse`/`main()` CLI entry point (kept only as
a `if __name__ == "__main__"` dev convenience, not part of the MCP-exposed
surface), and import `fetch_geo_datasets`/`fetch_one` into `server.py` as shown
above. Codex should not rewrite the fetch logic itself — it is complete and
tested; only the wrapping/import structure changes.

### 2.5 Full file layout

```
hypothesis2omics/
├── README.md
├── architecture.md
├── agent/
│   ├── judge.py                # Judge, ManualRelayJudge, APIJudge
│   ├── spec_builder.py         # calls judge.ask() for Stage 2
│   ├── eligibility.py          # calls judge.ask() for Stage 5
│   └── synthesis.py            # calls judge.ask() for Stage 8
├── mcp_server/
│   ├── server.py               # FastMCP server; tool definitions
│   ├── geo_tools.py            # geo-fetch-module.py, relocated
│   └── analysis_tools.py       # correlate (not yet built)
├── prompts/
│   ├── hypothesis_spec.md
│   ├── eligibility.md
│   └── synthesis.md
├── schemas/
│   ├── hypothesis_spec.schema.json
│   ├── eligibility.schema.json
│   ├── synthesis.schema.json
│   └── judgment_provenance.schema.json
├── pending/                     # manual relay working directory (gitignored)
├── provenance/
│   └── judgments.jsonl
├── data/
│   └── geo_cache/               # fetch_geo_datasets() output; already exists
└── reports/
    └── evidence_table.md
```

---

## 3. Updates to apply to README.md

**Section 2 ("Design principle: code decides mechanics, the agent decides
judgment")** — add a subsection:

> **LLM access note (added [date]):** During pipeline development we have
> browser-only access to Claude/ChatGPT, not API access. All LLM judgment calls
> therefore go through a single `Judge` interface (see `agent/judge.py`) with
> two interchangeable implementations: `ManualRelayJudge` (human pastes
> prompts/responses between this pipeline and the browser chat window) and
> `APIJudge` (direct API call, pending credentials — see Codeathon-provided LLM
> access in the project charter). No other code depends on which is active. If
> API access is confirmed before the event, switch via `JUDGE_IMPL=api` with no
> changes to `eligibility.py`, `spec_builder.py`, or `synthesis.py`.
>
> Separately, the MCP server (Section 4) is built and tested standalone now,
> independent of this constraint — it's validated via direct function calls and
> the MCP SDK's own dev/test client, not via a live LLM session. Live
> interactive tool-calling (an LLM actually invoking `geo_fetch_summary` etc.
> mid-conversation) requires a compatible local MCP host or a deployed remote
> endpoint. Neither is part of the team's current workflow; transport and
> deployment wiring can be added later without changing the underlying tools.

**Section 4 ("Tools needed")** — add a row/subsection for the Judge interface
alongside the MCP server tools table, noting it's the LLM-facing counterpart to
the deterministic tools already listed. Document `geo_fetch_artifacts` as the
completed download/inventory operation; mark `geo_fetch_summary` complete only
after the metadata extraction contract is implemented.

**Section 9 (repo structure)** — replace with the updated tree from Section 2.5
above.

**Section 7 (open questions)** — add:
> - Will Codeathon-provided LLM access include API-level (not just chat UI or
>   Claude Desktop) access? Pending confirmation from organizers. Determines
>   whether `ManualRelayJudge` remains necessary during the event itself or is
>   only needed for pre-event development, and when live MCP tool-calling
>   testing becomes possible.

---

## 4. Updates to apply to architecture.md

In the Mermaid diagram, stages 2 ("Hypothesis Specification"), 5 ("Eligibility
Assessment"), and 8 ("Cross-Dataset Synthesis") are currently labeled `(LLM)`.
Relabel and annotate:

```mermaid
subgraph SPEC["2. Hypothesis Specification (LLM via Judge interface)"]
    S["Structured hypothesis:<br/>gene=EIF2AK4, aliases=[GCN2]<br/>
    outcome=CD8 T cell response<br/>context=YF-17D vaccination<br/>
    species=Homo sapiens<br/><br/>[calls: judge.ask(), schema-validated]"]
end
```

Same annotation pattern (`via Judge interface`, note on schema validation) for
the ELIGIBILITY and SYNTH subgraphs.

Add a new small subgraph near the diagram (cross-cutting, not part of stage
order):

```mermaid
subgraph JUDGE["Judge Interface (cross-cutting)"]
    J1["ManualRelayJudge\n(browser chat relay,\nactive now)"]
    J2["APIJudge\n(direct API call,\npending credentials)"]
    J1 -.same interface.-> J2
end
```

Add a caption under the diagram:
> All LLM-judgment stages (2, 5, 8) call through a single `Judge` interface;
> the underlying implementation (manual browser relay vs. direct API) is
> swappable without changing pipeline code. The MCP server exposing
> deterministic tools (stages 3, 4, 7) is built and tested standalone via
> direct calls and FastMCP's in-memory client; live LLM tool-calling against it
> requires a compatible local MCP host or a deployed remote endpoint.

---

## 5. What Codex should NOT do

- Don't build any MCP wiring for the Judge itself — Judge is a plain Python
  interface, not an MCP tool.
- Don't add remote deployment or browser-product connector configuration in
  this build. The approved scope is a local stdio server with standalone tests.
- Don't hardcode a specific LLM's response format assumptions into the schema
  validation — validation should be schema-driven (jsonschema/pydantic) so it
  works identically whether the text came from Claude, ChatGPT, or a future
  API call.
- Don't skip provenance logging for manual relay calls on the theory that
  they're "just for dev" — these judgments will likely be the ones actually
  used for the MVP demo if API access doesn't come through in time.
- Don't rewrite `geo-fetch-module.py`'s core fetch logic when relocating it to
  `mcp_server/geo_tools.py` — only adapt the entry point/import structure.
