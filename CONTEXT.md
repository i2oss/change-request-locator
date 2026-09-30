# Change-Request Locator

A tool that points a technical writer at every place in a manual a change request touches. It finds and explains; the writer decides and writes.

## Language

### Locating

**Change request**:
One requested edit to a manual, stated in plain text by whoever asked for it.
_Avoid_: ticket, CR line, task

**Chunk**:
One searchable piece of a manual (a paragraph, warning, table, or figure) with its location and the places it points to.
_Avoid_: passage, segment, node

**Spot**:
One place in the manual the locator says a change request may touch, with its citation, snippet, and a one-line reason.
_Avoid_: hit, result, match, location

**Direct spot**:
A spot where the change request's subject is itself stated.
_Avoid_: primary hit, main spot

**Ripple**:
A spot that must change because a direct spot changes: a cross-ref, repeated warning, table, or parts list.
_Avoid_: side effect, secondary hit, related spot

**Likely**:
The tier of spots the locator is confident the writer must look at.
_Avoid_: high confidence, top results

**Check these**:
The tier of lower-ranked but plausible spots, shown because missing a ripple costs more than skimming an extra spot.
_Avoid_: low confidence, maybes, other results

**Mark**:
The writer's confirm, reject, or add-missed verdict on one spot.
_Avoid_: rating, feedback, label

### Measuring

**Answer key**:
The known correct spots for a change request, used to score the locator.
_Avoid_: ground truth, gold set

**Baseline**:
The locator with rules and search only, no LLM; the bar the LLM steps must beat.
_Avoid_: control, non-AI version

### Showing

**Replay**:
A pre-run locator result for one demo change request, shown without calling the pipeline.
_Avoid_: cached run, sample, example

**Demo corpus**:
The made-up S1000D manual that Replays run against: landing gear, brakes and hydraulics for the fictional Meridian M100.
_Avoid_: made-up project, sample data, test manual

**Facts sheet**:
The single list of every value, spec, part number and warning in the demo corpus, which each data module draws from.
_Avoid_: source data, seed file, master list

**Plant**:
A ripple deliberately added to the demo corpus because the manual didn't produce one naturally; always disclosed in the facts sheet.
_Avoid_: fake, staged ripple, injected example

**Honest miss**:
A Replay showing a ripple the locator really failed to find on the demo corpus, picked from the run's results and never planted.
_Avoid_: failure case, staged miss
