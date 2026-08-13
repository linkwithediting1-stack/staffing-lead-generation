# LeadGen AI

LeadGen AI is a local-first Python system for finding companies with public hiring
signals, qualifying them for staffing outreach, avoiding companies already contacted,
and preparing messages for human approval.

The default pipeline costs nothing to run. It uses deterministic extraction and scoring
first. AI is optional, cached, and limited to a small review batch. It supports a local
Ollama model or any OpenAI-compatible API.

## What it automates

1. Loads company website seeds and optional SearXNG search results.
2. Crawls permitted public pages while respecting `robots.txt` and per-domain delays.
3. Prioritizes careers, jobs, contact, about, and team pages.
4. Extracts company identity, location, industry, hiring phrases, job links, public
   role-based email addresses, phone numbers, and evidence URLs.
5. Deduplicates by company domain in SQLite.
6. Scores every lead with transparent rules before using AI.
7. Imports old outreach history into a do-not-contact ledger.
8. Creates outreach drafts only for qualified, contactable leads.
9. Provides a localhost review dashboard and explicit approval workflow.
10. Exports CSV/JSONL and optionally upserts results into Google Sheets.

It does not collect personal emails, guess personal attributes, bypass access controls,
solve CAPTCHAs, or automatically log in to social networks. Instagram, LinkedIn, and
other high-risk platforms are denied by default. Use their official APIs or exports when
available.

## Quick start on Windows

```powershell
cd leadgen-ai
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e .
leadgen init
leadgen init-db
leadgen doctor
```

Edit `config.json`, then add company pages to `data/seeds.csv`:

```csv
url,source
https://company-one.example/careers,manual
https://company-two.example,industry-list
```

Run discovery and open the review screen:

```powershell
leadgen run --skip-search --skip-ai
leadgen leads --min-score 45
leadgen dashboard
```

The dashboard is available at `http://127.0.0.1:8765` while that command is running.

## Free discovery

Manual seed lists are fully free and produce the most controlled results. For broader
discovery, connect a SearXNG instance:

```json
"search": {
  "provider": "searxng",
  "endpoint": "http://127.0.0.1:8080",
  "api_key_env": "SEARXNG_API_KEY",
  "queries": [
    "India SaaS company hiring careers",
    "Gurugram fintech open positions",
    "Bengaluru startup careers sales hiring"
  ],
  "results_per_query": 15
}
```

Only use a SearXNG service you operate or are authorized to access. Search results are
treated as candidate URLs; the company website remains the evidence source.

## Very low-token AI

AI is disabled by default. That is intentional: regex, HTML metadata, JSON-LD, link
classification, and rule scoring handle the bulk of the work for free.

For a local Ollama installation, start an installed model and configure:

```json
"ai": {
  "enabled": true,
  "base_url": "http://127.0.0.1:11434/v1",
  "api_key_env": "AI_API_KEY",
  "model": "YOUR_INSTALLED_MODEL",
  "max_input_characters": 3500,
  "max_output_tokens": 220,
  "max_reviews_per_run": 20
}
```

For a hosted OpenAI-compatible free-tier API, change `base_url` and `model`, then put the
key in `.env`:

```dotenv
AI_API_KEY=your_key_here
```

A hosted model receives the compact company-page evidence being reviewed. Secrets,
cookies, and browser credentials are never included in that prompt.

Token controls:

- Rules run before AI.
- Only leads above `target.ai_review_threshold` reach the model.
- At most `ai.max_reviews_per_run` are reviewed per run.
- Input evidence and output size are hard-capped.
- Temperature is zero and JSON output is requested.
- Identical evidence/model combinations use the SQLite cache.
- Message copy is template-generated, so drafting consumes no AI tokens.

## Avoid contacting the same lead twice

Export your existing sheet as CSV and import it:

```powershell
leadgen import-history "C:\path\existing-outreach.csv"
```

The importer recognizes common columns such as `Company`, `Website`, `Email`,
`Instagram ID`, `Message Sent`, `Reply Received`, `Unreachable`, and `Status`.
Rows marked sent, contacted, replied, unreachable, rejected, opted out, or unsubscribed
are added to the do-not-contact ledger. `Not Sent` remains eligible.

Manual entries are also supported:

```powershell
leadgen dnc-add company.com --reason "Already contacted on Instagram"
leadgen dnc-add careers@company.com --reason "Opted out"
```

## Outreach workflow

First generate drafts:

```powershell
leadgen draft
leadgen messages --status draft
```

Approve each message in the dashboard or CLI:

```powershell
leadgen approve --message-id 12
```

Approval never sends anything. To send approved email through SMTP, configure `.env`
and the `smtp` section, then use the explicit confirmation flag:

```dotenv
SMTP_USERNAME=your_smtp_username
SMTP_PASSWORD=your_app_password
SMTP_FROM_EMAIL=you@yourcompany.com
```

```powershell
leadgen send --confirm-send --limit 5
```

The daily cap in `outreach.daily_send_limit` is enforced. Opt-outs should immediately be
added with `dnc-add`. Follow local privacy, anti-spam, and platform rules.

## Google Sheets sync

Install the optional connector:

```powershell
python -m pip install -e ".[sheets]"
```

Create a Google service account, share the target sheet with its email address, put the
credential JSON path in `.env`, and configure the sheet URL:

```dotenv
GOOGLE_SERVICE_ACCOUNT_FILE=C:\secure\google-service-account.json
```

```json
"google_sheets": {
  "enabled": true,
  "spreadsheet_url": "https://docs.google.com/spreadsheets/d/ID/edit",
  "worksheet": "Leads",
  "credentials_file_env": "GOOGLE_SERVICE_ACCOUNT_FILE"
}
```

Run `leadgen sync-sheets`. Existing rows are matched by domain; new rows are appended.
The command refuses to overwrite a tab with an unexpected header layout.

## JavaScript-heavy authorized sites

Install browser support:

```powershell
python -m pip install -e ".[browser]"
playwright install chromium
```

Set `crawler.allowed_domains` to the exact sites you are authorized to automate and set
`crawler.use_browser` to `true`. If a permitted site needs a login, create the session
manually so the application never receives your password:

```powershell
leadgen browser-session --url https://authorized.example/login
```

The browser profile is stored under `data/browser-profile/` and excluded from Git. Treat
it as sensitive. Do not use this feature to bypass a platform's technical controls or
terms.

## Useful commands

```text
leadgen doctor                         Validate configuration
leadgen run                            Discover and qualify leads
leadgen run --skip-search --skip-ai    Seed-only, zero-AI run
leadgen leads --min-score 60           Inspect top leads
leadgen draft                          Build the approval queue
leadgen dashboard                      Review leads and messages locally
leadgen export --format csv            Export outputs/leads.csv
leadgen sync-sheets                    Upsert into Google Sheets
leadgen send --confirm-send --limit 5  Send approved email only
```

## Project layout

```text
leadgen-ai/
  config.json                 Local settings
  .env                        Local secrets, ignored by Git
  data/seeds.csv              Starting company URLs
  data/leadgen.db             Leads, messages, cache, DNC, and audit log
  outputs/                    CSV and JSONL exports
  src/leadgen_ai/             Application source
  tests/                      Standard-library unit tests
```

Run tests with:

```powershell
$env:PYTHONPATH="src"
python -m unittest discover -s tests -v
```
