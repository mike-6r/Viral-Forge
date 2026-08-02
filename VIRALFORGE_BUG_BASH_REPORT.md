# ViralForge bug-bash report

Date: 2026-08-02

## Fixed: worker render persistence failure

- Severity: P1
- Reproduction: approve a generated clip opportunity in a clean, restarted Celery worker, then wait for the render to complete.
- Observed failure: `NoReferencedTableError` for `production_clips.correction_plan_id` targeting `clip_correction_plans`.
- Root cause: the worker could load `ProductionClip` and configure SQLAlchemy mappers before importing the correction-plan model that owns the referenced table.
- Fix: import the correction model when `app.worker` initializes so the table is registered before any worker task configures `ProductionClip`.
- Regression test: `tests/test_worker_foundation.py` asserts the correction-plan table is registered by the worker import. A clean-worker mapper check was also run locally.
- Commit: `4f2b19c Fix worker correction model registration`.
- Status: fixed, deployed, and verified by retrying the same already-approved opportunity. The retry rendered successfully, persisted the clip, generated its quality report, and reached CONTENT_READY.

## Rejected as a defect: slow portrait rendering

The 90-second portrait render took about 215 seconds on the VPS. FFmpeg remained CPU-active and the output grew throughout. This was resource-intensive but progressive and successful, not a hang.
