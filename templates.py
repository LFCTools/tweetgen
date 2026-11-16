"""Match types, Gemini prompts and tweet / calendar templates (no Streamlit).

Every builder takes a `details` dict of display strings, e.g.
    {"name": "Fulham", "reg_close": "Thu 19 Nov, 5:00pm", "links_sent": "Fri 20 Nov",
     "sales": [{"tier": "All Members", "open": "...", "close": "TBA"}], ...}
Dates are "Wed 23 Sep, 8:15am", "Wed 23 Sep" (all day) or "TBA".
"""

from datetime import timedelta

from helpers import TBA, format_time, get_offset_time, get_results_day_morning, is_set, parse_to_datetime, today_label

IMMEDIATE = "Immediate / Upon Scanning"
BALLOTS_URL = "https://ticketing.liverpoolfc.com/tickets/ballots"

# Added to the bottom of every tweet.
FOOTER = "Make sure you're following this account for all ticket reminders & updates 🔔\n\n#lfc #liverpoolfc #lfctickets #anfield"

# A sale that closes within this long of opening gets no separate closing reminder.
SHORT_SALE = timedelta(hours=4)


# --------------------------------------------------------------------------
# Prompts
# --------------------------------------------------------------------------
COMMON_RULES = """
Respond ONLY with a valid JSON object matching the structure below.
Use abbreviated days (e.g., Wed) and months (e.g., Nov) and format times like 10:00am or 11:00am.
If any field is missing, make its value "TBA". Never invent dates that are not in the source text.
"""

CLASSIFY_PROMPT = """
You are given raw text from a Liverpool FC ticketing web page. Classify which kind of match sale it is:
- "pl_home": Premier League match at Anfield
- "cup_home": League Cup, FA Cup or European (Champions League / Europa League) match at Anfield
- "league_away": Premier League or domestic cup away match
- "euro_away": Champions League / European away match
Respond ONLY with JSON: {"match_type": "<one of the four values>"}
Source Text: <<PAGE_TEXT>>
"""

PL_PROMPT = """
Analyze the following raw text from an LFC Premier League ticket page.
CRITICAL INSTRUCTION: Exclude any sales tiers mentioning wheelchair, ambulant, or disabled access.
CRITICAL INSTRUCTION FOR 'links_sent': Look inside the 'TICKET SALE' or sale details section for sentences mentioning 'unique link'. Extract that exact date/time.
Extract unified registration details, local & YA ballot open/close/results dates, and ticket sale opening dates per tier.
""" + COMMON_RULES + """
Desired JSON Format:
{
  "match_name": "Only the opponent team name (e.g., Fulham)",
  "reg_open": "Day Date, Time",
  "reg_close": "Day Date, Time",
  "links_sent": "Day Date",
  "sales": [
    {"tier": "All Members", "open": "Day Date, Time", "close": "Day Date, Time"}
  ],
  "ballot_open": "Day Date, Time",
  "ballot_close": "Day Date, Time",
  "ballot_results": "TBA or Day Date",
  "match_date": "Day Date, Time"
}
Source Text: <<PAGE_TEXT>>
"""

CUP_PROMPT = """
Analyze the following raw text from an LFC Cup or European Home match ticket page. Exclude any wheelchair, ambulant, or disabled sales tiers.
Extract opponent name, competition type (determine whether it is 'League Cup', 'FA Cup', or 'Champions League' / 'European Home' based on the text), sales tiers with open/close times, local ballot dates, and ACS dates.
""" + COMMON_RULES + """
Desired JSON Format:
{
  "match_name": "Only the opponent team name (e.g., Spurs)",
  "competition": "League Cup, FA Cup, or Champions League",
  "sales": [
    {"tier": "Credit balance of 2", "open": "Day Date, Time", "close": "Day Date, Time"}
  ],
  "ballot_open": "Day Date, Time",
  "ballot_close": "Day Date, Time",
  "ballot_results": "TBA or Day Date",
  "acs_start": "Day Date",
  "acs_end": "Day Date",
  "match_date": "Day Date, Time"
}
Source Text: <<PAGE_TEXT>>
"""

AWAY_PROMPT = """
Analyze the following raw text from an LFC Away match ticket page. Exclude any wheelchair, ambulant, or disabled sales tiers.
Extract opponent name, sales tiers with open/close times, and forwarding deadline if present.
""" + COMMON_RULES + """
Desired JSON Format:
{
  "match_name": "Only the opponent team name (e.g., Lask)",
  "sales": [
    {"tier": "9+ Away Credit Balance", "open": "Day Date, Time", "close": "Day Date, Time"}
  ],
  "forwarding_deadline": "Day Date, Time",
  "match_date": "Day Date, Time"
}
Source Text: <<PAGE_TEXT>>
"""

CL_PROMPT = """
Analyze the following raw text from an LFC Champions League / European Away match ticket page. Exclude any wheelchair, ambulant, or disabled sales tiers.
Extract opponent name, match date, and sales tiers.
CRITICAL INSTRUCTIONS:
1. For 'tier', simplify the criteria name to something clean like '9+ Games' or '9+ Away Credit Balance' (e.g., if it says 'with a European Away Match Credit Balance of 9 or more', make tier '9+ Games').
2. Look inside EACH sale section for the sentence mentioning 'Forwarding Deadline' and extract its exact date and time into 'forwarding_deadline' for that tier.
3. For 'info', extract whether it states 'Guaranteed Sale' (or 'guaranteed'), 'Subject to availability', or leave empty if not stated.
""" + COMMON_RULES + """
Desired JSON Format:
{
  "match_name": "Only the opponent team name (e.g., LASK)",
  "sales": [
    {
      "tier": "9+ Games",
      "open": "Wed 23 Sep, 8:15am",
      "close": "Thu 24 Sep, 7:30am",
      "info": "Guaranteed Sale",
      "forwarding_deadline": "Thu 24 Sep, 11:00am"
    }
  ],
  "match_date": "Wed 14 Oct, 5:45pm"
}
Source Text: <<PAGE_TEXT>>
"""


# --------------------------------------------------------------------------
# Small builders
# --------------------------------------------------------------------------
def tweet(title, when, text, buffer=True):
    """A timeline item. `when` is the display string for when it should be posted."""
    return {"title": title, "when": when, "text": text, "buffer": buffer}


def event(label, name, time, all_day=False):
    return {"label": label, "name": name, "time": time, "all_day": all_day}


def with_footer(text):
    return f"{text.rstrip()}\n\n{FOOTER}"


def is_short_sale(open_str, close_str):
    """True if the sale closes within SHORT_SALE of opening."""
    opens, closes = parse_to_datetime(open_str), parse_to_datetime(close_str)
    return bool(opens and closes) and timedelta(0) <= closes - opens <= SHORT_SALE


def links_from_time(open_str):
    """Time unique links go out (30 minutes before the sale), e.g. "10:30am"."""
    opens = parse_to_datetime(open_str)
    return format_time(opens - timedelta(minutes=30)) if opens else TBA


def sale_close_reminder(match_prefix, tier, close, open_=None):
    if not is_set(close) or is_short_sale(open_, close):
        return None
    text = f"""{match_prefix} ⏰\n\nSale Closing Reminder ({tier})\n• Closes: {today_label(close)}"""
    return tweet(f"⏰ Sale Closing ({tier})", get_offset_time(close, hours_before=1), text)


def sale_events(match_prefix, sales):
    events = []
    for s in sales:
        events.append(event(f"Sale Open ({s['tier']})", f"{match_prefix} - Sale Opens ({s['tier']})", s["open"]))
        if is_set(s.get("close")):
            events.append(event(f"Sale Close ({s['tier']})", f"{match_prefix} - Sale Closes ({s['tier']})", s["close"]))
    return events


# --------------------------------------------------------------------------
# Premier League (home)
# --------------------------------------------------------------------------
def pl_tweets(d):
    name, sales = d["name"], d["sales"]
    r_open, r_close, l_sent = d["reg_open"], d["reg_close"], d["links_sent"]
    b_open, b_close, b_res, m_date = d["ballot_open"], d["ballot_close"], d["ballot_results"], d["match_date"]
    hallmap_url = d["hallmap_url"]

    sales_text = "".join(f"• {s['tier']}: {s['open']}\n" for s in sales)
    first_sale = sales[0]["open"] if sales else TBA

    announcement = f"""{name} (H) - Sale Details 📢\n\nRegistration (All Members) 📝\n• Opens: {r_open}\n• Closes: {r_close}\n• Sale links sent: {l_sent}\n\nSales 🎟️\n{sales_text}\nLocal & YA Ballots 🗳️\n• Opens: {b_open}\n• Closes: {b_close}\n• Results: {b_res}\n\nMatch Date • {m_date} 🏟️"""
    reg_open_tweet = f"""{name} (H) - Registration 📢\n\nRegistration (All Members) 📝\n• Opens: Now\n• Closes: {r_close}\n\n• Sale links sent: {l_sent}\n\nSale 🎟️\n• {first_sale}"""
    reg_close_tweet = f"""{name} (H) - Registration 📢\n\nRegistration (All Members) 📝\n• Opens: Now\n• Closes: {today_label(r_close)}\n\n• Sale links sent: {l_sent}\n\nSale 🎟️\n• {first_sale}"""
    ballots_open_tweet = f"""{name} (H) - Ballots 📢\n\nLocal Ballots & YA Ballot 🗳️\n• Opens: Now\n• Closes: {b_close}\n\n• Results: {b_res}\n\n{BALLOTS_URL}"""
    ballots_close_tweet = f"""{name} (H) - Ballots 📢\n\nLocal Ballots & YA Ballot 🗳️\n• Opens: Now\n• Closes: {today_label(b_close)}\n\n• Results: {b_res}\n\n{BALLOTS_URL}"""
    ballots_res_tweet = f"""{name} (H) - Local & YA Ballots 📢\n\nLocal & YA Ballot Results 🗳️\n• Results today\n• Ensure you have funds in your bank \n\nComment below if successful 👇"""

    tweets = [
        tweet("📢 Sales Detail Announcement", IMMEDIATE, announcement, buffer=False),
        tweet("📝 Registration Opening Notice", r_open, reg_open_tweet),
        tweet("⏰ Registration Closing Reminder", get_offset_time(r_close, 1), reg_close_tweet),
        tweet("🗳️ Ballots Opening", b_open, ballots_open_tweet),
        tweet("⏰ Ballots Closing Reminder", get_offset_time(b_close, 1), ballots_close_tweet),
        tweet("✨ Local & YA Ballot Results", get_results_day_morning(b_res), ballots_res_tweet),
    ]
    for s in sales:
        link_time = links_from_time(s["open"])
        rem_tweet = f"""{name} (H) - {s['tier']} 📢\n\n{s['tier']} 🎟️\n• Opens: {today_label(s['open'])}\n• Click unique links from {link_time}\n\nHallmap link  👇\n{hallmap_url}"""
        tweets.append(tweet(f"🎟️ Sale Opening ({s['tier']})", get_offset_time(s["open"], 1), rem_tweet))
        close = sale_close_reminder(f"{name} (H)", s["tier"], s["close"], s["open"])
        if close:
            tweets.append(close)
    return tweets


def pl_events(d):
    p = f"{d['name']} (H)"
    return [
        event("Registration Open", f"{p} - Registration Opens", d["reg_open"]),
        event("Registration Closes", f"{p} - Registration Closes", d["reg_close"]),
        event("Unique Links Sent", f"{p} - Unique Links Sent", d["links_sent"]),
        event("Ballots Open", f"{p} - Ballots Open", d["ballot_open"]),
        event("Ballots Close", f"{p} - Ballots Close", d["ballot_close"]),
        event("Ballots Results", f"{p} - Ballots Results", d["ballot_results"], all_day=True),
        *sale_events(p, d["sales"]),
        event("Match Day", f"{p} - Match Date", d["match_date"]),
    ]


# --------------------------------------------------------------------------
# Cup / European (home)
# --------------------------------------------------------------------------
def cup_tweets(d):
    name, comp, sales = d["name"], d["competition"], d["sales"]
    b_open, b_close, b_res = d["ballot_open"], d["ballot_close"], d["ballot_results"]
    acs_start, acs_end, m_date = d["acs_start"], d["acs_end"], d["match_date"]

    sales_text = "".join(f"Sale ({s['tier']})\n• Opens: {s['open']}\n• Closes: {s['close']}\n\n" for s in sales)
    announcement = f"""{name} (H) - Sale Details 📢\n\n{sales_text}Local Ballot 🗳️\n• Opens: {b_open}\n• Closes: {b_close}\n• Results: {b_res}\n\nACS Payment Run 💰\n• {acs_start} - {acs_end}\n\nMatch Date • {m_date} 🏟️"""

    tweets = [
        tweet("📢 Cup Sales Announcement", IMMEDIATE, announcement, buffer=False),
        tweet(
            "🗳️ Local Ballot Opening",
            b_open,
            f"""{name} (H) - Local Ballot 📢\n\nLocal Ballot 🗳️\n• Opens: Now\n• Closes: {b_close}\n\n{BALLOTS_URL}""",
        ),
        tweet(
            "⏰ Local Ballot Closing",
            get_offset_time(b_close, 1),
            f"""{name} (H) - Local Ballot 📢\n\nLocal Ballot 🗳️\n• Opens: Now\n• Closes: {today_label(b_close)}\n\n{BALLOTS_URL}""",
        ),
    ]
    for s in sales:
        open_tweet = f"""{name} ({comp})  🎟️\n\nSale ({s['tier']})\n• Opens: {today_label(s['open'])}\n• Closes: {s['close']}\n\nNo registration needed, pre-queue starts 30 minutes before."""
        tweets.append(tweet(f"🎟️ Sale Opening ({s['tier']})", get_offset_time(s["open"], 1), open_tweet))
        close = sale_close_reminder(f"{name} ({comp})", s["tier"], s["close"], s["open"])
        if close:
            tweets.append(close)
    return tweets


def cup_events(d):
    p = f"{d['name']} (H)"
    return [
        event("Local Ballot Opens", f"{p} - Local Ballot Opens", d["ballot_open"]),
        event("Local Ballot Closes", f"{p} - Local Ballot Closes", d["ballot_close"]),
        event("Local Ballot Results", f"{p} - Local Ballot Results", d["ballot_results"], all_day=True),
        event("ACS Payment Start", f"{p} - ACS Payment Start", d["acs_start"], all_day=True),
        event("ACS Payment End", f"{p} - ACS Payment End", d["acs_end"], all_day=True),
        *sale_events(p, d["sales"]),
        event("Match Day", f"{p} - Match Date", d["match_date"]),
    ]


# --------------------------------------------------------------------------
# League aways
# --------------------------------------------------------------------------
def away_tweets(d):
    name, sales, fwd, m_date = d["name"], d["sales"], d["forwarding_deadline"], d["match_date"]
    blocks = "\n\n".join(f"Sale ({s['tier']})\n• Opens: {s['open']}\n• Closes: {s['close']}" for s in sales)
    announcement = f"""{name} (A) - Sale Details 📢\n\n{blocks}\n\nMatch Date • {m_date} 🏟️"""

    tweets = [tweet("📢 Sales Detail Announcement", IMMEDIATE, announcement, buffer=False)]
    for s in sales:
        sale_tweet = f"""{name} (A) 🎟️\n\nSale ({s['tier']})\n• Opens: {today_label(s['open'])}\n• Closes: {s['close']}\n• Guaranteed Sale"""
        tweets.append(tweet(f"🎟️ Sale Opening ({s['tier']})", get_offset_time(s["open"], 1), sale_tweet))
        close = sale_close_reminder(f"{name} (A)", s["tier"], s["close"], s["open"])
        if close:
            tweets.append(close)
    if is_set(fwd):
        fwd_tweet = f"""Forwarding Deadline ➡️\n• Closes: {today_label(fwd)}"""
        tweets.append(tweet("➡️ Forwarding Deadline", get_offset_time(fwd, 1), fwd_tweet))
    return tweets


def away_events(d):
    p = f"{d['name']} (A)"
    events = sale_events(p, d["sales"])
    if is_set(d["forwarding_deadline"]):
        events.append(event("Forwarding Deadline", f"{p} - Forwarding Deadline", d["forwarding_deadline"]))
    events.append(event("Match Day", f"{p} - Match Date", d["match_date"]))
    return events


# --------------------------------------------------------------------------
# European aways
# --------------------------------------------------------------------------
def cl_tweets(d):
    name, sales, m_date = d["name"], d["sales"], d["match_date"]
    blocks = []
    for s in sales:
        info = s.get("info", "").strip()
        if "guaranteed" in info.lower() and "non" not in info.lower():
            info_tag = " - Guaranteed"
        elif info:
            info_tag = f" - {info}"
        else:
            info_tag = ""
        blocks.append(f"Sale ({s['tier']}{info_tag})\n• Opens: {s['open']}\n• Closes: {s['close']}")
    blocks_text = "\n\n".join(blocks)
    announcement = f"""{name} (A) - Sale Details 📢\n\n{blocks_text}\n\nMatch Date • {m_date} 🏟️"""

    tweets = [tweet("📢 CL Away Sales Details Announcement", IMMEDIATE, announcement, buffer=False)]
    for s in sales:
        info = s.get("info", "").strip()
        info_line = f"• {info}\n" if info else ""
        sale_tweet = f"""{name} (A) 🎟️\n\nSale ({s['tier']})\n• Opens: {today_label(s['open'])}\n• Closes: {s['close'].replace(',', ' -')}\n{info_line}"""
        tweets.append(tweet(f"🎟️ Sale Opening ({s['tier']})", get_offset_time(s["open"], 1), sale_tweet))
        close = sale_close_reminder(f"{name} (A)", s["tier"], s["close"], s["open"])
        if close:
            tweets.append(close)
        fwd = s.get("forwarding_deadline", TBA)
        if is_set(fwd):
            fwd_tweet = f"""{name} (A) ➡️\n\nForwarding Deadline ({s['tier']})\n• Closes: {today_label(fwd)}"""
            tweets.append(tweet(f"➡️ Forwarding Deadline ({s['tier']})", get_offset_time(fwd, 1), fwd_tweet))
    return tweets


def cl_events(d):
    p = f"{d['name']} (A)"
    events = sale_events(p, d["sales"])
    for s in d["sales"]:
        if is_set(s.get("forwarding_deadline")):
            events.append(
                event(f"Forwarding Deadline ({s['tier']})", f"{p} - Forwarding Deadline ({s['tier']})", s["forwarding_deadline"])
            )
    events.append(event("Match Day", f"{p} - Match Date", d["match_date"]))
    return events


def footered(builder):
    def build(d):
        return [{**t, "text": with_footer(t["text"])} for t in builder(d)]

    build.__name__ = builder.__name__
    return build


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------
# Each date field: (key, label, all-day by default). Order is the order shown in the editor.
MATCH_TYPES = {
    "pl_home": {
        "label": "🏆 PL Home",
        "suffix": "(H)",
        "prompt": PL_PROMPT,
        "dates": [
            ("reg_open", "Reg opens", False),
            ("reg_close", "Reg closes", False),
            ("links_sent", "Links sent", True),
            ("ballot_open", "Ballots open", False),
            ("ballot_close", "Ballots close", False),
            ("ballot_results", "Ballot results", True),
            ("match_date", "Kick-off", False),
        ],
        "sale_extras": [],
        "tweets": footered(pl_tweets),
        "events": pl_events,
    },
    "cup_home": {
        "label": "🏅 Cup / Euro Home",
        "suffix": "(H)",
        "prompt": CUP_PROMPT,
        "dates": [
            ("ballot_open", "Ballot opens", False),
            ("ballot_close", "Ballot closes", False),
            ("ballot_results", "Ballot results", True),
            ("acs_start", "ACS starts", True),
            ("acs_end", "ACS ends", True),
            ("match_date", "Kick-off", False),
        ],
        "sale_extras": [],
        "tweets": footered(cup_tweets),
        "events": cup_events,
    },
    "league_away": {
        "label": "✈️ League Away",
        "suffix": "(A)",
        "prompt": AWAY_PROMPT,
        "dates": [
            ("forwarding_deadline", "Forwarding", False),
            ("match_date", "Kick-off", False),
        ],
        "sale_extras": [],
        "tweets": footered(away_tweets),
        "events": away_events,
    },
    "euro_away": {
        "label": "🇪🇺 Euro Away",
        "suffix": "(A)",
        "prompt": CL_PROMPT,
        "dates": [("match_date", "Kick-off", False)],
        "sale_extras": ["info", "forwarding_deadline"],
        "tweets": footered(cl_tweets),
        "events": cl_events,
    },
}
