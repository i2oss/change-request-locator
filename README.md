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

## Adding a format

Every manual format gets one parser; the locator only ever reads chunks.

1. Write `parsers/<format>.py` that reads a source file and writes one JSON chunk per line.
2. Each chunk must match [`schema/chunk.schema.json`](schema/chunk.schema.json):
   - required: `id`, `doc`, `loc` (`scheme`, `id`, `cite`), `heading_path`, `type` (`text` / `warning` / `table` / `figure`), `text`, `refs_out`
   - optional: `specs`, `applies_to`, `page`
   - anything else goes in `extra`, which the pipeline never reads
3. `refs_out` holds the `loc.id`s a chunk points to, in the same doc.
4. Run `scripts/check_chunks.py` on the output. It must pass: valid schema, unique ids, a type on every chunk, and every unresolved ref listed in the report for a person to review.
