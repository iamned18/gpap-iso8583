# ISO 8583 Checker — Web Edition

A browser-based port of the Notepad++ ISO 8583 validator. It runs entirely
client-side: [Pyodide](https://pyodide.org) (CPython compiled to
WebAssembly) loads the **unmodified** `iso8583_rules.py`, `iso8583_parse.py`,
and `iso8583_validate.py` engine modules straight into the page and runs
them in your browser tab. No server, no build step, nothing leaves your
machine.

## Files

| File | Purpose |
|---|---|
| `index.html` | The page — UI, Pyodide bootstrap, all JS |
| `iso8583_rules.py` | Rule loading / constants (unchanged from the desktop tool) |
| `iso8583_parse.py` | Message parsing & auto-detection (unchanged) |
| `iso8583_validate.py` | Validation engine & report builder (unchanged) |
| `web_adapter.py` | New — thin glue that adapts the engine to take strings/uploads instead of Windows file paths and interactive prompts |

`iso8583_checker.py` (the Notepad++ entry point) and `iso8583_cli.py`
(the desktop CLI) are **not** part of this build — they depend on
Notepad++'s `Npp` module / local file I/O that don't apply in a browser.
`web_adapter.py` replaces that role.

## Preloading Rule Set CSVs (optional)

By default, people using the page upload the Rule Set CSVs themselves each
session. If you'd rather they didn't have to, commit your real CSVs into
the repo under a `rules/` folder next to `index.html`, using these exact
filenames:

| Repo path | Replaces |
|---|---|
| `rules/standard.csv` | Standard Rule Set upload |
| `rules/emv.csv` | EMV Tag Rule Set upload |
| `rules/de61.csv` | DE61 Rule Set upload |
| `rules/de63.csv` | DE63 Rule Set upload |

On page load, `index.html` tries to `fetch()` each of those paths. Any that
exist load automatically and the corresponding upload box shows a
**preloaded** badge; any that don't exist just fall back to requiring a
manual upload, same as before. Uploading a file always overrides whatever
was preloaded, for that browser session only — nothing is written back to
the repo.

Since these CSVs would then be sitting in a public GitHub Pages repo, only
do this if the rule set itself isn't sensitive (it's validation logic, not
transaction data — but use your judgment for your org).

## Deploying to GitHub Pages

1. Create a new GitHub repo (or use an existing one).
2. Add all five files above to the **repo root** (or to a `/docs` folder —
   just make sure Pages is pointed at wherever they live).
3. Push to GitHub.
4. In the repo: **Settings → Pages → Build and deployment → Source** → set
   to *Deploy from a branch*, pick the branch and folder (root or `/docs`).
5. Wait a minute for the first deploy, then visit the URL GitHub gives you
   (`https://<username>.github.io/<repo>/`).

That's it — no Actions workflow or build step needed, it's static files.

## Using it

1. Upload your **Standard Rule Set** and **EMV Tag Rule Set** CSVs (same
   column layout as `Rule Set.csv` / `Rule Set - EMV Tag.csv`). DE61/DE63
   rule CSVs are optional, same as the desktop tool.
2. Paste an ISO 8583 trace into the text box.
3. Pick the message leg to validate (defaults to `Acq Req`, same as
   `TARGET_LEG` in the desktop config).
4. Leave Card / Entry Mode / Txn Type on **Auto** to let it detect from the
   PAN / DE22 / MTI+DE3 the same way the desktop tool does, or override any
   of them explicitly.
5. Click **Run Validation**. The report renders on the right, with
   copy-to-clipboard and download-as-`.txt` buttons.

## Notes / limitations vs. the desktop tool

- **CSV uploads are per-session.** The browser can't read arbitrary local
  files on its own, so you re-select the CSVs each time you load the page
  (they're not written anywhere — just read into memory for that session).
  If you want them pre-loaded automatically, you can commit default CSVs
  into the repo and add a few lines to `boot()` in `index.html` to `fetch()`
  them instead of requiring an upload.
- **Single file at a time** — there's no batch/`--all` mode or
  `Summary_Results.txt` like the CLI's folder-scanning mode. Each run
  validates one pasted trace.
- **First load** pulls down the Pyodide runtime (a few MB) from a CDN, so
  the first visit takes a couple of seconds longer than later ones (the
  browser caches it after that).
- Everything else — parsing quirks, BIN ranges, MTI/DE3 detection tables,
  the DE55 TLV parser, DE61/DE63 fixed-subfield parser, report formatting —
  is byte-for-byte the same engine code as the desktop version.
