## What and why

<!-- One or two sentences. Link the issue if there is one. -->

## How it was checked

- [ ] `pytest`
- [ ] `ruff check src tests scripts` and `ruff format --check src tests scripts`
- [ ] `mypy src/hindsight`
- [ ] `npm test --prefix web` and `npm run build --prefix web` (UI changes; commit the rebuilt assets)
- [ ] `mkdocs build --strict` (docs changes)

## Safety

- [ ] Fixtures use synthetic data only: no real keys, transcripts, hostnames or customer identifiers.
- [ ] New capture or redaction paths run redaction before anything is written, indexed or sent to a model.
