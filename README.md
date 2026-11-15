# LFC Ticket Alerts & Tweet Scheduler

Streamlit app that scans an LFC ticketing page with Gemini, lets you correct the
extracted dates, then generates tweet timelines, Buffer schedules and Google
Calendar links.

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
