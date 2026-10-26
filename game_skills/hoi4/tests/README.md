# HOI4 Game Skills Test & Fixtures Directory

This directory contains validation fixtures and tests for the Hearts of Iron IV Game Skills Pack.

## Manifest Schema Validation

The manifest and its linked selectors, skills, and image templates are validated by `tests/test_game_skills.py` in the root test suite.

Tests ensure:
- Manifest follows version 1.0.0 schema.
- All declared `selectors` exist as valid JSON files under `selectors/`.
- All declared `skills` exist as valid JSON files under `skills/`.
- All referenced image templates exist under `templates/`.
- Bounding box ROIs are within `[0.0, 1.0]`.
- No hardcoded absolute screen pixel coordinates are mandated.
