# hypothesis-review — task runner.
#
# Design phase: the repo holds DESIGN.md + README.md and no code, so the QC gates are
# honest no-ops. Real recipes — lint/type/test for the `annotate` Python CLI and the
# browser-extension fork — land with the implementation and route through ai-review-ci,
# like the sibling repos.

# Commit-tier gate (whole-repo QC).
test-commit:
    @echo "hypothesis-review: design phase — no code to gate yet"

# Push-tier gate.
test-push: test-commit

# CI acceptance gate.
test-ci: test-commit
