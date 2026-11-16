"""Pure helper functions (no Streamlit) so they can be unit-tested.

All datetimes handled here are *naive UK local time* (Europe/London), because
that is how the LFC ticketing pages publish them. Conversion to UTC only
happens at the edge, when talking to Buffer.
"""

import hashlib
import re
import urllib.parse
from urllib.parse import urljoin, urlparse
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

UK_TZ = ZoneInfo("Europe/London")
UTC = ZoneInfo("UTC")

TBA = "TBA"
DEFAULT_TIME = (9, 0)  # used when a date has no time component


# --------------------------------------------------------------------------
# Clock
# --------------------------------------------------------------------------
def now_uk():
    """Current UK local time as a naive datetime (servers usually run in UTC)."""
    return datetime.now(UK_TZ).replace(tzinfo=None)


# --------------------------------------------------------------------------
# Parsing & formatting
# --------------------------------------------------------------------------
_WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]

_DATE_RE = re.compile(
    r"(?:(?P<wd>mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?[\s,]+)?"
    r"(?P<day>\d{1,2})(?:st|nd|rd|th)?\s+"
    r"(?P<mon>jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
    r"(?:\s+(?P<year>\d{4}))?"
    r"(?:[\s,\-–]+(?P<hour>\d{1,2})(?:[:.](?P<minute>\d{2}))?\s*(?P<ampm>am|pm))?",
    re.IGNORECASE,
)


def _infer_year(day, month, weekday, today):
    """Pick the year for a day/month that has no year.

    Prefer a year in which the stated weekday matches (e.g. "Wed 23 Sep"),
    then the candidate closest to today.
    """
    candidates = []
    for year in (today.year - 1, today.year, today.year + 1):
        try:
            candidates.append(date(year, month, day))
        except ValueError:  # e.g. 29 Feb in a non-leap year
            continue
    if not candidates:
        return None
    if weekday is not None:
        matching = [c for c in candidates if c.weekday() == weekday]
        if matching:
            candidates = matching
    return min(candidates, key=lambda c: abs((c - today).days)).year


def parse_to_datetime(date_str, today=None):
    """Parse strings like "Wed 23 Sep, 8:15am" into a naive UK datetime.

    Returns None for blanks, "TBA", placeholders like "[date]" or anything
    unparseable. Dates without a time default to 9:00am.
    """
    if not date_str:
        return None
    s = str(date_str).strip()
    if not s or s.upper() == TBA or "[" in s:
        return None

    m = _DATE_RE.search(s)
    if not m:
        return None

    day = int(m["day"])
    month = _MONTHS.index(m["mon"].lower()[:3]) + 1
    weekday = _WEEKDAYS.index(m["wd"].lower()[:3]) if m["wd"] else None

    if m["year"]:
        year = int(m["year"])
    else:
        year = _infer_year(day, month, weekday, today or now_uk().date())
        if year is None:
            return None

    if m["hour"]:
        hour = int(m["hour"]) % 12 + (12 if m["ampm"].lower() == "pm" else 0)
        minute = int(m["minute"] or 0)
    else:
        hour, minute = DEFAULT_TIME

    try:
        return datetime(year, month, day, hour, minute)
    except ValueError:
        return None


def format_time(dt):
    """8:15am style."""
    return dt.strftime("%I:%M%p").lstrip("0").lower()


def format_date(dt):
    """Wed 23 Sep style."""
    return dt.strftime("%a %d %b")


def format_datetime(dt):
    """Wed 23 Sep, 8:15am style (the format used throughout the tweets)."""
    return f"{format_date(dt)}, {format_time(dt)}"


def get_offset_time(date_str, hours_before=1):
    dt = parse_to_datetime(date_str)
    if not dt:
        return TBA
    return format_datetime(dt - timedelta(hours=hours_before))


def get_results_day_morning(date_str):
    dt = parse_to_datetime(date_str)
    if not dt:
        return TBA
    return format_datetime(dt.replace(hour=8, minute=30))


def today_label(date_str, hours_before=1):
    """Label for a reminder posted `hours_before` the event.

    If the reminder goes out on the same calendar day as the event this
    returns "Today, 5:00pm"; otherwise the original string unchanged.
    """
    dt = parse_to_datetime(date_str)
    if not dt:
        return date_str
    reminder = dt - timedelta(hours=hours_before)
    if reminder.date() == dt.date():
        return f"Today, {format_time(dt)}"
    return date_str


def day_label(date_str, post_str, fallback=None):
    """Label a date relative to the day a tweet is posted.

    Returns "Today, 10:00am" when `date_str` falls on the same calendar day as `post_str`
    (the tweet's posting time); otherwise `fallback` (default: `date_str` unchanged).
    """
    dt, post = parse_to_datetime(date_str), parse_to_datetime(post_str)
    if dt and post and dt.date() == post.date():
        return f"Today, {format_time(dt)}" if has_time(date_str) else "Today"
    return date_str if fallback is None else fallback


def has_time(date_str):
    """True if the string includes a clock time (as opposed to a bare date)."""
    s = str(date_str or "").lower()
    return any(m in s for m in ("am", "pm", ":"))


def to_display(dt, all_day=False):
    """Inverse of parse_to_datetime: datetime -> "Wed 23 Sep, 8:15am" / "Wed 23 Sep" / "TBA"."""
    if dt is None:
        return TBA
    return format_date(dt) if all_day else format_datetime(dt)


def is_set(value):
    return bool(value) and str(value).strip().upper() != TBA


# --------------------------------------------------------------------------
# Time zones
# --------------------------------------------------------------------------
def uk_to_utc(dt_local):
    """Treat a naive datetime as UK local time and return an aware UTC datetime."""
    return dt_local.replace(tzinfo=UK_TZ).astimezone(UTC)


def buffer_due_at(dt_local):
    """ISO-8601 UTC timestamp Buffer expects, from a naive UK local datetime."""
    return uk_to_utc(dt_local).strftime("%Y-%m-%dT%H:%M:%S.000Z")


# --------------------------------------------------------------------------
# Links
# --------------------------------------------------------------------------
def get_x_intent_url(text):
    return f"https://twitter.com/intent/tweet?text={urllib.parse.quote(text)}"


def format_gcal_dates(dt, is_all_day=False):
    if not dt:
        return None
    if is_all_day:
        return f"{dt:%Y%m%d}/{(dt + timedelta(days=1)):%Y%m%d}"
    start = dt.strftime("%Y%m%dT%H%M%S")
    return f"{start}/{start}"  # 0-minute duration (exact event time)


def gcal_url(title, dt, is_all_day=False):
    """Google Calendar 'add event' link, pinned to UK time."""
    dates = format_gcal_dates(dt, is_all_day)
    if not dates:
        return None
    return (
        "https://calendar.google.com/calendar/render?action=TEMPLATE"
        f"&text={urllib.parse.quote(title)}&dates={dates}&ctz=Europe/London"
    )


def _ics_escape(text):
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _ics_fold(line):
    """RFC 5545 line folding (75 octets max)."""
    out, current = [], ""
    for ch in line:
        if len((current + ch).encode()) > 74:
            out.append(current)
            current = " " + ch
        else:
            current += ch
    out.append(current)
    return "\r\n".join(out)


def build_ics(events):
    """One .ics file with every dated event, so the whole schedule imports in one go."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//LFC Alerts//Tweet Scheduler//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]
    for ev in events:
        dt = parse_to_datetime(ev["time"])
        if not dt:
            continue
        uid = hashlib.sha1(f"{ev['name']}|{dt.isoformat()}".encode()).hexdigest()
        lines += ["BEGIN:VEVENT", f"UID:{uid}@lfc-alerts", f"DTSTAMP:{stamp}", f"SUMMARY:{_ics_escape(ev['name'])}"]
        if ev.get("all_day"):
            lines += [f"DTSTART;VALUE=DATE:{dt:%Y%m%d}", f"DTEND;VALUE=DATE:{(dt + timedelta(days=1)):%Y%m%d}"]
        else:
            lines += [f"DTSTART:{uk_to_utc(dt):%Y%m%dT%H%M%SZ}", f"DTEND:{uk_to_utc(dt):%Y%m%dT%H%M%SZ}"]
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(_ics_fold(line) for line in lines) + "\r\n"


# Opponent names as the LFC ticketing site spells them in event URLs.
HALLMAP_ALIASES = {
    "brighton": "brighton - hove albion",
    "brighton & hove albion": "brighton - hove albion",
    "brighton and hove albion": "brighton - hove albion",
    "tottenham": "tottenham hotspur",
    "spurs": "tottenham hotspur",
    "bournemouth": "afc bournemouth",
    "man city": "manchester city",
    "man utd": "manchester united",
    "man united": "manchester united",
}

# Pin an exact hallmap URL here if the generated one is ever wrong
# (e.g. key "liverpool v xyz": "https://...").
HALLMAP_OVERRIDES = {}

HALLMAP_FALLBACK = "https://ticketing.liverpoolfc.com/"


def get_hallmap_link(opponent_name, match_date_str):
    """Build the Anfield hallmap URL from the opponent and kick-off time.

    URL pattern: /en-GB/events/liverpool%20v%20<opponent>/<Y>-<M>-<D>_<HH>.<MM>/anfield?hallmap
    (month and day are not zero padded).
    """
    key = (opponent_name or "").strip().lower()
    if not key:
        return HALLMAP_FALLBACK
    if key in HALLMAP_OVERRIDES:
        return HALLMAP_OVERRIDES[key]

    dt = parse_to_datetime(match_date_str)
    if not dt:
        return HALLMAP_FALLBACK

    slug_name = HALLMAP_ALIASES.get(key, key).replace("&", "-")
    slug_name = re.sub(r"\s+", " ", slug_name)
    event = urllib.parse.quote(f"liverpool v {slug_name}")
    when = f"{dt.year}-{dt.month}-{dt.day}_{dt:%H.%M}"
    return f"https://ticketing.liverpoolfc.com/en-GB/events/{event}/{when}/anfield?hallmap"


# --------------------------------------------------------------------------
# Tweet text
# --------------------------------------------------------------------------
TWEET_LIMIT = 280
_URL_RE = re.compile(r"https?://\S+")
# Code-point ranges X counts as weight 1; everything else (incl. "•" and emoji) counts as 2.
_LIGHT_RANGES = ((0x0000, 0x10FF), (0x2000, 0x200D), (0x2010, 0x201F), (0x2032, 0x2037))
_ZERO_WIDTH = {0xFE0E, 0xFE0F, 0x200D}


def tweet_length(text):
    """Approximate X's weighted character count (URLs = 23, emoji/CJK = 2)."""
    total = 23 * len(_URL_RE.findall(text))
    text = _URL_RE.sub("", text)
    for ch in text:
        cp = ord(ch)
        if cp in _ZERO_WIDTH:
            continue
        if any(lo <= cp <= hi for lo, hi in _LIGHT_RANGES):
            total += 1
        else:
            total += 2
    return total


# Words in LFC ticket URLs that don't identify the opponent.
_SLUG_STOPWORDS = {
    "liverpool", "tickets", "ticket", "match", "info", "english", "premier", "league", "home", "away",
    "carabao", "uefa", "champions", "europa", "conference", "community", "shield", "final", "semi",
    "quarter", "round", "group", "stage", "phase", "friendly", "women", "academy",
}


def _slug_tokens(path):
    return {t for t in re.split(r"[^a-z0-9]+", path.lower()) if len(t) > 3 and not t.isdigit() and t not in _SLUG_STOPWORDS}


def related_page_links(page_url, hrefs, limit=2):
    """Find the companion page for the same match.

    A match page (/tickets/match/...) often links to a ticket-info page (/tickets/ticket-info/...)
    with ACS dates, allocation and prices, and vice versa. Only links that share an opponent word
    with `page_url` (e.g. "bournemouth") are returned, so unrelated pages are ignored.
    """
    path = urlparse(page_url).path
    if "/tickets/match/" in path:
        want = "/tickets/ticket-info/"
    elif "/tickets/ticket-info/" in path:
        want = "/tickets/match/"
    else:
        return []
    tokens = _slug_tokens(path)
    if not tokens:
        return []

    found = []
    for href in hrefs:
        full = urljoin(page_url, href or "").split("#")[0].split("?")[0]
        parsed = urlparse(full)
        if not parsed.netloc.endswith("liverpoolfc.com") or want not in parsed.path:
            continue
        if full.rstrip("/") == page_url.split("?")[0].rstrip("/") or full in found:
            continue
        if tokens & _slug_tokens(parsed.path):
            found.append(full)
    return found[:limit]


def shorten_tier_name(tier_str):
    t_lower = tier_str.lower()
    if any(kw in t_lower for kw in ["wheelchair", "ambulant", "disabled"]):
        return None
    if "priority right" in t_lower or re.search(r"\bprh\b", t_lower):
        return None  # Priority Rights Holders sales are never tweeted

    num_match = re.search(r"(\d+)", tier_str)
    num = num_match.group(1) if num_match else ""

    if ("credit balance" in t_lower or "match credit" in t_lower or "games" in t_lower) and num:
        return f"{num}+ Credit Balance"
    if "all members" in t_lower:
        return "All Members"
    if "season ticket" in t_lower:
        return "Season Ticket Holders"

    cleaned = re.sub(
        r"(Season Ticket Holders and All Red Members with a.*?Balance of)", "", tier_str, flags=re.IGNORECASE
    ).strip()
    return cleaned or tier_str


def strip_json_fences(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text
