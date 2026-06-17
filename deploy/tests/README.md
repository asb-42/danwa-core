# Danwa-Core Test-Suite

Diese Test-Suite sichert das "Herz" des Danwa-Systems gegen Regressionen ab. Sie
umfasst 369+ Tests für die Kern-Module und erreicht eine Coverage, die deutlich
über dem Mindest-Zielwert von 60% liegt.

## Schnellstart

```bash
# Tests ausführen (vom Projekt-Root)
uv run pytest deploy/tests/backend/

# Mit Coverage-Report
uv run pytest deploy/tests/backend/ --cov=backend.core --cov=backend.models \
       --cov=backend.modules --cov=backend.llm_catalog \
       --cov-report=term-missing

# Nur ein Modul testen
uv run pytest deploy/tests/backend/core/
uv run pytest deploy/tests/backend/models/

# Verbose-Output
uv run pytest deploy/tests/backend/ -v

# Bestimmten Test
uv run pytest deploy/tests/backend/core/test_security.py::TestJWTRequiredClaims
```

## Architektur

```
tests/
├── conftest.py                          # Globale Fixtures, env-Isolation
└── backend/
    ├── core/                            # Querschnitts-Konzepte
    │   ├── test_config.py               # Settings, env-overrides, service-LLM-Eligibilität
    │   ├── test_security.py             # JWT, password hashing (SICHERHEITS-KRITISCH)
    │   ├── test_profiles.py             # LLMProfile-Pydantic-Schema
    │   ├── test_llm_id_aliases.py       # Legacy-ID → UUID Auflösung
    │   ├── test_logging.py              # structlog-Konfiguration
    │   └── test_seed.py                 # Default-Tenant/Admin-Seeding
    ├── models/                          # Pydantic-Datenmodelle
    │   ├── test_user.py
    │   ├── test_case.py
    │   ├── test_project.py
    │   ├── test_debate_input.py
    │   ├── test_artifact.py
    │   ├── test_tag.py
    │   ├── test_membership.py
    │   ├── test_tenant.py
    │   ├── test_transactional.py
    │   └── test_schemas.py
    ├── modules/                         # Modul-System (Validierung, Dependencies, Typen)
    │   ├── test_validation.py           # ModuleValidator
    │   ├── test_dependency_resolver.py  # semver + Rollen-Auflösung + Zyklen
    │   ├── test_type_derivation.py      # ModulTyp-Ableitung
    │   └── test_models.py               # Manifest/Profile-Pydantic-Modelle
    └── llm_catalog/                     # LLM-Katalog-Integration
        ├── test_id_strategy.py          # (vorhanden) deterministische Modul-IDs
        ├── test_id_strategy_extra.py    # Edge cases
        ├── test_normalize.py            # (vorhanden) catwalk/llm_db Normalisierung
        ├── test_import_engine.py        # (vorhanden) Diff + apply
        ├── test_sources.py              # Source-Registry
        └── test_fetcher.py              # git clone/pull Wrapper
```

## Test-Strategie

### 1. Pure-Unit-Tests (Standard)

Keine I/O, keine Datenbank, keine externen Services. Beispiele:

- `test_config.py` — Pydantic-Settings, env-vars, defaults
- `test_security.py` — bcrypt-Hashing, JWT encode/decode, claim-validierung
- `test_models/*` — Validierungs- und Konvertierungslogik der Pydantic-Schemas

Diese Tests sind **schnell** (Sekundenbruchteile) und **deterministisch**.

### 2. Filesystem-Fixture-Tests

Tests, die das Dateisystem nutzen, verwenden `tmp_path` (pytest-built-in) und
monkeypatchen `MODULES_DIR` etc. auf das tempdir — **niemals** wird der echte
`modules/`-Ordner berührt.

Beispiele: `test_import_engine.py`, `test_llm_id_aliases.py`.

### 3. Subprocess-Mocking-Tests

Tests, die externe Tools (`git`) aufrufen würden, mocken `subprocess.run` und
`shutil.which`. So laufen sie auch in Umgebungen ohne `git` durch.

Beispiele: `test_fetcher.py` — Klon-Fehler, Fetch-Fehler, Timeout.

### 4. Parametrisierte Tests

Validierungs-Tests (z.B. `temperature in [0, 2]`, `role in {admin, editor, viewer}`)
verwenden `@pytest.mark.parametrize`, um die gesamte Boundary-Logik mit einem
kompakten Test abzudecken.

## Coverage-Ziele

| Modul | Ziel | Status |
|-------|------|--------|
| `backend.core` | 100% | ✅ |
| `backend.models` | 100% | ✅ |
| `backend.modules` (validator, dependency_resolver, type_derivation, models) | 100% | ✅ |
| `backend.llm_catalog` (id_strategy, sources, normalize, import_engine) | 100% | ✅ |
| **Gesamt im Geltungsbereich** | **≥ 60%** | ✅ |

### Coverage lokal messen

```bash
uv run pytest deploy/tests/backend/ \
    --cov=backend.core --cov=backend.models \
    --cov=backend.modules --cov=backend.llm_catalog \
    --cov-report=term-missing:skip-covered \
    --cov-report=html:htmlcov
# HTML-Report: htmlcov/index.html
```

## Konventionen

### Fixture-Philosophie

- **Globale Fixtures** in `tests/conftest.py` (env-Isolation, fs-Helfer).
- **Lokale Fixtures** in der jeweiligen Test-Datei (Modul-spezifisch).
- **`autouse=True`** nur, wenn jeder Test im Modul die Isolation braucht
  (z.B. `tmp_path`-Reset im `test_llm_id_aliases.py`).

### Test-Benennung

- `test_<unit>_<scenario>_<expected_outcome>()` — z.B. `test_decode_token_rejects_missing_sub`.
- `@pytest.mark.parametrize` für Boundary-Werte.
- Keine Test-Klassen (nur Funktionen — einfacher zu navigieren).

### Mocking-Strategie

- `unittest.mock.MagicMock` + `patch.object` für Klassen-/Modul-Attribute.
- `monkeypatch` für `os.environ`-Einträge.
- `pytest.MonkeyPatch.setattr` für Modul-Variablen.
- **Niemals** `requests`/`httpx`/`subprocess` ohne Mock aufrufen.

### Test-Daten

- Keine "magic strings" — Werte werden am Anfang des Tests benannt.
- Keine geteilten `conftest.py`-Datenbanken — `tmp_path` für jeden Test.

## Bekannte Einschränkungen / nächste Schritte

Diese erste Test-Suite fokussiert auf die **Kern-Module** mit hoher Test-Dichte
und hoher Regression-Risiko. Ausbaufähige Bereiche:

1. **API-Routes** (`backend/api/routers/*`) — Test-Stack mit `httpx.AsyncClient`
   gegen die FastAPI-App.
2. **Workflow-Engine** (`backend/workflow/*`) — Integrationstests der
   LangGraph-Graphen mit gemockten LLM-Responses.
3. **DMS / RAG** (`backend/services/dms/*`) — Tests mit In-Memory-Vector-Store.
4. **A2A-Protocol** (`backend/a2a/*`) — HTTP-Mocking + Schema-Validierung.
5. **Migrationen** (`backend/migrations/*`) — Round-Trip-Tests.

Diese Bereiche sind im Sprint-Backlog, aber für die initiale "Heart of the
System"-Absicherung bewusst noch nicht enthalten.

## CI-Integration

In GitHub Actions o.ä.:

```yaml
- name: Tests
  run: |
    uv pip install -e ".[test]"
    pytest deploy/tests/backend/ \
      --cov=backend.core --cov=backend.models \
      --cov=backend.modules --cov=backend.llm_catalog \
      --cov-fail-under=60
```

`--cov-fail-under=60` bricht den Build, wenn die Coverage unter 60% fällt.
