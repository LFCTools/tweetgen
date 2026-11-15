# LFC Ticket Alerts & Tweet Scheduler

Streamlit app that scans an LFC ticketing page with Gemini, lets you correct the
extracted dates, then generates tweet timelines, Buffer schedules and calendar
entries.

## How it works

1. **Scan** – paste a ticket page URL. The match type (PL home, cup/euro home,
   league away, euro away) is auto-detected, or pick it yourself. *Enter manually*
   gives a blank form if scanning fails.
2. **Check the details** – all key dates and sale tiers are in two editable tables.
   Clear a date to mark it TBA; add or remove sale tiers as rows.
3. **Review tweets** – tweets update live as you edit. Any tweet text can be edited,
   and each reminder can be unticked to leave it out of Buffer.
4. **Schedule & calendar** – preview what will go to Buffer and send it; download
   every date as one `.ics` file (or add them to Google Calendar one by one).

Code layout: `app.py` (UI), `templates.py` (match types, prompts, tweet wording),
`helpers.py` (dates, time zones, links). Tweet wording is locked by golden tests in
`tests/golden_tweets.json`.

## Secrets (`.streamlit/secrets.toml` or the Streamlit Cloud dashboard)

```toml
GEMINI_API_KEY = "..."
BUFFER_API_KEY = "..."
BUFFER_CHANNEL_ID = "..."   # required – the Buffer channel to post to
```

## Run

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

## Notes

- All times are treated as UK time (Europe/London) and converted to UTC only when
  sent to Buffer, so reminders are correct during both GMT and BST.
- Reminders with a TBA time, a time in the past, or text over 280 characters are
  skipped rather than posted immediately. Each reminder is sent once per session.
- Hallmap links are generated from the opponent and kick-off time. Add an entry to
  `HALLMAP_ALIASES` in `helpers.py` if the site spells a club differently, or paste
  the correct link into the override box in the app.
- Scanning without `GEMINI_API_KEY` is disabled, but manual entry still works.
