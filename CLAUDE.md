# Notes for Claude

Daily digest of new Facebook events, scraped with Playwright using the owner's own session.
See README.md for what it does and how it's deployed.

- `fbevents/extract.py`: pure parsing (event ids from links, event data from page JSON). Most
  fixes for "Facebook changed its page code" belong here, with a test in `tests/test_extract.py`.
- `fbevents/browser.py`: the only code that talks to Facebook. `LoginRequired` means the session
  is gone; the run then emails the owner instead of retrying.
- `fbevents/run.py`: one run (collect, open new events, filter, email, remember).
- `fbevents/fbedb.py`: client for mwalczak/fbedb (Pages list, saving events; its answer decides
  which events are new and get emailed). Used when FBEDB_URL and FBEDB_TOKEN are set; change the
  API together with fbedb. `tests/fake_fb.py` has a FakeFbedb.
- Tests: `python -m pytest -q`. `tests/test_run.py` needs Chromium: in Claude cloud sessions set
  `FBEVENTS_CHROMIUM=/opt/pw-browsers/chromium` (picked up automatically if that path exists).
- Deploy: push to `main` → GitHub Actions builds the image and applies `k8s.yaml` on the k3s server
  (same pattern as mwalczak/mobile-scores). Settings come from the `CONFIG_TOML` repo variable,
  never from committed files; `update-settings.yml` copies them (and the secrets) into the k8s
  Secret, standalone or called from `deploy.yml`. Never commit `config.toml` or anything under `state/`.
- Keep the load on Facebook low: no retries (`backoffLimit: 0`), pauses between pages, a cap on
  event pages per run. Don't add parallelism.
- Issues are tracked in PMO (https://pmo.walczaki.com, project `FBEDB`, covering fbedb and
  fbevents; API docs at /docs, auth comes from the session's network secret). Always keep an issue's
  status current: `in_progress` when work starts, `done` once it's deployed and checked (or
  `wont_fix`/`duplicate`), with a short "Implemented" note appended to the description saying what
  was built, the decisions taken and what's left out.
