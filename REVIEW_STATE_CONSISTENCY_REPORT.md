# Review State Consistency Report

## Finding

The Operations Center showed an open `REVIEW_CONTENT` task after all reviewable
work had been resolved. `refresh_operational_state()` created that task but
never completed it when its underlying work reached zero.

The displayed queue count was not itself a review count. It counts
`PostingQueueItem` rows in `READY_TO_POST`, which are already-approved creative
work awaiting a separate explicit publishing decision. Likewise, the
`content-ready` briefing metric counts approved content packages produced in
the last 24 hours; it does not mean a creative decision is pending.

## Resolution

The authoritative, brand-scoped review inbox now contains only items that
`/viralforge review` can open, in this order:

1. source acceptance (`SOURCE_REVIEW_REQUIRED`)
2. pending clip opportunities
3. successfully rendered clips awaiting approval
4. pending content packages
5. discovery items in `NEEDS_REVIEW`

Operations health, task generation, the Operations Center, and
`/viralforge review` use this same inbox. Refreshing Operations updates an
existing task's live reason and marks `REVIEW_CONTENT` as `COMPLETED` when the
inbox is empty. The Operations embed now separately labels creative-review
count and no longer directs an operator to review when none is pending.

## Regression coverage

- Complete five-stage review inbox coverage
- Cross-brand isolation
- Health count matches the shared review inbox
- `/viralforge review` priority uses that same inbox
- Stale review task closes when no reviewable work remains

## Verification

- `pytest -q`: 167 passed, 2 skipped
- `ruff check .`: passed
- `mypy app`: passed

The normal SQLite teardown foreign-key-cycle warning and third-party library
deprecation warnings remain non-failing test-environment warnings.
