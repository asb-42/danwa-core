# DOX: deploy/

## Purpose

Deployment scripts and configurations for Danwa: import/export, GitNexus integration, service deployment, and module deployment.

## Ownership

- **Import/Export**: `deploy/` — module and blueprint import/export
- **GitNexus**: `deploy/gitnexus/` — GitNexus integration deployment
- **Docs**: `deploy/docs/` — deployment documentation

## Local Contracts

- Deployment scripts must be idempotent
- Import/export maintains data integrity
- GitNexus integration follows its own API contracts

## Work Guidance

- Test deployment scripts in staging before production
- Document deployment steps and rollback procedures
- Maintain deployment logs

## Verification

- Test deployment in staging environment
- Verify import/export data integrity

## Child DOX Index

| Child | Purpose |
|-------|---------|
| `deploy/gitnexus/` | GitNexus integration deployment |
| `deploy/docs/` | Deployment documentation |
