# DOX: templates/

## Purpose

Jinja2 and Markdown templates for Danwa's UI generation, document generation, and email rendering systems.

## Ownership

- **Jinja2 Templates**: `templates/jinja2/` — UI generation templates
- **Markdown Templates**: `templates/markdown/` — document generation templates

## Local Contracts

- Templates must be valid Jinja2 or Markdown syntax
- Template variables must be documented
- Templates follow existing naming conventions

## Work Guidance

- Keep templates DRY (use includes/extends where possible)
- Document template variables and usage
- Test template rendering with sample data

## Verification

- Verify templates render correctly
- Check for syntax errors with template linter if available

## Child DOX Index

| Child | Purpose |
|-------|---------|
| `templates/jinja2/` | Jinja2 UI generation templates |
| `templates/markdown/` | Markdown document templates |
