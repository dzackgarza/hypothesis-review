# hypothesis-review

`annotate` CLI: an agent runs `annotate wait`, the user annotates pages/PDFs and hits
"Send to agent", the agent reads the recorded batch. HTML math is recovered from the page
x-tex layer (`mathquote.py`); PDF math is OCR'd with Mathpix (`pdfmath.py`) and written
back into the annotation body (`enrich.py`) so the sidebar renders it. `MATHPIX_API_KEY`
comes from the environment.

**Use `just`, never raw `pytest`/`ruff`/`pyright`.** QC runs on commit/push via the git
hooks; `just test-commit` is the manual whole-repo gate (ruff + pyright + pytest),
`just test-push` / `just test-ci` the higher tiers.
