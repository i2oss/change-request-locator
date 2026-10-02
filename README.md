# change-request-locator

A change request comes in; the locator lists every spot in the manual it touches: the direct spot plus its ripples, each with a citation and a one-line reason. It finds and explains. The writer decides and writes.

Portfolio prototype built only on public or made-up data.

- **Spec:** [SPEC.md](SPEC.md) (what gets built, and in what order)
- **Glossary:** [CONTEXT.md](CONTEXT.md) (the terms used throughout)

## Setup

```sh
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python corpus/faa/fetch.py        # downloads the FAA manual (not committed)
```

## FAA chunks (build step 1)

```sh
python parsers/faa_pdf.py                              # -> build/faa_chunks.jsonl
python scripts/check_chunks.py build/faa_chunks.jsonl  # contract gate -> build/unresolved_refs.md
python -m pytest
```

- `parsers/faa_pdf.py` turns AC 43.13-1B (w/ Change 1) into ~1,320 chunks: paragraphs, WARNING/CAUTION/NOTE blocks, tables and figure captions. Its docstring walks through the rules.
- `corpus/faa/ref_overrides.yaml` holds the hand-fixed relative and page references ("see page 4-19, f.").
- `build/faa_parse_stats.json` records what the parser skipped or placed inline.

## FAA index (build step 2)

```sh
python scripts/build_index.py build/faa_chunks.jsonl   # -> build/faa.sqlite (~1 min on a laptop CPU)
python -m pytest tests/test_build_index.py
```

- One SQLite file per corpus holds everything the locator searches: the chunks, lookup tables for `refs_out` / `specs` / `applies_to`, an FTS5 keyword index, and the embeddings. Copy it anywhere; plain `sqlite3` opens it.
- The `meta` table says what was searched (doc, chunk count, embedding model).
- The embedding model is set once, in `locator/config.py`. Changing it means rebuilding every index.
- The first build downloads the model (~130 MB) from Hugging Face; later builds use the local copy.

## Baseline locator (build step 2)

```sh
python -m locator "Flexible cable spec MIL-W-83420 is replaced by MRS-C-200." --db build/faa.sqlite
python -m pytest tests/test_search.py tests/test_tiers.py tests/test_locator_faa.py
```

- Rules and search only, no LLM: the bar the LLM steps must beat.
- It prints what was searched, what it read from the request (specs, refs, values, words), then the spots in two tiers, **Likely** and **Check these**. Each spot shows its citation, a "why" line and a snippet (`«»` marks what was found). There are no scores; retrieval scores aren't probabilities.
- `locator/search.py` reads the request and finds candidates:
  - exact lookups: specs the request names (any revision letter), and paragraphs/tables/figures it names (with their lettered parts and warnings)
  - keyword (FTS5) and meaning (embedding) search, merged by reciprocal rank fusion
  - ripples by plain lookup: chunks pointing at a hit, sharing a spec with it, repeating its warning, or splitting it by applicability
- `locator/tiers.py` sorts them into tiers and writes the "why" lines from templates.
- The knobs (how many spots to keep, where the tier cutoffs sit) are constants at the top of each file. Tune them on the eval's tune split only.
- The smoke tests use made-up requests only. Requests from the 2024 FAA edits are the eval set; never test or tune on them.

## Local app (build step 2)

```sh
flask --app app run                           # http://127.0.0.1:5000, against build/faa.sqlite
LOCATOR_DB=build/other.sqlite flask --app app run
flask --app app import-marks marks.jsonl      # load an export back in
python -m pytest tests/test_marks.py tests/test_app.py
```

- One page: paste a change request, see what was searched, then the spots in **Likely** and **Check these**. Each spot is labelled direct or ripple and shows its citation, "why" line and a highlighted snippet.
- Mark each spot **Confirm** or **Reject** (click again to clear), and **Add missed** spots by typing their paragraph, table or figure (`7-144`, `table 7-4`, `figure 7-9`).
- Marks save as you click, into two tables in the corpus SQLite file (`change_requests`, `marks`). A change request's id is a hash of its text, so pasting the same request again brings its marks back.
- **Export** downloads marks as JSONL (one request, or all). `import-marks` reads an export back in.
- Rebuilding the index keeps the marks: `scripts/build_index.py` copies them into the new file.
- The first search loads the embedding model (a few seconds); later ones take well under a second.
- Single writer, local only: no login and no multi-user support.

## Adding a format

Every manual format gets one parser; the locator only ever reads chunks.

1. Write `parsers/<format>.py` that reads a source file and writes one JSON chunk per line.
2. Each chunk must match [`schema/chunk.schema.json`](schema/chunk.schema.json):
   - required: `id`, `doc`, `loc` (`scheme`, `id`, `cite`), `heading_path`, `type` (`text` / `warning` / `table` / `figure`), `text`, `refs_out`
   - optional: `specs`, `applies_to`, `page`
   - anything else goes in `extra`, which the pipeline never reads
3. `refs_out` holds the `loc.id`s a chunk points to, in the same doc.
4. Run `scripts/check_chunks.py` on the output. It must pass: valid schema, unique ids, a type on every chunk, and every unresolved ref listed in the report for a person to review.
