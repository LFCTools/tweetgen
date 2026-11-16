from datetime import date, datetime

import pytest

import helpers
from helpers import (
    buffer_due_at,
    gcal_url,
    get_hallmap_link,
    get_offset_time,
    get_results_day_morning,
    parse_to_datetime,
    shorten_tier_name,
    strip_json_fences,
    today_label,
    tweet_length,
)

TODAY = date(2026, 11, 15)


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(helpers, "now_uk", lambda: datetime(2026, 11, 15, 12, 0))


# --- parsing -------------------------------------------------------------
@pytest.mark.parametrize(
    "text, expected",
    [
        ("Wed 23 Sep, 8:15am", datetime(2026, 9, 23, 8, 15)),
        ("wed 23 sep, 07:15am", datetime(2026, 9, 23, 7, 15)),
        ("Sat 21 Nov, 3:00pm", datetime(2026, 11, 21, 15, 0)),
        ("Sat 21 Nov 3pm", datetime(2026, 11, 21, 15, 0)),
        ("Sat 21st November, 12:30pm", datetime(2026, 11, 21, 12, 30)),
        ("Sat 21 Nov, 12:00am", datetime(2026, 11, 21, 0, 0)),
        ("Sat 21 Nov, 3:00 pm", datetime(2026, 11, 21, 15, 0)),
        ("Thu 01 Oct", datetime(2026, 10, 1, 9, 0)),  # no time -> 9am
        ("Sat 02 Jan, 3:00pm", datetime(2027, 1, 2, 15, 0)),
        ("2 Jan 2030, 3:00pm", datetime(2030, 1, 2, 15, 0)),
    ],
)
def test_parse(text, expected):
    assert parse_to_datetime(text) == expected


@pytest.mark.parametrize("text", [None, "", "TBA", "tba", "[Date]", "soon", "Wed 31 Feb"])
def test_parse_rejects(text):
    assert parse_to_datetime(text) is None


def test_year_uses_weekday_across_season_boundary():
    # Parsed in June 2027: an early-July pre-season date belongs to 2027, not 2026.
    june = date(2027, 6, 1)
    assert parse_to_datetime("Fri 09 Jul, 10:00am", today=june) == datetime(2027, 7, 9, 10, 0)
    # Weekday disambiguates: Thu 10 Jul only fits 2025 within +/-1 year of June 2026.
    assert parse_to_datetime("Thu 10 Jul", today=date(2026, 6, 1)).year == 2025
    # No weekday: nearest occurrence to today.
    assert parse_to_datetime("10 Jul", today=june).year == 2027


# --- offsets & labels ----------------------------------------------------
def test_offset_round_trips():
    assert get_offset_time("Wed 23 Sep, 8:15am", 1) == "Wed 23 Sep, 7:15am"
    assert get_offset_time("Wed 23 Sep, 8:15am", 0.5) == "Wed 23 Sep, 7:45am"
    assert parse_to_datetime(get_offset_time("Wed 23 Sep, 8:15am", 1)) == datetime(2026, 9, 23, 7, 15)
    assert get_offset_time("TBA") == "TBA"


def test_results_morning():
    assert get_results_day_morning("Thu 01 Oct") == "Thu 01 Oct, 8:30am"
    assert get_results_day_morning("TBA") == "TBA"


def test_today_label():
    assert today_label("Wed 23 Sep, 5:00pm") == "Today, 5:00pm"
    # Reminder for a 12:30am deadline goes out the previous evening -> keep full date.
    assert today_label("Wed 23 Sep, 12:30am") == "Wed 23 Sep, 12:30am"
    assert today_label("TBA") == "TBA"


# --- time zones ----------------------------------------------------------
def test_buffer_converts_uk_time_to_utc():
    # GMT (winter): no change
    assert buffer_due_at(datetime(2026, 11, 21, 15, 0)) == "2026-11-21T15:00:00.000Z"
    # BST (summer): UK 10:00 is 09:00 UTC
    assert buffer_due_at(datetime(2027, 3, 30, 10, 0)) == "2027-03-30T09:00:00.000Z"
    assert buffer_due_at(datetime(2026, 9, 23, 8, 15)) == "2026-09-23T07:15:00.000Z"


def test_gcal_url():
    url = gcal_url("Fulham (H) - Sale", datetime(2026, 9, 23, 8, 15))
    assert "dates=20260923T081500/20260923T081500" in url
    assert url.endswith("&ctz=Europe/London")
    assert "text=Fulham%20%28H%29%20-%20Sale" in url
    assert "dates=20261001/20261002" in gcal_url("x", datetime(2026, 10, 1, 9), is_all_day=True)


# --- hallmap -------------------------------------------------------------
OLD_HALLMAP_TABLE = {
    ("manchester city", "Sat 10 Oct, 3:00pm"): "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20manchester%20city/2026-10-10_15.00/anfield?hallmap",
    ("brighton", "Sat 24 Oct, 3:00pm"): "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20brighton%20-%20hove%20albion/2026-10-24_15.00/anfield?hallmap",
    ("Brighton & Hove Albion", "Sat 24 Oct, 3:00pm"): "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20brighton%20-%20hove%20albion/2026-10-24_15.00/anfield?hallmap",
    ("arsenal", "Sat 31 Oct, 3:00pm"): "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20arsenal/2026-10-31_15.00/anfield?hallmap",
    ("Manchester United", "Sat 21 Nov, 3:00pm"): "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20manchester%20united/2026-11-21_15.00/anfield?hallmap",
    ("sunderland", "Wed 02 Dec, 8:00pm"): "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20sunderland/2026-12-2_20.00/anfield?hallmap",
    ("leeds united", "Sat 12 Dec, 3:00pm"): "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20leeds%20united/2026-12-12_15.00/anfield?hallmap",
    ("tottenham", "Sat 19 Dec, 3:00pm"): "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20tottenham%20hotspur/2026-12-19_15.00/anfield?hallmap",
    ("coventry city", "Sat 02 Jan, 3:00pm"): "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20coventry%20city/2027-1-2_15.00/anfield?hallmap",
    ("crystal palace", "Sat 16 Jan, 3:00pm"): "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20crystal%20palace/2027-1-16_15.00/anfield?hallmap",
    ("everton", "Sat 30 Jan, 3:00pm"): "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20everton/2027-1-30_15.00/anfield?hallmap",
    ("hull city", "Sat 20 Feb, 3:00pm"): "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20hull%20city/2027-2-20_15.00/anfield?hallmap",
    ("aston villa", "Wed 03 Mar, 8:00pm"): "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20aston%20villa/2027-3-3_20.00/anfield?hallmap",
    ("ipswich town", "Sat 13 Mar, 3:00pm"): "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20ipswich%20town/2027-3-13_15.00/anfield?hallmap",
    ("newcastle united", "Sat 10 Apr, 3:00pm"): "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20newcastle%20united/2027-4-10_15.00/anfield?hallmap",
    ("chelsea", "Sat 01 May, 3:00pm"): "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20chelsea/2027-5-1_15.00/anfield?hallmap",
    ("brentford", "Sat 15 May, 3:00pm"): "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20brentford/2027-5-15_15.00/anfield?hallmap",
    ("bournemouth", "Sun 30 May, 3:00pm"): "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20afc%20bournemouth/2027-5-30_15.00/anfield?hallmap",
}


@pytest.mark.parametrize("args, expected", OLD_HALLMAP_TABLE.items())
def test_hallmap_generator_matches_old_hardcoded_table(args, expected):
    assert get_hallmap_link(*args) == expected


def test_hallmap_fallback():
    assert get_hallmap_link("Fulham", "TBA") == helpers.HALLMAP_FALLBACK
    assert get_hallmap_link("", "Sat 21 Nov, 3:00pm") == helpers.HALLMAP_FALLBACK


# --- tweet text ----------------------------------------------------------
def test_tweet_length():
    assert tweet_length("hello") == 5
    assert tweet_length("• a") == 4  # bullet counts double
    assert tweet_length("📢") == 2
    assert tweet_length("see https://example.com/a/very/long/path?x=1") == 4 + 23
    assert tweet_length("⏰\n") == 3


def test_shorten_tier_name():
    assert shorten_tier_name("Wheelchair users") is None
    assert shorten_tier_name("Season Ticket Holders and All Red Members with a Match Credit Balance of 9+") == "9+ Credit Balance"
    assert shorten_tier_name("All Members") == "All Members"
    assert shorten_tier_name("Season Ticket Holders") == "Season Ticket Holders"


def test_strip_json_fences():
    assert strip_json_fences('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert strip_json_fences('{"a": 1}') == '{"a": 1}'


# --- ACS dates straight from page text ---------------------------------------
@pytest.mark.parametrize(
    "text, expected",
    [
        ("Auto Cup Scheme Auto Cup payments will be processed between Thursday 26 November - Friday 27 November. Ticket prices",
         ("Thu 26 Nov", "Fri 27 Nov")),
        ("ACS members will be charged on Tuesday 1 December.", ("Tue 01 Dec", "Tue 01 Dec")),
        ("Auto Cup Scheme payments will be taken between Mon 30 Nov and Wed 2 Dec.", ("Mon 30 Nov", "Wed 02 Dec")),
        ("Need to update your payment card? Credit or Debit Card - ACS payment option should be selected.", (None, None)),
        ("No scheme here.", (None, None)),
    ],
)
def test_find_acs_dates(text, expected):
    from helpers import find_acs_dates
    assert find_acs_dates(text, today=TODAY) == expected
