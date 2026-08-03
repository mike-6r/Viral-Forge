"""Policy-governed unattended operations.

The package deliberately sits above the existing production and publishing
services.  It records a decision before dispatching work and fails closed when
evidence or a required review is missing.
"""
