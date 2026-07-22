# Hypothesis Review Loop

`hypothesis-review` turns annotations made in the self-hosted Hypothesis service into a bounded, git-recorded batch of feedback for an agent.
Hypothesis owns annotation capture and normalization.
This project owns session timing, delivery, recording, and resolution.

## Review workflow

Run the workflow from the git repository being reviewed:

1. The agent runs `annotate doctor` and then `annotate wait`.

2. `wait` records the current time under `.git/annotate/open_time` and listens on `http://127.0.0.1:8902/session/close`.

3. The reviewer annotates HTML or PDF content with the browser extension.

4. The reviewer presses **Send to agent**. The extension posts to the loopback close endpoint; it shows the returned failure description and remains immediately retryable if no session is listening or the endpoint rejects the request.

5. `wait` reads annotations created after the local open time, excluding annotations already tagged `acted` and any rows left by the retired marker protocol.

6. The batch is appended to the repository's JSONL feedback ledger, committed, and only then printed to standard output.

7. After acting on the batch, the agent runs `annotate resolve` to add the `acted` tag through the Hypothesis API.

The browser request closes only the local waiting process.
It does not create a marker annotation or write session state to Hypothesis.

## Ownership and data path

- The self-hosted `h` service stores annotations and their normalized quotes.

- `PostgresSource` reads the annotation table directly because the observed search-index path is not a reliable source for this workflow.

- `annotate wait` owns the local session lower bound and the loopback close server.

- The browser extension owns the explicit close gesture and its visible success or error state.
  HTML pages and the bundled PDF viewer use the same control implementation.

- The repository under review owns `feedback/ledger.jsonl` by default.
  A caller may select another repo-relative ledger with `--path`.

- Git history supplies the code-state anchor for recorded feedback; there is no separate deployment log or version registry.

## Delivery invariants

- Commands that deliver feedback (`wait`, `pull`, and `record`) require a git repository.

- Delivery records before printing.
  There is no unrecorded delivery mode.

- Ledger entries are deduplicated by annotation ID.

- An annotation with a normalization error is not deliverable or recordable.
  The command reports the annotation ID and the stored normalization error instead of silently using degraded text.

- Every PDF selection uses the OCR-normalized quote produced by `h`; selected glyphs never bypass OCR. An HTML selection uses identity only when semantic source extraction returns the captured quote unchanged.

- Legacy `review:open` and `review:send` rows are ignored; new sessions never emit them.

## Commands

- `annotate doctor` checks the ambient git repository, configuration, self-hosted API, and Postgres source.

- `annotate wait [--timeout SECONDS] [--path PATH]` opens a local session, waits for the extension, records the resulting batch, and prints it as JSON.

- `annotate pull [--path PATH]` records and prints the batch after the most recently stored local open time without waiting for another close request.

- `annotate slice [--since TIME | --last DURATION] [--until TIME] [--uri URI]` is a read-only view for annotations outside the active review loop.

- `annotate record [--path PATH] ID...` records selected annotations discovered with `slice`.

- `annotate resolve` tags the current batch `acted`.

- `annotate status [--root BUILD_DIR]` reports open and acted counts and can check whether open annotation quotes still occur in a built site.

## Failure boundaries

`annotate wait` times out with an error if the extension does not close the session.
The extension distinguishes a rejected HTTP response from failure to contact the loopback service and keeps that description visible for retry.
Normalization failures retain their specific backend description through storage and CLI delivery rather than collapsing into a generic failure.
