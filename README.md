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

## Status

Build step 1 (SPEC §9): FAA PDF parser + chunk contract check — in progress.
