# Notes for Claude

Daily digest of new Facebook events, scraped with Playwright using the owner's own session.
See README.md for what it does and how it's deployed.

- `fbevents/extract.py`: pure parsing (event ids from links, event data from page JSON). Most
  fixes for "Facebook changed its page code" belong here, with a test in `tests/test_extract.py`.
- `fbevents/browser.py`: the only code that talks to Facebook. `LoginRequired` means the session
  is gone; the run then emails the owner instead of retrying.
- `fbevents/run.py`: one run (collect, open new events, filter, email, remember).
- Tests: `python -m pytest -q`. `tests/test_run.py` needs Chromium: in Claude cloud sessions set
  `FBEVENTS_CHROMIUM=/opt/pw-browsers/chromium` (picked up automatically if that path exists).
- Deploy: push to `main` → GitHub Actions builds the image and applies `k8s.yaml` on the k3s server
  (same pattern as mwalczak/mobile-scores). Settings come from the `CONFIG_TOML` repo variable,
  never from committed files. Never commit `config.toml` or anything under `state/`.
- Keep the load on Facebook low: no retries (`backoffLimit: 0`), pauses between pages, a cap on
  event pages per run. Don't add parallelism.
