# hypothesis-review task runner.

ai_review_ci_schema_version := "1"
ai_review_ci_profile := "python"
ai_review_ci_ref := "main"
ai_review_ci_release_channel := "main"
ai_review_ci_workflow_template_version := "1"
ai_review_ci_local_delegation := "global-justfile"
ai_review_ci_default_branch := "main"

# List available recipes.
default:
    @just --list

# Run commit-tier Python QC through the central implementation.
test-commit:
    @just -f ~/ai-review-ci/justfiles/python.just -d . test-commit

# Run the full Python test suite before pushing.
test-push:
    @just -f ~/ai-review-ci/justfiles/python.just -d . test-push

# Run CI acceptance QC through the central implementation.
test-ci:
    @just -f ~/ai-review-ci/justfiles/python.just -d . test-ci

[private]
_lock:
    uv lock --python 3.14

[private]
_serve-session-close:
    uv run python -c 'from annotate.session_server import SESSION_CLOSE_PORT, wait_for_close; raise SystemExit(0 if wait_for_close(30, SESSION_CLOSE_PORT) else 1)'

[private]
_test-source:
    uv run pytest -q tests/test_source.py

[private]
_test-config:
    uv run pytest -q tests/test_config.py tests/test_cli_commands.py

[private]
_typecheck:
    just -f ~/ai-review-ci/justfiles/python.just -d . _mypy

# Prove the queue/drain boundary against the configured h API, Postgres, and ledger repo.
# The extension and client visual proofs remain owned by their respective repositories.
proof-integrated:
    #!/usr/bin/env bash
    set -euo pipefail
    echo "=== integrated proof: preflight ==="
    uv run annotate doctor
    echo "=== integrated proof: active queue and drain ==="
    ANNOTATE_PG_IT=1 uv run pytest -q tests/test_send_to_agent.py tests/test_api.py
    echo "=== integrated proof: complete ==="
