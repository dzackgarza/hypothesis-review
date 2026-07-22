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
_test-session-server:
    uv run pytest -q tests/test_session_server.py

[private]
_test-session:
    uv run pytest -q tests/test_session.py tests/test_session_server.py tests/test_cli_pull.py

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

# Integrated cross-repo proof (issue #6): drives the real four-branch stack end to end.
# Prerequisites (fail loudly, never skip): the h dev stack (web API, Postgres,
# Elasticsearch, broker) at the configured ANNOTATE_API_URL / ANNOTATE_PG_DSN, the client
# harness at ANNOTATE_HARNESS_URL, MATHPIX_API_KEY for the PDF OCR legs, and a browser
# session for the extension legs. No endpoint is guessed: every address the preflight
# probes comes from that declared configuration. Steps: doctor -> live-boundary suite
# (pg/e2e opt-ins) -> session close over the real loopback -> delivery/ledger reread.
proof-integrated:
    #!/usr/bin/env bash
    set -euo pipefail
    echo "=== integrated proof: preflight ==="
    # Required inputs, validated once here before any proof step runs. The harness base has
    # no default: a guessed-but-reachable localhost port would prove the run against
    # whatever happens to be listening rather than the stack under test, so `set -u` plus
    # `:?` makes an unset value a loud failure.
    : "${ANNOTATE_HARNESS_URL:?required -- the client harness base URL served by dev-server/run-harness.mjs}"
    # `annotate doctor` is this tool's config boundary: it loads the total ANNOTATE_* model
    # and probes the *configured* h API and Postgres, exiting non-zero on any unreachable
    # dependency. It therefore owns the h-side reachability check outright.
    uv run annotate doctor
    # The harness is not part of the annotate config model, so probe it directly. Under
    # `set -e` a connection failure or non-2xx stops the recipe with curl's own status.
    curl -sf "${ANNOTATE_HARNESS_URL}" >/dev/null
    echo "=== integrated proof: live-boundary suite (pg opt-in) ==="
    ANNOTATE_PG_IT=1 uv run pytest -q tests/
    echo "=== integrated proof: session close over the real loopback ==="
    uv run pytest -q tests/test_session_server.py
    echo "=== integrated proof: complete ==="
    echo "Browser legs (login, annotate, Send-to-agent click, screenshots) are driven"
    echo "by the harness runner; captures land in .proof/ for the PR evidence index."
