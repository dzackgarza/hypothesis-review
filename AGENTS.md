# hypothesis-review

`annotate` CLI: an agent runs `annotate wait`, the user annotates pages/PDFs and hits
"Send to agent", the agent reads the recorded batch. HTML math is recovered from the page
x-tex layer (`mathquote.py`); PDF math is OCR'd with Mathpix (`pdfmath.py`) and written
back into the annotation body (`enrich.py`) so the sidebar renders it. `MATHPIX_API_KEY`
comes from the environment.

**Use `just`, never raw `pytest`/`ruff`/`pyright`.** QC runs on commit/push via the git
hooks; `just test-commit` is the manual whole-repo gate (ruff + pyright + pytest),
`just test-push` / `just test-ci` the higher tiers.

<!-- agent-memory:start -->
# Agent memory

This repository uses the central agent memory vault at `/home/dzack/.agent-memory-vault`.

Project memory key: `projects/github.com__dzackgarza__hypothesis-review/index`.

Repository `.agents` and `.hermes` paths are symlinks to the same vault-owned project directory.

Before changing architecture, search both project and global memory:

```bash
agent-memory search --scope both "<task or subsystem>"
```

Record durable repo-specific lessons with:

```bash
agent-memory add --scope project --type decision --title <title> --content <content>
agent-memory add --scope project --type trap --title <title> --content <content>
agent-memory add --scope project --type advice --title <title> --content <content>
agent-memory add --scope project --type context --title <title> --content <content>
agent-memory add --scope project --type reference --title <title> --content <content>
```

Plan work is card-backed. Create and update plan cards with `agent-memory plan add` and `agent-memory plan update`, not `agent-memory add --type plan`.

Use `agent-memory retrieve <key>`, `agent-memory update <key>`, and `agent-memory delete <key>` for memory CRUD.

The vault should be committed at all times. Treat staged or unstaged vault changes as an ephemeral error state. Before normal memory work resumes, load the bundled vault-maintenance skill with `agent-memory maintain skill vault-maintenance` and follow its referenced check, repair, and commit workflows.

Move reusable lessons during maintenance with:

```bash
agent-memory maintain move <key> --to global/advice
```
<!-- agent-memory:end -->
