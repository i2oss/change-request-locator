# Change-Request Locator: prototype spec

**Status:** final · **Date:** 2026-09-28 · **Planning map:** [Portfolio RAG prototype — pick the fit and spec it](https://github.com/i2oss/wayfinder-rag-portfolio/issues/1)

A change request comes in; the locator lists every spot in the manual it touches, the **direct spot** plus its **ripples**, each with a citation and a one-line reason. It finds and explains. The writer decides and writes.

Built entirely on public or made-up data. Every decision below links to the ticket that holds its reasoning; this spec restates the *what*, not the full *why*. Terms in **bold** follow the glossary in `CONTEXT.md`.

---

## 1. Goals and non-goals

**Goals**
- A working locator on a real public manual (FAA AC 43.13-1B), scored honestly against a real answer key.
- A public demo page on a made-up S1000D manual that shows S1000D skill and the human-in-the-loop design.
- A write-up that reports **baseline** vs. +LLM results, and a one-page "how this carries over to GA" section (§10).

**Non-goals** (ruled out on the map)
- Suggesting wording, drafting edits, or producing diffs. The tool points; it never edits.
- Customer rule lookup. It's named only as a GA next step.
- Scripting the Arbortext save-as / compose / PDF page-pull chain.
- Any real GA data, documents, or DevTrack history. No customer names anywhere.

## 2. Human-in-the-loop rules

These are design requirements, not preferences ([Where human judgment stays](https://github.com/i2oss/wayfinder-rag-portfolio/issues/5)).

- **Point only.** Each spot shows a citation, a highlighted snippet, and a one-line "why this spot". Nothing else.
- **Two tiers, no scores.** Spots are grouped into **Likely** and **Check these**, and each is labelled direct or ripple. The tool leans toward recall, because missing a ripple costs more than skimming an extra spot. No numeric scores, since retrieval scores aren't probabilities.
- **Say what was searched**, e.g. "AC 43.13-1B w/ Change 1, 1,214 chunks". That way an empty or short list has a known scope.
- **The writer marks every spot:** confirm, reject, or add-missed. **Marks** are saved per change request. They are the writer's record, and later they become eval data.

## 3. Data

Two manual sets share one chunk schema ([Pick the locator corpus](https://github.com/i2oss/wayfinder-rag-portfolio/issues/6), [Public stand-in data](https://github.com/i2oss/wayfinder-rag-portfolio/issues/2)).

### 3.1 FAA set: the honest eval

- **Indexed manual:** `AC_43.13-1B_w-chg1.pdf`, the 1998 manual with Change 1 merged (646 pp, two-column). [Source](https://www.faa.gov/documentLibrary/media/Advisory_Circular/AC_43.13-1B_w-chg1.pdf).
- **Source of changes only (never searched):** the May 2024 Editorial Update, `AC_43.13-1B_CHG_1_Ed_Upd_FAA.pdf` (756 pp, 400 change bars). [Source](https://www.faa.gov/documentLibrary/media/Advisory_Circular/AC_43.13-1B_CHG_1_Ed_Upd_FAA.pdf).
- Avoid `AC_43.13-1B_Full.pdf`, which contains a duplicate chapter 5.
- **Licensing:** a US government work. Use the text only, since some images are credited to third parties. A fetch script downloads the PDFs; they are not committed.

### 3.2 Demo corpus: MERM100

Designed in [Design the made-up S1000D project](https://github.com/i2oss/wayfinder-rag-portfolio/issues/16).

- **Product:** the fictional Meridian M100 (model ident `MERM100`), covering landing gear/brakes (ATA 32) and hydraulics (ATA 29) only.
- **SNS:** 29-10 main hydraulic · 29-20 hand pump/reservoir · 32-10 main gear · 32-20 nose gear · 32-30 extension/retraction · 32-40 wheels & brakes · 32-50 steering.
- **Size:** ~40 data modules, ~600–900 chunks, all at issue 001.
- **Info-code mix:** 040 descriptions, 200/300 servicing and inspection, 520/720 remove and install, 4xx fault isolation, 941 IPD, 012 warnings/cautions.
- **Schema:** s1000d-mcp's subset XSD, extended with Issue 6 element names: `warning`/`caution`/`note`, CALS `table`, `illustratedPartsCatalog`/`catalogSeqNumber`, `faultIsolation`, `commonInfo`, `warningRef`. The XSD extension is contributed to [s1000d-mcp](https://github.com/i2oss/s1000d-mcp).
- **No BREX.** Business rules belong to customer rule lookup, which is out of scope.
- **Facts sheet** (`corpus/merm100/facts.yaml`, public): the single source for every value, spec, part number, and warning, each with an id. It also generates the demo answer keys.
- **Warnings:** mostly inline, so a repeated warning is a text match. A few use a CIR `warningRef`, which sets up the GA single-sourcing line.
- **Applicability:** one ACT attribute, `landingGear = Fixed | Retractable`. A handful of modules split on it.
- **Figures:** placeholder ICNs with titles and callout legends, no artwork.
- **Manual first.** Write it like a real manual from the facts sheet. Add a **plant** only where a Replay behaviour is missing, and disclose every plant in the facts sheet.
- **Authoring:** Claude drafts; Ross reviews in two batches:
  1. The facts sheet plus one 040, one 520, and the 941 IPD, to set the style.
  2. The rest, which Ross spot-checks. Ross reads every warning.
- **CI:** each module must pass `validate_xml_schema` + `check_cross_references` (s1000d-mcp, a dev dependency).

## 4. Chunk schema and parsers

Format-agnostic from the start ([Make the chunk schema format-agnostic](https://github.com/i2oss/wayfinder-rag-portfolio/issues/14)). A field is named only if the locator's logic reads it.

### 4.1 Chunk record

| Level | Field | Meaning |
|---|---|---|
| Required | `id` | Unique chunk id |
| | `doc` | Source document and revision |
| | `loc.scheme` | e.g. `faa-para`, `s1000d` |
| | `loc.id` | Canonical location, unique in the doc, e.g. `4-47` or `DMC-MERM100-A-32-40-00-00A-520A-A#step-3` |
| | `loc.cite` | Human citation shown on a spot, e.g. "AC 43.13-1B ¶4-47, p. 4-21" |
| | `heading_path` | Breadcrumb of headings |
| | `type` | `text` / `warning` (incl. caution, note) / `table` / `figure` |
| | `text` | Chunk text |
| | `refs_out` | `loc.id`s this chunk points to (may be empty) |
| Named optional | `specs` | Spec, standard, and part numbers (the strongest ripple signal) |
| | `applies_to` | Applicability; empty = all variants. Drives the *Check these* catch |
| | `page` | Printed page |
| `extra` | anything | Never read by the pipeline (DMC parts, info code, …) |

### 4.2 Parser contract

- A parser takes a source file and writes chunk records as **JSONL**, which must validate against `schema/chunk.schema.json`.
- `scripts/check_chunks.py` gates every parser:
  - every chunk matches the schema
  - every `id` is unique
  - every `refs_out` resolves or is listed in an unresolved report (never dropped silently)
  - every chunk has a `type`
- Only two parsers are built. A short "Adding a format" section in the repo README explains how to add more.

### 4.3 FAA PDF parser

Findings: [Parse the FAA PDF into tagged chunks](https://github.com/i2oss/wayfinder-rag-portfolio/issues/8), [research/faa-pdf-chunking.md](research/faa-pdf-chunking.md).

- **PyMuPDF** as the primary tool, **pdfplumber** for tables. No OCR: 643 of 646 pages have a text layer and the other 3 are blank.
- **Boundaries:** font rules on the bold paragraph numbers.
- **Clean-up:** drop the 9 duplicate pages, fix the U+F8E7 dashes, and key chunks by (pdf_page, para_id).
- **`type`:** a regex on the bold WARNING/CAUTION/NOTE lead-ins.
- **`refs_out`:** a regex covers ~600 paragraph/figure/table references. About 20 relative or page references are fixed by hand in a small override file.
- **`specs`:** the ~1,141 MIL/AN/MS/… numbers.
- **Out of scope:** row-by-row table splitting and figure-callout linking. Revisit only if the eval shows tables or figures being missed.
- **Expected output:** ~1,150–1,300 chunks.

### 4.4 S1000D XML parser

Approach: [RAG over structured XML/SGML](https://github.com/i2oss/wayfinder-rag-portfolio/issues/3).

- Split along S1000D structure: para, step, warning/caution/note, table, IPD `catalogSeqNumber` row, figure.
- `loc.id` = DMC + `#` + element id. `refs_out` comes from `dmRef`/`internalRef`/`warningRef`. `applies_to` comes from the applicability annotations.
- DMC parts and the info code go in `extra`.

## 5. Locator pipeline

Stack decided in [Pick the tech stack](https://github.com/i2oss/wayfinder-rag-portfolio/issues/12).

### 5.1 Stack

- **Python + Flask** for the local app.
- **One SQLite file** per corpus, holding the chunks, an FTS5 keyword index (BM25), and the embeddings. Nothing to host; it copies to an air-gapped machine as-is.
- **Embeddings:** local `bge-small-en-v1.5` via sentence-transformers, searched with numpy cosine similarity. Step up to `bge-base-en-v1.5` only if the eval shows vector-side misses.
- **LLM:** hosted Claude through the `anthropic` SDK, kept behind one module, `locator/llm.py`, which has two functions (§5.3).

### 5.2 Flow

1. **Read the request.** The locator reads only the change request `text`.
   - Baseline: regex pulls spec numbers, values, and paragraph/table/figure refs.
   - +LLM: `parse_request(text)` returns the same fields plus rephrasings.
2. **Search.**
   - Exact lookup on `specs` and `loc.id`/`refs_out`.
   - FTS5 keyword search and vector search, merged by reciprocal rank fusion. Exact hits are always kept.
3. **Expand ripples** (plain lookup, no AI):
   - chunks whose `refs_out` point at a hit
   - chunks sharing a `specs` value with a hit
   - warnings whose text repeats a hit warning
   - applicability splits of a hit (`applies_to`)
4. **Tier and explain.**
   - Baseline: tiers from rank cutoffs plus direct/ripple provenance; "why" lines from templates ("same spec MIL-W-5088", "points to 11-48").
   - +LLM: `sort_and_explain(request, candidates)` places each of the top ~40 candidates in **Likely** or **Check these** and writes its "why" line. It never drafts wording.
5. **Show and mark.** The spots appear with the search scope. The writer marks each spot, and the marks are saved to a `marks` table keyed by change request.

### 5.3 LLM module

- `parse_request(text) -> {specs, values, refs, terms}`
- `sort_and_explain(request, candidates) -> [{chunk_id, tier, why}]`
- **Models:** tune on Claude Opus 5 (`claude-opus-5`), then run the final eval once on Claude Sonnet 5 (`claude-sonnet-5`) and report both. The model id is a config value.
- **GA swap:** only this module changes (§10).

### 5.4 Local app

A Flask page that works like the demo page but runs live:
- paste a request
- see the tiered spots with their citations and snippets
- mark each spot
- export marks as JSONL

No login and no multi-user support; it's a single-writer local tool.

## 6. Evaluation

The answer key is set in [Choose the eval answer key](https://github.com/i2oss/wayfinder-rag-portfolio/issues/11); the change-bar facts come from [Do the 2024 change bars mark Change 1?](https://github.com/i2oss/wayfinder-rag-portfolio/issues/10).

### 6.1 Change requests (~40)

**Real requests (~30):**
- Take FAA's w-chg1 → 2024 edits, **grouped by what changed**, not by paragraph. For example, "MIL-W-5088 superseded by AS50881" is one request.
- Drop typo and house-style fixes.
- Word each request the way it would land on a writer's desk.
- Keep renumbering families as ripple tests.

**Made-up requests (~10)** ([Plan the made-up change requests](https://github.com/i2oss/wayfinder-rag-portfolio/issues/17)):
- The spread:
  - 2 value/limit changes
  - 2 WARNING/CAUTION changes
  - 2 procedure-step changes
  - 1 table row
  - 1 retired part or tool
  - 1 figure callout
  - 1 applicability change
- Chapters outside 11, at most 2 per chapter.
- At least half are chosen because their target paragraph has natural ripples.

**Writing:** Claude drafts each request; Ross rewrites it in intake voice. Requests carry no hints that help the locator.

**File:** `eval/faa/requests.yaml`, one entry per request with `id`, `text`, `source` (`faa-2024` / `made-up`), `why`, and `split` (`tune` / `holdout`, see §6.4).

### 6.2 Answer key

**Direct spots:**
- For real requests: the 2024 change bars plus a text diff for unbarred edits, mapped back to w-chg1 paragraph ids and pages (760 of 761 paragraphs match).
- For made-up requests: written by Ross.

**Ripple spots:**
- A script proposes candidates from `specs`, `refs_out`, and repeated warning text.
- Ross confirms or rejects each candidate and adds misses, in the same mark format as the tool.
- The candidate script shares logic with the locator; Ross's added misses keep the eval from grading the tool against itself. The write-up says so.

**File:** `eval/faa/key.yaml`.

### 6.3 Scoring and runs

**Reported per run** (no combined score):
- **Direct recall:** the direct spot is in *Likely*.
- **Ripple recall:** confirmed ripples surfaced in either tier.
- ***Check these* length:** the noise measure.

**Runs:**
1. The baseline first, on the tune split.
2. +LLM on Opus 5 during tuning, on the tune split only.
3. The final runs on both splits: baseline, +LLM on Opus 5, and +LLM on Sonnet 5 (see §6.4).

**Cost:** about $5–10 per Opus eval run and $2–4 per Sonnet run. Tuning totals roughly $100–200. Embeddings and storage cost $0.

**Output:** `eval/results/<run-id>.json` plus a short markdown table for the write-up.

### 6.4 Holdout set

Tuning prompts, rank cutoffs, and "why" templates on the same requests they're scored on makes the numbers look better than they are. So ~10 of the ~40 requests are held out.

**How the split is made:**
- The split is fixed in `requests.yaml` **before the first baseline run**, and never changed after.
- It's stratified, not random: the holdout mirrors the full set. That means about 7 real and 3 made-up requests, at least 2 spec swaps, at least 1 renumbering family, and at least 3 requests with 3+ confirmed ripples.
- Claude proposes the split; Ross approves it.

**While building:**
- `eval/run.py` skips holdout requests unless it's given `--final`.
- Nobody reads holdout results or answer-key hits while tuning. The key itself is written and checked for all ~40 requests up front, so it can't be shaped by results.

**The final run:**
- `--final` runs baseline, Opus 5, and Sonnet 5 on both splits. It's done once, after tuning stops.
- If a bug forces a re-run, the write-up says so and why.

**Reporting:**
- Report tune and holdout numbers side by side. The gap between them is the overfitting check.
- **Headline numbers use the holdout** (the eval strip on the demo page and the §10 slots), with n stated.
- Holdout numbers from ~10 requests are coarse, so percentages are shown with their counts (e.g. "9 of 11 direct spots").

## 7. Demo page

Decided in [Choose the demo format](https://github.com/i2oss/wayfinder-rag-portfolio/issues/13).

**Replays.** It's a cached replay: nothing runs live, there's no API key, and visits cost $0. There are 6–8 **Replays** of made-up requests against MERM100, each showing one behaviour. Expected set (revised once the corpus exists):

| Behaviour | Sketch |
|---|---|
| Direct spot only | Steering limit stated once in the 32-50 description |
| Spec-number ripple | Hydraulic fluid spec MRS-H-101 → MRS-H-120: 29-10 servicing, 29-20 reservoir, IPD consumables |
| Repeated warning | "Release hydraulic pressure" warning across 520/720 modules |
| Table / parts list | Brake assembly part number superseded: 941 IPD row + 32-40 remove/install |
| Cross-ref chain | Gear retraction test changes: every "do the test in …" module |
| *Check these* catch | Tire pressure differs for Fixed vs. Retractable |
| **Honest miss** | Picked from the real demo run after the fact, never planted. If nothing is missed, say so and show the closest *Check these*-only catch |

**On the page:**
- Tiers, direct/ripple labels, and "why" lines.
- A **`baseline | +LLM` toggle** per Replay, with both results saved from real runs.
- Marks that work in the open page only.
- A **Show answer key** button that compares the visitor's marks with the key.
- An eval strip showing FAA holdout numbers only (§6.4): direct recall, ripple recall, and *Check these* length for baseline vs. +LLM (Opus 5 / Sonnet 5), linking to the write-up.

**Demo requests and keys:**
- `eval/demo/requests.yaml` adds a `behaviour` field and the facts-sheet ids each request changes.
- Keys are generated from the facts sheet (modules using the changed fact ids, plus cross-refs to them), then Ross confirms them and adds meaning-only ripples.
- Demo Replays are **not scored**.

**Look:**
- Terminal style: `$ locate "…"` prompt line, tiered output blocks with `[✓] [✗]` controls, citation/snippet pane.
- One stylesheet with one token block (`--bg`, `--ink`, `--accent`, `--term`; IBM Plex Mono / Inter / Space Grotesk).
- Meaning-based classes (`.spot`, `.tier-likely`, `.ripple`); no hard-coded colours.

**Hosting:**
- The static page lives in `demo/`, with Replay data exported to `demo/replays/*.json`.
- It's a second Netlify site at `locator.rwshelton.com`, with a card in the rwshelton.com Projects section.

## 8. Repo layout

The locator lives in its own public repo. Its name is picked at build time.

```
corpus/
  faa/fetch.py                # downloads the two FAA PDFs (not committed)
  faa/ref_overrides.yaml      # ~20 hand-fixed refs
  merm100/facts.yaml          # facts sheet
  merm100/dm/*.xml            # ~40 data modules
schema/chunk.schema.json
parsers/faa_pdf.py
parsers/s1000d_xml.py
scripts/check_chunks.py       # parser contract gate
scripts/build_index.py        # JSONL -> SQLite (chunks, FTS5, embeddings)
scripts/ripple_candidates.py  # answer-key candidates
scripts/export_replays.py
locator/search.py             # exact + FTS5 + vector + RRF + ripple expansion
locator/tiers.py              # baseline tiers and "why" templates
locator/llm.py                # parse_request, sort_and_explain
app/                          # Flask local app
eval/faa/{requests,key}.yaml
eval/demo/{requests,key}.yaml
eval/run.py
eval/results/
demo/                         # static page for Netlify
.github/workflows/ci.yml      # XSD + cross-ref check on merm100, chunk contract
```

## 9. Build order

Each step ends in something Ross can check. The FAA track (1–5) and the demo-corpus track (6) can run in parallel.

1. **FAA parser + contract check.** Done when there are ~1,150–1,300 chunks, the contract check is green, and the unresolved-ref report is reviewed.
2. **Index + baseline locator.** SQLite build, search, ripple expansion, baseline tiers, and the Flask app.
3. **FAA eval set.** Claude drafts ~40 requests and a ripple candidate list; Ross rewrites the requests and marks the candidates. This is the biggest block of Ross's time: roughly a 2–3 hour pass over the 137 barred paragraphs, plus the ripple marking. It ends with the tune/holdout split being fixed (§6.4).
4. **Baseline eval run** on the tune split. The first real numbers.
5. **LLM module + tuning** on the tune split, then the one `--final` run: baseline, Opus 5, and Sonnet 5 on both splits.
6. **MERM100 corpus.** XSD extension, facts sheet, review batch 1, then the rest; CI green; XML parser passes the contract.
7. **Demo Replays.** Draft the requests, generate keys, run baseline and +LLM, pick the honest miss, export.
8. **Demo page + deploy.**
9. **Write-up.** Methods, results table, limits, and the §10 section with its slots filled.

## 10. How this carries over to GA

Scoped in [Scope the GA carryover section](https://github.com/i2oss/wayfinder-rag-portfolio/issues/20). About one page, written for a team lead, in plain language. It holds no customer names and no GA data. Draft text follows; the `{…}` slots are filled after step 9.

> **What it does.** When a change request arrives, the locator lists every place in the manual it touches: the paragraph that changes, plus the cross-references, repeated warnings, tables, and parts-list entries that change with it. The writer confirms or rejects each spot and adds anything it missed. It never writes or edits text.
>
> **What the prototype showed.** On a public 646-page FAA manual, tested against {n} real and realistic change requests held back until tuning was finished:
> - Without any AI model, it found the changed paragraph {baseline direct recall} of the time and {baseline ripple recall} of the knock-on spots.
> - With a language model sorting and explaining results, those rose to {LLM direct recall} and {LLM ripple recall}, at about {cost per request} per request.
>
> **Running it inside GA.**
> - The search runs entirely on one machine: a single database file and a small local model, with nothing to install on a server. It can run air-gapped.
> - No manual text leaves the network.
> - The optional language-model step is one small module. It moves to whatever model GA has approved (on-prem or accredited cloud) without touching the rest.
> - If no model is approved, the no-AI version already works, at the scores above.
> - The writer's confirm/reject choices are kept per change request, as a record of what was checked.
> - The prototype has never touched GA data. Accreditation belongs to IT/security.
>
> **Where it fits.** At intake and scoping, before any editing. It reads data modules exported from the CSDB using the same S1000D reader the demo uses. It never writes back to the CSDB or to tickets.
>
> **Next steps, in order.**
> 1. **Intake adapters:** turn a spreadsheet row or ticket straight into a request, with no copy-paste.
> 2. **Customer rule lookup:** answer "what does this customer require here?" with citations.
> 3. **Other teams' manuals:** any format plugs in by writing one reader that meets the published chunk contract.
>
> **The ask.** A pilot: one writer, one manual, four weeks, on an approved machine. Measure the time to scope each change request with and without the tool, and the spots the writer had to add that it missed.

## 11. Open items

**To settle during the build** (none of these block starting):
- The locator repo name.
- Final change-request texts and the Replay list. Rules are fixed; the texts are written at build time.
- Real-request counts. The ~50–80 usable edits come from a rough automatic classifier and firm up in step 3.
- Whether to step up to `bge-base`, row-split tables, or link figure callouts. Decide only if the eval shows misses there.

**Phase 2** (never blocks the spec): a live demo mode via a Netlify Function, intake adapters, rule lookup, and more parsers.
