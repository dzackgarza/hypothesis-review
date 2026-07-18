# hypothesis-review — task runner.
#
# Real QC for the `annotate` Python CLI: lint (ruff) + type (pyright) + test (pytest),
# all through uv. Routes through ai-review-ci like the sibling repos.

# Commit-tier gate (whole-repo QC).
test-commit:
    uv run ruff check
    uv run pyright
    uv run pytest

# Push-tier gate.
test-push: test-commit

# CI acceptance gate.
test-ci: test-commit
