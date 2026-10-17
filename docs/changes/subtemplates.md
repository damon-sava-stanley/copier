# Linked subtemplates

## Problem and outcome

Copier instances cannot currently declare and preserve parent-child links. Add
explicit links so each instance stays independently copyable, recopyable, and
updatable while linked answers and fragments render in affected relatives.

## Scope

Implement the six milestones in `.local/plans/subtemplate.md`: metadata and
links, linked contexts, fragment rendering, graph propagation, update merges,
and documentation. Preserve standalone behavior. Do not infer links from
generated files or `_external_data`, and do not add full transactionality.

## Design sketch

Identify instances by destination-relative answers-file paths. Validate a
reciprocal, acyclic graph before destination changes. Supply direct-relative
answer views to rendering, and resolve child fragments through the linked
instance. Build one affected-instance plan per primary operation. Reuse the
existing update merge machinery for dependent output.

## Acceptance examples

- Two instances of one parent repository can each select distinct children;
  selection is explicit and reciprocal links are saved.
- Direct relatives see only recorded nonsecret answers, including fresh primary
  answers during the same operation.
- Child fragments render with the child's template environment and context.
- A primary operation rerenders its affected relatives once, preserving their
  recorded revisions and leaving sibling branches alone.
- Dependent update output merges with project edits using Copier's conflict
  modes.

## Risks and open questions

- Template source identity must be consistent between declarations and saved
  `_src_path` values.
- Output ownership and update merge behavior need careful validation before
  destination mutation.
