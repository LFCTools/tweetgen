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
    prices = raw.get("prices", "")
    d["allocation"] = raw.get("allocation", "")
    d["prices"] = "\n".join(prices) if isinstance(prices, list) else prices
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


# --- short sales: no closing reminder if the sale closes within 4 hours -----
@pytest.mark.parametrize(
    "open_, close, expect_reminder",
    [
        ("Fri 20 Nov, 10:00am", "Fri 20 Nov, 1:00pm", False),  # 3h
        ("Fri 20 Nov, 10:00am", "Fri 20 Nov, 2:00pm", False),  # exactly 4h
        ("Fri 20 Nov, 10:00am", "Fri 20 Nov, 2:01pm", True),   # just over 4h
        ("Fri 20 Nov, 10:00am", "Sat 21 Nov, 10:00am", True),  # next day
        ("Fri 20 Nov, 10:00am", "TBA", False),                 # no close time at all
        ("TBA", "Fri 20 Nov, 1:00pm", True),                   # unknown open -> keep reminder
    ],
)
@pytest.mark.parametrize("match_type", list(MATCH_TYPES))
def test_short_sale_has_no_closing_reminder(match_type, open_, close, expect_reminder):
    raw = dict(FAKES[OLD_TAB[match_type]])
    raw["sales"] = [{"tier": "All Members", "open": open_, "close": close, "info": "", "forwarding_deadline": "TBA"}]
    d = details_from(match_type, raw)
    titles = [t["title"] for t in MATCH_TYPES[match_type]["tweets"](d)]
    assert ("⏰ Sale Closing (All Members)" in titles) == expect_reminder
    # The calendar still gets the closing time whenever it is known.
    labels = [e["label"] for e in MATCH_TYPES[match_type]["events"](d)]
    assert ("Sale Close (All Members)" in labels) == (close != "TBA")


def test_footer_on_every_tweet():
    from templates import FOOTER
    for match_type, tab in OLD_TAB.items():
        for t in MATCH_TYPES[match_type]["tweets"](details_from(match_type, FAKES[tab])):
            assert t["text"].endswith("\n\n" + FOOTER)
            assert "\n\n\n" not in t["text"]


def test_links_time_only():
    from templates import links_from_time
    assert links_from_time("Fri 20 Nov, 11:00am") == "10:30am"
    assert links_from_time("Fri 20 Nov, 12:15pm") == "11:45am"
    assert links_from_time("TBA") == "TBA"


# --- images ---------------------------------------------------------------
def _image_by_title(match_type):
    tab = OLD_TAB[match_type]
    return {t["title"]: t["image"] for t in MATCH_TYPES[match_type]["tweets"](details_from(match_type, FAKES[tab]))}


def test_pl_images():
    imgs = _image_by_title("pl_home")
    assert imgs["📢 Sales Detail Announcement"] == "sales_details"
    assert imgs["📝 Registration Opening Notice"] == imgs["⏰ Registration Closing Reminder"] == "registration"
    assert imgs["🗳️ Ballots Opening"] == imgs["⏰ Ballots Closing Reminder"] == imgs["✨ Local & YA Ballot Results"] == "local_ya_ballot"
    assert imgs["🎟️ Sale Opening (All Members)"] == imgs["⏰ Sale Closing (All Members)"] == "sale_reminder"


def test_cup_images():
    imgs = _image_by_title("cup_home")
    assert imgs["📢 Cup Sales Announcement"] == "sales_details"
    assert imgs["🗳️ Local Ballot Opening"] == imgs["⏰ Local Ballot Closing"] == "local_ballot"
    assert all(v == "sale_reminder" for k, v in imgs.items() if "Sale Opening" in k or "Sale Closing" in k)


@pytest.mark.parametrize("match_type", ["league_away", "euro_away"])
def test_away_images(match_type):
    for title, image in _image_by_title(match_type).items():
        if "Announcement" in title:
            assert image == "sales_details"
        elif "Sale Opening" in title or "Sale Closing" in title:
            assert image == "sale_reminder"
        elif "Forwarding" in title:
            assert image is None


def test_image_files_exist():
    from templates import IMAGES
    images_dir = Path(__file__).resolve().parent.parent / "images"
    for _, filename in IMAGES.values():
        f = images_dir / filename
        assert f.exists() and f.stat().st_size < 5_000_000, filename


# --- "Today" for anything on the same day the tweet is posted --------------
def test_day_label():
    from helpers import day_label
    post = "Mon 16 Nov, 7:15am"
    assert day_label("Mon 16 Nov, 10:00am", post) == "Today, 10:00am"
    assert day_label("Mon 16 Nov", post) == "Today"
    assert day_label("Tue 17 Nov, 10:00am", post) == "Tue 17 Nov, 10:00am"
    assert day_label("Tue 17 Nov, 10:00am", post, fallback="X") == "X"
    assert day_label("TBA", post) == "TBA"


@pytest.mark.parametrize("match_type", ["cup_home", "league_away", "euro_away"])
def test_same_day_sale_says_today_for_open_and_close(match_type):
    raw = dict(FAKES[OLD_TAB[match_type]])
    raw["sales"] = [{"tier": "All Members", "open": "Mon 16 Nov, 8:15am", "close": "Mon 16 Nov, 10:00am",
                     "info": "Subject to availability", "forwarding_deadline": "TBA"}]
    tweets = MATCH_TYPES[match_type]["tweets"](details_from(match_type, raw))
    sale = next(t for t in tweets if t["title"].startswith("🎟️ Sale Opening"))
    assert "• Opens: Today, 8:15am" in sale["text"]
    assert "• Closes: Today, 10:00am" in sale["text"]


def test_euro_away_close_on_another_day_keeps_date():
    raw = dict(FAKES["cl"])
    raw["sales"] = [{"tier": "9+ Games", "open": "Mon 16 Nov, 8:15am", "close": "Tue 17 Nov, 10:00am",
                     "info": "", "forwarding_deadline": "TBA"}]
    sale = MATCH_TYPES["euro_away"]["tweets"](details_from("euro_away", raw))[1]
    assert "• Closes: Tue 17 Nov - 10:00am" in sale["text"]


def test_euro_away_forwarding_deadline_is_in_sale_tweet_only():
    raw = dict(FAKES["cl"])
    raw["sales"] = [{"tier": "9+ Games", "open": "Mon 16 Nov, 8:15am", "close": "Mon 16 Nov, 10:00am",
                     "info": "Guaranteed Sale", "forwarding_deadline": "Mon 16 Nov, 11:00am"},
                    {"tier": "5+ Games", "open": "Tue 17 Nov, 8:15am", "close": "Tue 17 Nov, 4:00pm",
                     "info": "", "forwarding_deadline": "Wed 18 Nov, 11:00am"}]
    d = details_from("euro_away", raw)
    tweets = MATCH_TYPES["euro_away"]["tweets"](d)
    assert not any("Forwarding" in t["title"] for t in tweets)
    sales = [t["text"] for t in tweets if t["title"].startswith("🎟️ Sale Opening")]
    assert "• Closes: Today, 10:00am\n• Forwarding deadline: Today, 11:00am\n• Guaranteed Sale" in sales[0]
    assert "• Forwarding deadline: Wed 18 Nov - 11:00am" in sales[1]
    # Still in the calendar.
    assert any(e["label"].startswith("Forwarding Deadline") for e in MATCH_TYPES["euro_away"]["events"](d))


# --- cup ballot results, PRH, allocation & prices, companion pages ----------
def test_cup_ballot_results_tweet_and_lines():
    raw = dict(FAKES["cup"], ballot_results="Thu 26 Nov")
    tweets = {t["title"]: t for t in MATCH_TYPES["cup_home"]["tweets"](details_from("cup_home", raw))}
    res = tweets["✨ Local Ballot Results"]
    assert res["when"] == "Thu 26 Nov, 8:30am" and res["image"] == "local_ballot"
    assert "Local Ballot Results 🗳️\n• Results today" in res["text"]
    for title in ("🗳️ Local Ballot Opening", "⏰ Local Ballot Closing"):
        assert "• Results: Thu 26 Nov" in tweets[title]["text"]


def test_priority_rights_holders_never_tweeted():
    for match_type, tab in OLD_TAB.items():
        raw = dict(FAKES[tab])
        raw["sales"] = raw["sales"] + [{"tier": "Priority Rights Holders", "open": "Mon 23 Nov, 9:00am", "close": "TBA"},
                                       {"tier": "PRH Sale", "open": "Mon 23 Nov, 9:00am", "close": "TBA"}]
        for t in MATCH_TYPES[match_type]["tweets"](details_from(match_type, raw)):
            assert "priority" not in t["text"].lower() and "PRH" not in t["text"]


def test_allocation_block_skips_unknowns():
    from templates import allocation_block
    assert allocation_block({"allocation": "TBA", "prices": ""}) == ""
    assert allocation_block({"allocation": "", "prices": "• Adults £30\n\nTBA\n"}) == "\n\nPrices 💷\n• Adults £30"


def test_related_page_links():
    from helpers import related_page_links
    match = "https://www.liverpoolfc.com/tickets/match/liverpool-v-bournemouth-english-league-cup-20261216"
    info = "https://www.liverpoolfc.com/tickets/ticket-info/2026-27/league-cup-home/bournemouth-home-carabao-cup-2627"
    hrefs = [
        "/tickets/ticket-info/2026-27/league-cup-home/bournemouth-home-carabao-cup-2627",
        "/tickets/ticket-info/2026-27/general-ticketing-info",
        "/tickets/ticket-info/2026-27/league-cup-home/chelsea-home-carabao-cup-2627",
        "https://example.com/tickets/ticket-info/bournemouth",
        "#top",
    ]
    assert related_page_links(match, hrefs) == [info]
    assert related_page_links(info, ["/tickets/match/liverpool-v-bournemouth-english-league-cup-20261216"]) == [match]
    assert related_page_links("https://www.liverpoolfc.com/news/x", hrefs) == []


def test_without_footer_for_discord():
    from templates import FOOTER, with_footer, without_footer
    assert without_footer(with_footer("Hello\n• line")) == "Hello\n• line"
    assert without_footer("Edited by hand, no footer") == "Edited by hand, no footer"
    for match_type, tab in OLD_TAB.items():
        for t in MATCH_TYPES[match_type]["tweets"](details_from(match_type, FAKES[tab])):
            assert "following this account" not in without_footer(t["text"])
            assert "#lfc" not in without_footer(t["text"])
