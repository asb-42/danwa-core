# DOX: packages/

## Purpose

Shared Python packages for the Danwa ecosystem, extracted as independent installable units.

## Ownership

- **danwa-core-models**: Shared Pydantic models
- **danwa-shared**: Shared utilities and constants

## Local Contracts

- Packages follow standard Python packaging conventions
- Dependencies are managed via pyproject.toml
- Breaking changes require version bumps

## Work Guidance

- Keep packages minimal and focused
- Document public APIs in package README
- Version packages independently

## Verification

- Run package tests if available
- Verify imports work correctly

## Child DOX Index

| Child | Purpose |
|-------|---------|
| (flat structure, no significant subdirectories) |
