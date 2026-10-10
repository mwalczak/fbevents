# fbevents

Once a day, a short email listing new Facebook events from Pages you choose and from your
area, without opening Facebook or its feed.

It runs as a daily CronJob on the k3s server with your own Facebook login. It does what you
would do by hand: opens each Page's events tab and your town's Events page, then opens each
event it hasn't seen before to read the date, place and location. Each event is reported once.

**Know before you start:** Facebook's terms don't allow automated access, and Facebook can ask
you to re-verify a login that looks automated. The server is also in a data centre, not at
home, which makes that more likely. The app keeps it gentle (one run a day at a random
time, random pauses, a fixed browser identity, at most 40 event pages a run), but the risk
isn't zero. If Facebook logs the session out, you get an email asking you to log in again.

## How it runs

- `k8s.yaml` has a CronJob that starts at 7:15 Europe/Warsaw. The app then waits a random
  0–30 minutes before opening Facebook.
- Seen events and the Facebook session (refreshed after each run) are kept on a small
  persistent volume.
- Settings, the Facebook session you upload and the SMTP password are in the `fbevents`
  Secret, which the deploy workflow writes from GitHub.
- On days with nothing new, no email is sent (unless `send_when_empty = true`).

## One-time setup

### 1. Log in to Facebook on your computer

`login` opens a visible browser window, so do it on your laptop (Linux, Mac or Windows with
Python 3.11+):

```sh
git clone https://github.com/mwalczak/fbevents && cd fbevents
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
.venv/bin/python -m fbevents login        # log in, finish any 2FA check, press Enter
gh secret set FB_AUTH_JSON < state/auth.json
```

`state/auth.json` is your Facebook session. Treat it like a password; it is git-ignored.

### 2. Settings

Copy `config.example.toml` to `config.toml` and fill it in:

- **Pages**: open the venue's Page in a browser and paste its address, e.g.
  `https://www.facebook.com/SomeVenue`. `profile.php?id=…` addresses work as they are.
- **City URL**: go to facebook.com/events, choose your town in the location filter and copy
  the address (`https://www.facebook.com/events/explore/<town>/<number>/`). You can add
  neighbouring towns. "Nearby" events farther than `radius_km` from `city` are skipped.
  Events from your Pages are always included, whatever the distance, unless you set
  `pages_use_radius = true`.
- **Email**: for Gmail, create an [App Password](https://myaccount.google.com/apppasswords).

Then upload it as a repository variable (variables, unlike secrets, can be viewed and
edited later on GitHub):

```sh
gh variable set CONFIG_TOML < config.toml
gh secret set SMTP_PASSWORD               # paste the app password
```

### 3. Deploy

Repository secrets and variables (Settings → Secrets and variables → Actions):

| Name | Kind | What |
| --- | --- | --- |
| `VPS_HOST` | secret | server IP address (same as in `ip`, `mobile-scores`) |
| `VPS_SSH_KEY` | secret | private deploy key for user `debian` (same as in `ip`) |
| `FB_AUTH_JSON` | secret | Facebook session from step 1 |
| `SMTP_PASSWORD` | secret | email password / app password |
| `CONFIG_TOML` | variable | the whole `config.toml` |

Re-run the **Test and deploy** workflow (or push to `main`). It runs the tests, builds the
image, pushes it to GHCR and applies the Secret and `k8s.yaml` on the server. Until all five
are set it only runs the tests.

After the first deploy, make the GHCR package public (Packages → fbevents → Package settings
→ Change visibility), as with `mobile-scores`. The image holds no settings or passwords.

### 4. Try it

Run the **Run now** workflow with "Dry run" ticked. It starts a run right away and shows its
log, ending with the list of events it would send. Nothing is emailed or remembered. Untick
"Dry run" to send the email for real.

## Day to day

- **Change settings**: edit the `CONFIG_TOML` variable on GitHub, then re-run **Test and
  deploy**.
- **"please log in again" email**: repeat step 1 (`login` + `gh secret set FB_AUTH_JSON`) and
  re-run **Test and deploy**. A newly uploaded session replaces the one on the server.
- **"the daily run failed" email**: it contains the error; look at the log too (below).
- **Logs on the server**:
  `kubectl logs job/<name>` for the last few jobs (`kubectl get jobs`), or
  `run.log` on the volume.

## Running it locally

Useful for checking a Page or the city URL before uploading the settings:

```sh
.venv/bin/python -m fbevents test-email                    # needs SMTP_PASSWORD in the environment
.venv/bin/python -m fbevents run --dry-run --no-delay -v   # print the list, send and remember nothing
```

The list is also written to `state/last_digest.html`. Without `--dry-run` it emails the
digest and remembers the events in `state/seen.json`.

| File (in `state/` locally, `/data` on the server) | |
| --- | --- |
| `seen.json` | events already reported (forgotten after 180 days) |
| `auth.json` | Facebook session, refreshed after each run |
| `last_digest.html` | the last digest, open it in a browser |
| `run.log` | log of the runs |

## When something goes wrong

- **A Page finds 0 events**: open the Page's events tab yourself to check it has upcoming
  events.
- **Missing dates or places**: Facebook changes its page code often. The event still comes
  with its name and link, and the email shows the text from the event card instead. Run
  with `-v` and look for "no date found".
- **Facebook keeps logging out**: lower the load (fewer Pages, `max_event_pages`), or run it
  from a computer at home instead, e.g. a Raspberry Pi with the local commands above in cron
  (`15 7 * * * cd ~/fbevents && .venv/bin/python -m fbevents run`).

## Development

```sh
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

`tests/fake_fb.py` serves pages shaped like Facebook's, and `tests/test_run.py` runs the
whole app against them in headless Chromium.
