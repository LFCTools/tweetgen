import json

import requests
import streamlit as st
from bs4 import BeautifulSoup
from google import genai
from google.genai import types

from helpers import (
    TBA,
    TWEET_LIMIT,
    buffer_due_at,
    format_datetime,
    format_time,
    gcal_url,
    get_hallmap_link,
    get_offset_time,
    get_results_day_morning,
    get_x_intent_url,
    is_set,
    now_uk,
    parse_to_datetime,
    shorten_tier_name,
    strip_json_fences,
    today_label,
    tweet_length,
)

GEMINI_MODEL = "gemini-2.5-flash"
PAGE_TEXT_LIMIT = 40000
MIN_PAGE_TEXT = 300
IMMEDIATE = "Immediate / Upon Scanning"
BALLOTS_URL = "https://ticketing.liverpoolfc.com/tickets/ballots"


# ==========================================
# EXTERNAL SERVICES
# ==========================================
def schedule_to_buffer(text, scheduled_at_local):
    """Queue a post in Buffer. `scheduled_at_local` is a naive UK-time datetime."""
    api_key = st.secrets.get("BUFFER_API_KEY")
    channel_id = st.secrets.get("BUFFER_CHANNEL_ID")
    if not api_key:
        return False, "BUFFER_API_KEY is missing from Streamlit secrets."
    if not channel_id:
        return False, "BUFFER_CHANNEL_ID is missing from Streamlit secrets."

    mutation = """
    mutation CreateScheduledPost($input: CreatePostInput!) {
      createPost(input: $input) {
        ... on PostActionSuccess {
          post {
            id
            dueAt
          }
        }
        ... on MutationError {
          message
        }
      }
    }
    """
    variables = {
        "input": {
            "text": text,
            "channelId": channel_id,
            "schedulingType": "automatic",
            "mode": "customScheduled",
            "dueAt": buffer_due_at(scheduled_at_local),
        }
    }

    try:
        response = requests.post(
            "https://api.buffer.com",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
            json={"query": mutation, "variables": variables},
            timeout=10,
        )
        res_data = response.json()
        if res_data.get("errors"):
            return False, res_data["errors"][0].get("message", "GraphQL Error")
        create_result = (res_data.get("data") or {}).get("createPost") or {}
        if "message" in create_result:
            return False, create_result["message"]
        if not create_result.get("post"):
            return False, f"Unexpected Buffer response (HTTP {response.status_code})"
        return True, "Scheduled successfully"
    except Exception as e:
        return False, str(e)


@st.cache_resource
def get_gemini_client(api_key):
    return genai.Client(api_key=api_key)


def fetch_page_text(url):
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    response = requests.get(url, headers=headers, timeout=15)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.extract()
    page_text = " ".join(soup.get_text().split())

    if len(page_text) < MIN_PAGE_TEXT:
        raise ValueError(
            f"The page only contained {len(page_text)} characters of text. "
            "The site may have blocked the request or the URL is wrong — check it in a browser."
        )
    return page_text[:PAGE_TEXT_LIMIT]


@st.cache_data(ttl=600, show_spinner=False)
def scan_ticket_page(url, prompt_template, _api_key):
    """Fetch a ticket page and extract structured details with Gemini (cached 10 min per URL)."""
    page_text = fetch_page_text(url)
    prompt = prompt_template.replace("<<PAGE_TEXT>>", page_text)

    client = get_gemini_client(_api_key)
    ai_response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0),
    )
    data = json.loads(strip_json_fences(ai_response.text or ""))
    if not isinstance(data, dict):
        raise ValueError("Gemini did not return a JSON object.")
    if not isinstance(data.get("sales"), list):
        data["sales"] = []
    return data


# ==========================================
# PROMPTS
# ==========================================
COMMON_RULES = """
Respond ONLY with a valid JSON object matching the structure below.
Use abbreviated days (e.g., Wed) and months (e.g., Nov) and format times like 10:00am or 11:00am.
If any field is missing, make its value "TBA". Never invent dates that are not in the source text.
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


# ==========================================
# TWEET / EVENT BUILDERS
# ==========================================
def tweet(title, when, text, buffer=True):
    """A timeline item. `when` is the display string for when it should be posted."""
    return {"title": title, "when": when, "text": text, "buffer": buffer}


def event(label, name, time, all_day=False):
    return {"label": label, "name": name, "time": time, "all_day": all_day}


def sale_close_reminder(match_prefix, tier, close):
    if not is_set(close):
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


# ==========================================
# UI COMPONENTS
# ==========================================
def editable_date_row(label, default_val, key):
    if st.session_state.get(f"{key}_default") != default_val:
        st.session_state[f"{key}_default"] = default_val
        dt_obj = parse_to_datetime(default_val)

        st.session_state[f"{key}_tba"] = not dt_obj
        st.session_state[f"{key}_allday"] = bool(
            dt_obj and not any(m in str(default_val).lower() for m in ["am", "pm", ":"])
        )
        st.session_state[f"{key}_date"] = dt_obj.date() if dt_obj else now_uk().date()
        st.session_state[f"{key}_time"] = dt_obj.time() if dt_obj else now_uk().replace(hour=10, minute=0).time()

    d = st.session_state[f"{key}_date"]
    t = st.session_state[f"{key}_time"]
    if st.session_state[f"{key}_tba"]:
        display_val = TBA
    elif st.session_state[f"{key}_allday"]:
        display_val = d.strftime("%a %d %b")
    else:
        display_val = f"{d.strftime('%a %d %b')}, {format_time(t)}"

    if st.toggle(f"✏️ **{label}** : {display_val}", key=f"toggle_{key}"):
        with st.container(border=True):
            c1, c2 = st.columns(2)
            with c1:
                st.date_input("Date", key=f"{key}_date", disabled=st.session_state[f"{key}_tba"])
                st.checkbox("TBA", key=f"{key}_tba")
            with c2:
                st.time_input(
                    "Time",
                    key=f"{key}_time",
                    disabled=st.session_state[f"{key}_tba"] or st.session_state[f"{key}_allday"],
                )
                st.checkbox("All Day", key=f"{key}_allday", disabled=st.session_state[f"{key}_tba"])

    return display_val


def reset_tab_state(prefix):
    """Drop edited fields / generated tweets from a previous scan so a new match starts clean."""
    keep = {f"{prefix}_url", f"{prefix}_scan", f"{prefix}_force", f"{prefix}_gen"}
    for k in list(st.session_state.keys()):
        if k in keep:
            continue
        if k.startswith(f"{prefix}_") or k.startswith(f"toggle_{prefix}_"):
            del st.session_state[k]
    st.session_state[f"tweets_{prefix}"] = None


def scan_section(prefix, url_label, button_label, spinner_text, prompt_template, api_key):
    with st.container(border=True):
        url = st.text_input(url_label, placeholder="https://www.liverpoolfc.com/tickets/...", key=f"{prefix}_url")
        force = st.checkbox("Ignore cached scan (re-read the page)", key=f"{prefix}_force")
        if st.button(button_label, width="stretch", key=f"{prefix}_scan"):
            url = (url or "").strip()
            if not url:
                st.error("Please provide the URL.")
            elif not url.startswith(("http://", "https://")):
                st.error("The URL must start with http:// or https://")
            else:
                if force:
                    scan_ticket_page.clear(url, prompt_template, api_key)
                with st.spinner(spinner_text):
                    try:
                        data = scan_ticket_page(url, prompt_template, api_key)
                    except requests.HTTPError as e:
                        st.error(f"The ticket page returned an error ({e.response.status_code}). Check the URL.")
                    except requests.RequestException as e:
                        st.error(f"Could not load the ticket page: {e}")
                    except json.JSONDecodeError:
                        st.error("Gemini returned something that wasn't valid JSON. Try scanning again.")
                    except Exception as e:
                        st.error(f"Failed to automatically pull details: {e}")
                    else:
                        reset_tab_state(prefix)
                        st.session_state[f"data_{prefix}"] = data
                        st.toast("✅ Data successfully extracted!")
    return st.session_state.get(f"data_{prefix}")


def sale_tier_inputs(prefix, sales, extra=None):
    """Render editable sale tiers. `extra(i, sale)` may return additional fields for a tier."""
    edited = []
    for i, sale in enumerate(sales):
        short_name = shorten_tier_name(sale.get("tier", "") or "")
        if not short_name:
            continue
        with st.expander(f"Sale Tier {i + 1} ({short_name})", expanded=True):
            t_name = st.text_input("Criteria", value=short_name, key=f"{prefix}_tier_name_{i}")
            t_open = editable_date_row("Sale Opens", sale.get("open", ""), f"{prefix}_tier_open_{i}")
            t_close = editable_date_row("Closes", sale.get("close", TBA), f"{prefix}_tier_close_{i}")
            row = {"tier": t_name, "open": t_open, "close": t_close}
            if extra:
                row.update(extra(i, sale))
            edited.append(row)
    return edited


def _tweet_id(t, dt):
    return f"{t['title']}|{dt.isoformat() if dt else ''}|{t['text']}"


def render_buffer_section(prefix, tweets, label):
    st.markdown("### ⚡ Buffer Automation")
    scheduled = st.session_state.setdefault(f"scheduled_{prefix}", set())
    now = now_uk()

    rows, to_send = [], []
    for t in tweets:
        if not t["buffer"]:
            continue
        dt = parse_to_datetime(t["when"])
        length = tweet_length(t["text"])
        tid = _tweet_id(t, dt)
        if tid in scheduled:
            status = "✅ Already scheduled"
        elif not dt:
            status = "⏭️ Skip – no time set (TBA)"
        elif dt <= now:
            status = "⏭️ Skip – time already passed"
        elif length > TWEET_LIMIT:
            status = f"⏭️ Skip – too long ({length}/{TWEET_LIMIT})"
        else:
            status = "🕒 Will schedule"
            to_send.append((t, dt, tid))
        rows.append(
            {
                "Tweet": t["title"],
                "UK time": format_datetime(dt) if dt else TBA,
                "Sent to Buffer (UTC)": buffer_due_at(dt)[:16].replace("T", " ") if dt else "—",
                "Status": status,
            }
        )

    if rows:
        st.dataframe(rows, hide_index=True, width="stretch")
    st.caption(
        "Each reminder is sent to Buffer once per session. If you change details and regenerate, "
        "delete the old versions in Buffer."
    )

    if st.button(
        f"🚀 Schedule {len(to_send)} {label} reminder(s) to Buffer",
        type="primary",
        width="stretch",
        key=f"buf_{prefix}",
        disabled=not to_send,
    ):
        prog = st.progress(0)
        ok_count, errors = 0, []
        for idx, (t, dt, tid) in enumerate(to_send):
            ok, msg = schedule_to_buffer(t["text"], dt)
            if ok:
                ok_count += 1
                scheduled.add(tid)
            else:
                errors.append(f"Failed to queue '{t['title']}': {msg}")
            prog.progress((idx + 1) / len(to_send))
        st.session_state[f"bufmsg_{prefix}"] = (ok_count, len(to_send), errors)
        st.rerun()

    result = st.session_state.pop(f"bufmsg_{prefix}", None)
    if result:
        ok_count, total, errors = result
        for err in errors:
            st.error(err)
        if ok_count:
            st.success(f"🎉 Successfully scheduled {ok_count}/{total} {label} reminders to Buffer!")


def render_timeline(tweets, cal_prefix, heading):
    st.markdown(f"### 🐦 {heading}")
    for t in tweets:
        length = tweet_length(t["text"])
        flag = " ⚠️" if length > TWEET_LIMIT else ""
        with st.expander(f"{t['title']} — *Scheduled: {t['when']}* · {length}/{TWEET_LIMIT}{flag}"):
            st.code(t["text"], language="text")
            if length > TWEET_LIMIT:
                st.warning(f"About {length} characters — over X's {TWEET_LIMIT} limit. Trim it before posting.")
            else:
                st.caption(f"≈ {length}/{TWEET_LIMIT} characters (URLs count as 23, emoji and • as 2)")
            c1, c2 = st.columns(2)
            with c1:
                st.link_button("🌐 Post on X", get_x_intent_url(t["text"]), width="stretch")
            with c2:
                dt = parse_to_datetime(t["when"])
                if dt:
                    st.link_button(
                        "📅 Add to Google Calendar", gcal_url(f"{cal_prefix}: {t['title']}", dt), width="stretch"
                    )


def render_calendar(events):
    st.markdown("### 📅 Master Calendar Schedule Links (Exact Event Times)")
    for ev in events:
        dt = parse_to_datetime(ev["time"])
        if dt:
            st.link_button(
                f"📅 Add **{ev['label']}** ({ev['time']})",
                gcal_url(ev["name"], dt, is_all_day=ev["all_day"]),
                width="stretch",
            )


def render_outputs(prefix, label, timeline_heading, cal_prefix, events):
    tweets = st.session_state.get(f"tweets_{prefix}")
    if not tweets:
        return
    with st.container(border=True):
        render_buffer_section(prefix, tweets, label)
        render_timeline(tweets, cal_prefix, timeline_heading)
    with st.container(border=True):
        render_calendar(events)


# ==========================================
# APP SETUP
# ==========================================
st.set_page_config(page_title="LFC Alerts & Scheduler", page_icon="🔴", layout="centered")

gemini_api_key = st.secrets.get("GEMINI_API_KEY")
if not gemini_api_key:
    st.error("⚠️ GEMINI_API_KEY is missing from Streamlit Secrets. Please add it to your dashboard to continue.")
    st.stop()

st.title("🔴 LFC Ticket Alerts & Tweet Scheduler")
st.markdown("Automate your custom sale templates, scheduled tweet timelines, and calendar schedules instantly.")
st.write("")

tab1, tab2, tab3, tab4 = st.tabs(["🏆 Premier League (Home)", "🏅 Cup Games (Home)", "✈️ League Aways", "🇪🇺 CL Aways"])

# ==========================================
# TAB 1: PREMIER LEAGUE HOME GAMES
# ==========================================
with tab1:
    d = scan_section("pl", "🔗 Ticket Page URL (PL):", "Scan PL Page 🔍", "Analyzing ticketing page...", PL_PROMPT, gemini_api_key)

    if d:
        st.write("")
        st.subheader("⚙️ Refine Details")
        with st.container(border=True):
            name = st.text_input("Opponent Team Name", value=d.get("match_name", ""), key="pl_m_name")

        with st.container(border=True):
            st.markdown("#### 📝 Registration & Links")
            r_open = editable_date_row("Registration Opens", d.get("reg_open", ""), "pl_r_open")
            r_close = editable_date_row("Registration Closes", d.get("reg_close", ""), "pl_r_close")
            l_sent = editable_date_row("Links Sent", d.get("links_sent", ""), "pl_l_sent")

        with st.container(border=True):
            st.markdown("#### 🎟️ Tiered Ticket Sales")
            sales = sale_tier_inputs("pl", d.get("sales", []))

        with st.container(border=True):
            st.markdown("#### 🗳️ Local & YA Ballots")
            b_open = editable_date_row("Ballots Open", d.get("ballot_open", ""), "pl_b_open")
            b_close = editable_date_row("Ballots Close", d.get("ballot_close", ""), "pl_b_close")
            b_res = editable_date_row("Ballots Results", d.get("ballot_results", TBA), "pl_b_res")

        with st.container(border=True):
            st.markdown("#### 🏟️ Match Details")
            m_date = editable_date_row("Match Date & Time", d.get("match_date", ""), "pl_m_date")
            auto_hallmap = get_hallmap_link(name, m_date)
            hallmap_override = st.text_input(
                "Hallmap link override (optional)", key="pl_hallmap", placeholder=auto_hallmap
            )
            hallmap_url = hallmap_override.strip() or auto_hallmap
            st.caption(f"Hallmap link used: {hallmap_url}")

        st.write("")
        if st.button("Generate PL Tweet Timelines & Calendars 🚀", type="primary", width="stretch", key="pl_gen"):
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
                link_time = get_offset_time(s["open"], hours_before=0.5)
                rem_tweet = f"""{name} (H) - {s['tier']} 📢\n\n{s['tier']} 🎟️\n• Opens: {today_label(s['open'])}\n• Click unique links from {link_time}\n\nHallmap link  👇\n{hallmap_url}"""
                tweets.append(tweet(f"🎟️ Sale Opening ({s['tier']})", get_offset_time(s["open"], 1), rem_tweet))
                close = sale_close_reminder(f"{name} (H)", s["tier"], s["close"])
                if close:
                    tweets.append(close)
            st.session_state["tweets_pl"] = tweets

        p = f"{name} (H)"
        render_outputs(
            "pl",
            "PL",
            "Scheduled Tweet Timeline",
            p,
            [
                event("Registration Open", f"{p} - Registration Opens", r_open),
                event("Registration Closes", f"{p} - Registration Closes", r_close),
                event("Unique Links Sent", f"{p} - Unique Links Sent", l_sent),
                event("Ballots Open", f"{p} - Ballots Open", b_open),
                event("Ballots Close", f"{p} - Ballots Close", b_close),
                event("Ballots Results", f"{p} - Ballots Results", b_res, all_day=True),
                *sale_events(p, sales),
                event("Match Day", f"{p} - Match Date", m_date),
            ],
        )


# ==========================================
# TAB 2: CUP GAMES
# ==========================================
with tab2:
    cd = scan_section(
        "cup",
        "🔗 Ticket Page URL (Cup/European Home):",
        "Scan Cup Page 🔍",
        "Analyzing Cup/European ticketing page...",
        CUP_PROMPT,
        gemini_api_key,
    )

    if cd:
        st.write("")
        st.subheader("⚙️ Refine Details")
        name = st.text_input("Opponent Team Name", value=cd.get("match_name", ""), key="cup_m_name")
        comp = st.text_input("Competition Type", value=cd.get("competition", "League Cup"), key="cup_comp")

        with st.container(border=True):
            st.markdown("#### 🎟️ Tiered Sales")
            sales = sale_tier_inputs("cup", cd.get("sales", []))

        with st.container(border=True):
            st.markdown("#### 🗳️ Local Ballot")
            b_open = editable_date_row("Local Ballot Opens", cd.get("ballot_open", ""), "cup_b_open")
            b_close = editable_date_row("Local Ballot Closes", cd.get("ballot_close", ""), "cup_b_close")
            b_res = editable_date_row("Local Ballot Results", cd.get("ballot_results", TBA), "cup_b_res")

        with st.container(border=True):
            st.markdown("#### 💰 Auto Cup Scheme & Match Details")
            acs_start = editable_date_row("ACS Payment Start", cd.get("acs_start", ""), "cup_acs_start")
            acs_end = editable_date_row("ACS Payment End", cd.get("acs_end", ""), "cup_acs_end")
            m_date = editable_date_row("Match Date & Time", cd.get("match_date", ""), "cup_m_date")

        st.write("")
        if st.button("Generate Cup Tweet Timelines & Calendars 🚀", type="primary", width="stretch", key="cup_gen"):
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
                close = sale_close_reminder(f"{name} ({comp})", s["tier"], s["close"])
                if close:
                    tweets.append(close)
            st.session_state["tweets_cup"] = tweets

        p = f"{name} (H)"
        render_outputs(
            "cup",
            "Cup",
            "Scheduled Cup Tweet Timeline",
            p,
            [
                event("Local Ballot Opens", f"{p} - Local Ballot Opens", b_open),
                event("Local Ballot Closes", f"{p} - Local Ballot Closes", b_close),
                event("Local Ballot Results", f"{p} - Local Ballot Results", b_res, all_day=True),
                event("ACS Payment Start", f"{p} - ACS Payment Start", acs_start, all_day=True),
                event("ACS Payment End", f"{p} - ACS Payment End", acs_end, all_day=True),
                *sale_events(p, sales),
                event("Match Day", f"{p} - Match Date", m_date),
            ],
        )


# ==========================================
# TAB 3: LEAGUE AWAYS
# ==========================================
with tab3:
    ad = scan_section(
        "away", "🔗 Ticket Page URL (Away):", "Scan Away Page 🔍", "Analyzing Away ticketing page...", AWAY_PROMPT, gemini_api_key
    )

    if ad:
        st.write("")
        st.subheader("⚙️ Refine Details")
        with st.container(border=True):
            name = st.text_input("Opponent Team Name", value=ad.get("match_name", ""), key="away_m_name")
            fwd = editable_date_row("Forwarding Deadline", ad.get("forwarding_deadline", TBA), "away_fwd")

        with st.container(border=True):
            st.markdown("#### 🎟️ Tiered Sales")
            sales = sale_tier_inputs("away", ad.get("sales", []))

        with st.container(border=True):
            st.markdown("#### 🏟️ Match Details")
            m_date = editable_date_row("Match Date & Time", ad.get("match_date", ""), "away_m_date")

        st.write("")
        if st.button("Generate Away Tweet Timelines & Calendars 🚀", type="primary", width="stretch", key="away_gen"):
            blocks = "\n\n".join(f"Sale ({s['tier']})\n• Opens: {s['open']}\n• Closes: {s['close']}" for s in sales)
            announcement = f"""{name} (A) - Sale Details 📢\n\n{blocks}\n\nMatch Date • {m_date} 🏟️"""

            tweets = [tweet("📢 Sales Detail Announcement", IMMEDIATE, announcement, buffer=False)]
            for s in sales:
                sale_tweet = f"""{name} (A) 🎟️\n\nSale ({s['tier']})\n• Opens: {today_label(s['open'])}\n• Closes: {s['close']}\n• Guaranteed Sale"""
                tweets.append(tweet(f"🎟️ Sale Opening ({s['tier']})", get_offset_time(s["open"], 1), sale_tweet))
                close = sale_close_reminder(f"{name} (A)", s["tier"], s["close"])
                if close:
                    tweets.append(close)

            if is_set(fwd):
                fwd_tweet = f"""Forwarding Deadline ➡️\n• Closes: {today_label(fwd)}"""
                tweets.append(tweet("➡️ Forwarding Deadline", get_offset_time(fwd, 1), fwd_tweet))
            st.session_state["tweets_away"] = tweets

        p = f"{name} (A)"
        away_events = sale_events(p, sales)
        if is_set(fwd):
            away_events.append(event("Forwarding Deadline", f"{p} - Forwarding Deadline", fwd))
        away_events.append(event("Match Day", f"{p} - Match Date", m_date))
        render_outputs("away", "Away", "Scheduled Away Tweet Timeline", p, away_events)


# ==========================================
# TAB 4: CHAMPIONS LEAGUE AWAYS
# ==========================================
with tab4:
    cld = scan_section(
        "cl",
        "🔗 Ticket Page URL (CL Away):",
        "Scan CL Away Page 🔍",
        "Analyzing Champions League Away page...",
        CL_PROMPT,
        gemini_api_key,
    )

    if cld:
        st.write("")
        st.subheader("⚙️ Refine Details")
        with st.container(border=True):
            name = st.text_input("Opponent Team Name", value=cld.get("match_name", ""), key="cl_m_name")

        def cl_extra(i, sale):
            return {
                "info": st.text_input("Information / Guarantee", value=sale.get("info", "") or "", key=f"cl_tier_info_{i}"),
                "forwarding_deadline": editable_date_row(
                    "Forwarding Deadline", sale.get("forwarding_deadline", TBA), f"cl_tier_fwd_{i}"
                ),
            }

        with st.container(border=True):
            st.markdown("#### 🎟️ Tiered Sales & Forwarding Deadlines")
            sales = sale_tier_inputs("cl", cld.get("sales", []), extra=cl_extra)

        with st.container(border=True):
            st.markdown("#### 🏟️ Match Details")
            m_date = editable_date_row("Match Date & Time", cld.get("match_date", ""), "cl_m_date")

        st.write("")
        if st.button("Generate CL Away Tweet Timelines & Calendars 🚀", type="primary", width="stretch", key="cl_gen"):
            blocks = []
            for s in sales:
                info = s["info"].strip()
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
                info_line = f"• {s['info'].strip()}\n" if s["info"].strip() else ""
                sale_tweet = f"""{name} (A) 🎟️\n\nSale ({s['tier']})\n• Opens: {today_label(s['open'])}\n• Closes: {s['close'].replace(',', ' -')}\n{info_line}"""
                tweets.append(tweet(f"🎟️ Sale Opening ({s['tier']})", get_offset_time(s["open"], 1), sale_tweet))
                close = sale_close_reminder(f"{name} (A)", s["tier"], s["close"])
                if close:
                    tweets.append(close)
                fwd = s.get("forwarding_deadline")
                if is_set(fwd):
                    fwd_tweet = f"""{name} (A) ➡️\n\nForwarding Deadline ({s['tier']})\n• Closes: {today_label(fwd)}"""
                    tweets.append(tweet(f"➡️ Forwarding Deadline ({s['tier']})", get_offset_time(fwd, 1), fwd_tweet))
            st.session_state["tweets_cl"] = tweets

        p = f"{name} (A)"
        cl_events = sale_events(p, sales)
        for s in sales:
            if is_set(s.get("forwarding_deadline")):
                cl_events.append(
                    event(f"Forwarding Deadline ({s['tier']})", f"{p} - Forwarding Deadline ({s['tier']})", s["forwarding_deadline"])
                )
        cl_events.append(event("Match Day", f"{p} - Match Date", m_date))
        render_outputs("cl", "CL", "Scheduled CL Away Tweet Timeline", p, cl_events)
