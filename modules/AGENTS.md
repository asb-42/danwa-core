# DOX: modules/

## Purpose

Module installation, validation, and resolution system for Danwa. Handles dependency resolution, version management, and module lifecycle.

## Ownership

- **Install Logic**: `modules/install.py` — module installation pipeline
- **Validation**: `modules/validation.py` — schema and version validation
- **Resolver**: `modules/resolver.py` — dependency resolution algorithms
- **Module Manager**: `modules/module_manager.py` — lifecycle management

## Local Contracts

- Modules follow the standard Danwa module format (index.json, manifest.json)
- Validation must check schema compliance before installation
- Resolver handles circular dependency detection

## Work Guidance

- Maintain backward compatibility in resolver logic
- Add validation for new module metadata fields
- Test edge cases in dependency resolution (circular deps, version conflicts)

## Verification

- Run `pytest tests/backend/modules/ -v` for module tests
- Verify with `python scripts/manage.sh modules validate`

## Child DOX Index

| Child | Purpose |
|-------|---------|
| (flat structure, no significant subdirectories) |
