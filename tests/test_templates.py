"""Golden tests: tweet wording must match what the app produced before the redesign."""
import json
from datetime import datetime
from pathlib import Path

import pytest

import helpers
from fixtures import FAKES
from helpers import build_ics, get_hallmap_link, parse_to_datetime, shorten_tier_name, to_display, has_time
from templates import MATCH_TYPES

GOLDEN = json.loads((Path(__file__).parent / "golden_tweets.json").read_text())
OLD_TAB = {"pl_home": "pl", "cup_home": "cup", "league_away": "away", "euro_away": "cl"}


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(helpers, "now_uk", lambda: datetime(2026, 11, 15, 12, 0))


def details_from(match_type, raw):
    cfg = MATCH_TYPES[match_type]
    d = {"name": raw["match_name"], "competition": raw.get("competition", "")}
    for key, _, _ in cfg["dates"]:
        d[key] = raw.get(key, "TBA")
    d["sales"] = []
    for s in raw["sales"]:
        tier = shorten_tier_name(s["tier"])
        if tier:
            d["sales"].append({**s, "tier": tier, "info": s.get("info", ""),
                               "forwarding_deadline": s.get("forwarding_deadline", "TBA")})
    d["hallmap_url"] = get_hallmap_link(d["name"], d.get("match_date"))
    return d


@pytest.mark.parametrize("match_type", list(MATCH_TYPES))
def test_tweets_match_golden(match_type):
    tab = OLD_TAB[match_type]
    tweets = MATCH_TYPES[match_type]["tweets"](details_from(match_type, FAKES[tab]))
    assert [[t["title"], t["when"], t["text"]] for t in tweets] == GOLDEN[tab]


@pytest.mark.parametrize("match_type", list(MATCH_TYPES))
def test_events_build(match_type):
    events = MATCH_TYPES[match_type]["events"](details_from(match_type, FAKES[OLD_TAB[match_type]]))
    assert events[-1]["label"] == "Match Day"


@pytest.mark.parametrize("text", ["Fri 20 Nov, 10:00am", "Thu 19 Nov, 12:30am", "Fri 20 Nov", "Tue 01 Dec", "Sat 21 Nov, 3:00pm"])
def test_display_round_trip(text):
    """Values coming back out of the date editor must render exactly as the scanned strings did."""
    assert to_display(parse_to_datetime(text), all_day=not has_time(text)) == text


def test_to_display_tba():
    assert to_display(None) == "TBA"


def test_ics():
    ics = build_ics([
        {"name": "Everton (A) - Sale Opens (9+, test)", "time": "Mon 23 Nov, 9:00am", "all_day": False},
        {"name": "ACS", "time": "Tue 01 Dec", "all_day": True},
        {"name": "Skipped", "time": "TBA", "all_day": False},
        {"name": "Summer", "time": "Wed 23 Sep, 8:15am", "all_day": False},
    ])
    assert ics.startswith("BEGIN:VCALENDAR\r\n") and ics.endswith("END:VCALENDAR\r\n")
    assert ics.count("BEGIN:VEVENT") == 3
    assert "SUMMARY:Everton (A) - Sale Opens (9+\\, test)" in ics
    assert "DTSTART:20261123T090000Z" in ics
    assert "DTSTART;VALUE=DATE:20261201" in ics and "DTEND;VALUE=DATE:20261202" in ics
    assert "DTSTART:20260923T071500Z" in ics  # BST -> UTC
    assert all(len(line.encode()) <= 75 for line in ics.split("\r\n"))
