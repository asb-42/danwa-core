# DOX: config/

## Purpose

Configuration files for the Danwa backend: Docker compose, environment templates, deployment settings, and application configuration.

## Ownership

- **Docker**: `config/docker-compose*.yml` — container orchestration
- **Environment**: `config/env/` — environment-specific settings
- **Deploy**: `config/deploy/` — deployment configurations
- **Application**: `config/` root — app config, schema files, service definitions

## Local Contracts

- Environment variables override config file values
- Docker compose files must be validated before deployment
- Config schema files define valid configuration structure

## Work Guidance

- Keep config files environment-specific where needed
- Document required environment variables
- Validate config changes against schema

## Verification

- Validate Docker compose with `docker compose config`
- Check environment variable coverage

## Child DOX Index

| Child | Purpose |
|-------|---------|
| `config/env/` | Environment-specific configuration |
| `config/deploy/` | Deployment configurations |
