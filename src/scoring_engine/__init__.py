"""Pure scoring library: rubric weighting, judge-bias correction, uncertainty, assignment.

No Django, no database, no I/O. Inputs are plain dataclasses and outputs are
plain dataclasses, so every number is reproducible and testable in isolation.
An import-linter contract enforces this boundary.
"""
