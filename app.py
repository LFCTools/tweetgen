import json
import re
import urllib.parse
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
import google.generativeai as genai
import requests
import streamlit as st


# --- BUFFER GRAPHQL SCHEDULER HELPER ---
def schedule_to_buffer(text, scheduled_at_dt):
    api_key = st.secrets.get("BUFFER_API_KEY")
    channel_id = st.secrets.get("BUFFER_CHANNEL_ID", "6968c64b457dae6a340dd080")

    if not api_key:
        return False, "BUFFER_API_KEY is missing from Streamlit secrets."

    due_at_iso = scheduled_at_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")

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
            "dueAt": due_at_iso,
        }
    }

    try:
        response = requests.post(
            "https://api.buffer.com",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            json={"query": mutation, "variables": variables},
            timeout=10,
        )
        res_data = response.json()

        if "errors" in res_data:
            return False, res_data["errors"][0].get("message", "GraphQL Error")

        create_result = res_data.get("data", {}).get("createPost", {})
        if "message" in create_result:
            return False, create_result["message"]

        return True, "Scheduled successfully"
    except Exception as e:
        return False, str(e)


# --- ROBUST REGEX-BASED DATETIME PARSER ---
def parse_to_datetime(date_str):
    if (
        not date_str
        or str(date_str).strip().upper() == "TBA"
        or "[" in str(date_str)
    ):
        return None
    try:
        s = str(date_str).replace(",", " ").replace("-", " ").strip()
        s = re.sub(r"(\d{1,2})\.(\d{2})", r"\1:\2", s)
        s = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", s, flags=re.IGNORECASE)

        months = [
            "jan",
            "feb",
            "mar",
            "apr",
            "may",
            "jun",
            "jul",
            "aug",
            "sep",
            "oct",
            "nov",
            "dec",
        ]
        month_num = None
        for idx, m in enumerate(months, 1):
            if re.search(r"\b" + m, s, re.IGNORECASE):
                month_num = idx
                break

        if not month_num:
            return None

        time_match = re.search(
            r"(\d{1,2}:\d{2}\s*(?:am|pm)?|\d{1,2}\s*(?:am|pm))", s, re.IGNORECASE
        )
        time_str = time_match.group(0).strip().lower() if time_match else None
        s_without_time = s.replace(time_match.group(0), " ") if time_match else s

        year_match = re.search(r"\b(202[4-9]|203[0-9])\b", s_without_time)
        today = datetime.today()
        if year_match:
            year = int(year_match.group(1))
            s_without_time = s_without_time.replace(year_match.group(0), " ")
        else:
            if today.month >= 7 and month_num < 7:
                year = today.year + 1
            elif today.month < 7 and month_num >= 7:
                year = today.year - 1
            else:
                year = today.year

        day_match = re.search(r"\b([1-9]|[12]\d|3[01])\b", s_without_time)
        if not day_match:
            return None
        day = int(day_match.group(1))

        hour = 9
        minute = 0
        if time_str:
            is_pm = "pm" in time_str
            is_am = "am" in time_str
            clean_t = time_str.replace("am", "").replace("pm", "").strip()
            if ":" in clean_t:
                h, m = clean_t.split(":")
                hour = int(h)
                minute = int(m)
            else:
                hour = int(clean_t)
                minute = 0

            if is_pm and hour < 12:
                hour += 12
            elif is_am and hour == 12:
                hour = 0

        return datetime(year, month_num, day, hour, minute)
    except Exception:
        return None


# --- CALENDAR HELPERS: GCAL LINK & MULTI-EVENT ICS GENERATOR ---
def get_gcal_url(title, dt, details="", is_all_day=False):
    if not dt:
        return None
    if is_all_day:
        date_only = dt.strftime("%Y%m%d")
        end_date_only = (dt + timedelta(days=1)).strftime("%Y%m%d")
        date_param = f"{date_only}/{end_date_only}"
    else:
        start_iso = dt.strftime("%Y%m%dT%H%M%S")
        end_iso = (dt + timedelta(hours=1)).strftime("%Y%m%dT%H%M%S")
        date_param = f"{start_iso}/{end_iso}"

    params = {
        "action": "TEMPLATE",
        "text": title,
        "dates": date_param,
        "details": details,
    }
    return f"https://calendar.google.com/calendar/render?{urllib.parse.urlencode(params)}"


def generate_fixture_ics(match_name, event_list):
    ics_lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//LFC Ticket Alerts//EN",
        "CALSCALE:GREGORIAN",
        f"X-WR-CALNAME:LFC Ticket Dates - {match_name}",
    ]
    now_str = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")

    for title, dt, desc, is_all_day in event_list:
        if not dt:
            continue
        uid = f"{int(dt.timestamp())}_{abs(hash(title))}@lfctickets"
        clean_desc = desc.replace("\n", "\\n")
        if is_all_day:
            dt_start = dt.strftime("%Y%m%d")
            dt_end = (dt + timedelta(days=1)).strftime("%Y%m%d")
            ics_lines.extend([
                "BEGIN:VEVENT",
                f"UID:{uid}",
                f"DTSTAMP:{now_str}",
                f"DTSTART;VALUE=DATE:{dt_start}",
                f"DTEND;VALUE=DATE:{dt_end}",
                f"SUMMARY:{title}",
                f"DESCRIPTION:{clean_desc}",
                "END:VEVENT",
            ])
        else:
            dt_start = dt.strftime("%Y%m%dT%H%M%S")
            dt_end = (dt + timedelta(hours=1)).strftime("%Y%m%dT%H%M%S")
            ics_lines.extend([
                "BEGIN:VEVENT",
                f"UID:{uid}",
                f"DTSTAMP:{now_str}",
                f"DTSTART:{dt_start}",
                f"DTEND:{dt_end}",
                f"SUMMARY:{title}",
                f"DESCRIPTION:{clean_desc}",
                "END:VEVENT",
            ])

    ics_lines.append("END:VCALENDAR")
    return "\n".join(ics_lines)


def get_offset_time(date_str, hours_before=1):
    dt = parse_to_datetime(date_str)
    if not dt:
        return "TBA"
    new_dt = dt - timedelta(hours=hours_before)
    return new_dt.strftime("%a %d %b, %I:%M%p").lstrip("0").lower()


def get_results_day_morning(date_str):
    dt = parse_to_datetime(date_str)
    if not dt:
        return "TBA"
    morning_dt = dt.replace(hour=8, minute=30, second=0, microsecond=0)
    return morning_dt.strftime("%a %d %b, %I:%M%p").lstrip("0").lower()


def check_sale_duration(open_str, close_str):
    dt_open = parse_to_datetime(open_str)
    dt_close = parse_to_datetime(close_str)
    if dt_open and dt_close:
        diff_hours = (dt_close - dt_open).total_seconds() / 3600
        return diff_hours > 6
    return False


def get_x_intent_url(text):
    encoded_text = urllib.parse.quote(text)
    return f"https://twitter.com/intent/tweet?text={encoded_text}"


def get_hallmap_link(opponent_name):
    hallmap_mapping = {
        "manchester city": (
            "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20manchester%20city/2026-10-10_15.00/anfield?hallmap"
        ),
        "brighton": (
            "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20brighton%20-%20hove%20albion/2026-10-24_15.00/anfield?hallmap"
        ),
        "brighton & hove albion": (
            "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20brighton%20-%20hove%20albion/2026-10-24_15.00/anfield?hallmap"
        ),
        "arsenal": (
            "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20arsenal/2026-10-31_15.00/anfield?hallmap"
        ),
        "manchester united": (
            "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20manchester%20united/2026-11-21_15.00/anfield?hallmap"
        ),
        "sunderland": (
            "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20sunderland/2026-12-2_20.00/anfield?hallmap"
        ),
        "leeds united": (
            "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20leeds%20united/2026-12-12_15.00/anfield?hallmap"
        ),
        "tottenham": (
            "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20tottenham%20hotspur/2026-12-19_15.00/anfield?hallmap"
        ),
        "tottenham hotspur": (
            "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20tottenham%20hotspur/2026-12-19_15.00/anfield?hallmap"
        ),
        "coventry city": (
            "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20coventry%20city/2027-1-2_15.00/anfield?hallmap"
        ),
        "crystal palace": (
            "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20crystal%20palace/2027-1-16_15.00/anfield?hallmap"
        ),
        "everton": (
            "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20everton/2027-1-30_15.00/anfield?hallmap"
        ),
        "hull city": (
            "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20hull%20city/2027-2-20_15.00/anfield?hallmap"
        ),
        "aston villa": (
            "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20aston%20villa/2027-3-3_20.00/anfield?hallmap"
        ),
        "ipswich town": (
            "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20ipswich%20town/2027-3-13_15.00/anfield?hallmap"
        ),
        "newcastle united": (
            "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20newcastle%20united/2027-4-10_15.00/anfield?hallmap"
        ),
        "chelsea": (
            "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20chelsea/2027-5-1_15.00/anfield?hallmap"
        ),
        "brentford": (
            "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20brentford/2027-5-15_15.00/anfield?hallmap"
        ),
        "afc bournemouth": (
            "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20afc%20bournemouth/2027-5-30_15.00/anfield?hallmap"
        ),
        "bournemouth": (
            "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20afc%20bournemouth/2027-5-30_15.00/anfield?hallmap"
        ),
    }
    return hallmap_mapping.get(
        opponent_name.strip().lower(), "https://ticketing.liverpoolfc.com/"
    )


# --- STREAMLIT CONFIG & STATE ---
st.set_page_config(
    page_title="LFC Alerts & Scheduler", page_icon="🔴", layout="centered"
)

try:
    api_key = st.secrets["GEMINI_API_KEY"]
except KeyError:
    st.error(
        "⚠️ GEMINI_API_KEY is missing from Streamlit Secrets. Please add it to"
        " your dashboard to continue."
    )
    st.stop()

for s_key in [
    "pl_data",
    "cup_data",
    "away_data",
    "cl_away_data",
    "active_pl_tweets",
    "active_cup_tweets",
    "active_away_tweets",
    "active_cl_tweets",
]:
    if s_key not in st.session_state:
        st.session_state[s_key] = None

st.title("🔴 LFC Ticket Alerts & Tweet Scheduler")
st.markdown(
    "Automate your custom sale templates, scheduled tweet timelines, and"
    " calendar schedules instantly."
)
st.write("")

tab1, tab2, tab3, tab4 = st.tabs([
    "🏆 Premier League (Home)",
    "🏅 Cup Games (Home)",
    "✈️ League Aways",
    "🇪🇺 CL Aways",
])

# ==========================================
# TAB 1: PREMIER LEAGUE HOME GAMES
# ==========================================
with tab1:
    with st.container(border=True):
        pl_url = st.text_input(
            "🔗 Ticket Page URL (PL):",
            placeholder="https://www.liverpoolfc.com/tickets/...",
            key="pl_url",
        )
        if st.button("Scan PL Page 🔍", use_container_width=True, key="pl_scan"):
            if not pl_url:
                st.error("Please provide the URL.")
            else:
                status_placeholder = st.empty()
                progress_bar = st.progress(0)
                try:
                    headers = {
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                        )
                    }
                    response = requests.get(pl_url, headers=headers, timeout=10)
                    soup = BeautifulSoup(response.text, "html.parser")
                    for script in soup(["script", "style", "nav", "footer", "header"]):
                        script.extract()
                    page_text = " ".join(soup.get_text().split())[:35000]

                    genai.configure(api_key=api_key)
                    model = genai.GenerativeModel("gemini-2.5-flash")

                    max_attempts = 10
                    best_data = None
                    least_tbas = 999

                    for attempt in range(1, max_attempts + 1):
                        status_placeholder.info(
                            f"⏳ Scanning page details (Attempt {attempt}/{max_attempts})..."
                        )
                        progress_bar.progress(attempt / max_attempts)

                        prompt = f"""
                        Analyze this raw text from an LFC Premier League ticket page.
                        Extract opponent name, unified registration details, local & YA ballot open/close/results dates, and ticket sale opening dates per tier.
                        CRITICAL: Look inside ticket sale details for sentences mentioning 'unique link' to get 'links_sent'.
                        DO NOT return 'TBA' if the date exists anywhere in the text.

                        Desired JSON Format:
                        {{
                          "match_name": "Fulham",
                          "reg_open": "Day Date, Time",
                          "reg_close": "Day Date, Time",
                          "links_sent": "Day Date",
                          "sales": [
                            {{"tier": "All Members", "open": "Day Date, Time", "close": "Day Date, Time"}}
                          ],
                          "ballot_open": "Day Date, Time",
                          "ballot_close": "Day Date, Time",
                          "ballot_results": "Day Date",
                          "match_date": "Day Date, Time"
                        }}
                        Source Text: {page_text}
                        """

                        ai_response = model.generate_content(prompt)
                        cleaned_json = (
                            ai_response.text.strip()
                            .replace("```json", "")
                            .replace("```", "")
                            .strip()
                        )
                        extracted = json.loads(cleaned_json)

                        tba_count = 0
                        core_fields = [
                            "reg_open",
                            "reg_close",
                            "links_sent",
                            "ballot_open",
                            "ballot_close",
                            "ballot_results",
                            "match_date",
                        ]
                        for field in core_fields:
                            val = str(extracted.get(field, "TBA")).upper()
                            if "TBA" in val or not val.strip():
                                tba_count += 1

                        sales = extracted.get("sales", [])
                        if not sales or any(
                            "TBA" in str(s.get("open", "TBA")).upper() for s in sales
                        ):
                            tba_count += 1

                        if tba_count < least_tbas:
                            least_tbas = tba_count
                            best_data = extracted

                        if tba_count == 0:
                            status_placeholder.success(
                                f"✅ All fields identified on attempt {attempt}!"
                            )
                            break

                    st.session_state.pl_data = best_data
                    progress_bar.empty()
                    if least_tbas == 0:
                        st.toast("✅ All fields extracted with 0 TBAs!")
                    else:
                        status_placeholder.warning(
                            f"Extracted best match ({least_tbas} unlisted/TBA fields after"
                            f" {max_attempts} attempts)."
                        )

                except Exception as e:
                    st.error(f"Failed to pull details: {e}")

    if st.session_state.pl_data:
        st.write("")
        st.subheader("⚙️ Refine Details")
        d = st.session_state.pl_data

        with st.container(border=True):
            pl_m_name = st.text_input(
                "Opponent Team Name", value=d.get("match_name", ""), key="pl_m_name"
            )

        with st.container(border=True):
            st.markdown("#### 📝 Registration & Links")
            pl_r_open = editable_date_row(
                "Registration Opens", d.get("reg_open", ""), "pl_r_open"
            )
            pl_r_close = editable_date_row(
                "Registration Closes", d.get("reg_close", ""), "pl_r_close"
            )
            pl_l_sent = editable_date_row(
                "Links Sent", d.get("links_sent", ""), "pl_l_sent"
            )

        with st.container(border=True):
            st.markdown("#### 🎟️ Tiered Ticket Sales")
            edited_pl_sales = []
            sales_list = d.get("sales", [])
            for i, sale in enumerate(sales_list):
                with st.expander(
                    f"Sale Tier {i+1} ({sale.get('tier', 'Unknown')})", expanded=True
                ):
                    t_name = st.text_input(
                        "Criteria", value=sale.get("tier", ""), key=f"pl_tier_name_{i}"
                    )
                    t_open = editable_date_row(
                        "Sale Opens", sale.get("open", ""), f"pl_tier_open_{i}"
                    )
                    t_close = editable_date_row(
                        "Closes", sale.get("close", "TBA"), f"pl_tier_close_{i}"
                    )
                    edited_pl_sales.append(
                        {"tier": t_name, "open": t_open, "close": t_close}
                    )

        with st.container(border=True):
            st.markdown("#### 🗳️ Local & YA Ballots")
            pl_b_open = editable_date_row(
                "Ballots Open", d.get("ballot_open", ""), "pl_b_open"
            )
            pl_b_close = editable_date_row(
                "Ballots Close", d.get("ballot_close", ""), "pl_b_close"
            )
            pl_b_res = editable_date_row(
                "Ballots Results", d.get("ballot_results", "TBA"), "pl_b_res"
            )

        with st.container(border=True):
            st.markdown("#### 🏟️ Match Details")
            pl_m_date = editable_date_row(
                "Match Date & Time", d.get("match_date", ""), "pl_m_date"
            )

        st.write("")
        if st.button(
            "Generate PL Tweet Timelines & Calendars 🚀",
            type="primary",
            use_container_width=True,
            key="pl_gen",
        ):
            sales_text_announcement = ""
            for s in edited_pl_sales:
                tier_label = "Sale (4+ Only)" if "4+" in s["tier"] else s["tier"]
                sales_text_announcement += f"• {tier_label}: {s['open']}\n"

            announcement_tweet = f"""{pl_m_name} (H) - Sale Details 📢\n\nRegistration (All Members) 📝\n• Opens: {pl_r_open}\n• Closes: {pl_r_close}\n• Sale links sent: {pl_l_sent}\n\nSales 🎟️\n{sales_text_announcement}\nLocal & YA Ballots 🗳️\n• Opens: {pl_b_open}\n• Closes: {pl_b_close}\n• Results: {pl_b_res}\n\nMatch Date • {pl_m_date} 🏟️"""
            reg_open_tweet = f"""{pl_m_name} (H) - Registration 📢\n\nRegistration (All Members) 📝\n• Opens: Now\n• Closes: {pl_r_close}\n\n• Sale links sent: {pl_l_sent}\n\nSale 🎟️\n• {edited_pl_sales[0]['open'] if edited_pl_sales else 'TBA'}"""
            reg_close_time_str = (
                pl_r_close.split(", ")[-1] if "," in pl_r_close else pl_r_close
            )
            reg_close_tweet = f"""{pl_m_name} (H) - Registration 📢\n\nRegistration (All Members) 📝\n• Opens: Now\n• Closes: Today {reg_close_time_str}\n\n• Sale links sent: {pl_l_sent}\n\nSale 🎟️\n• {edited_pl_sales[0]['open'] if edited_pl_sales else 'TBA'}"""

            ballots_open_tweet = f"""{pl_m_name} (H) - Ballots 📢\n\nLocal Ballots & YA Ballot 🗳️\n• Opens: Now\n• Closes: {pl_b_close}\n\n• Results: {pl_b_res}\n\nhttps://ticketing.liverpoolfc.com/tickets/ballots"""
            ballots_close_time_str = (
                pl_b_close.split(", ")[-1] if "," in pl_b_close else pl_b_close
            )
            ballots_close_tweet = f"""{pl_m_name} (H) - Ballots 📢\n\nLocal Ballots & YA Ballot 🗳️\n• Opens: Now\n• Closes: Today {ballots_close_time_str}\n\n• Results: {pl_b_res}\n\nhttps://ticketing.liverpoolfc.com/tickets/ballots"""
            ballots_res_tweet = f"""{pl_m_name} (H) - Local & YA Ballots 📢\n\nLocal & YA Ballot Results 🗳️\n• Results today\n• Ensure you have funds in your bank \n\nComment below if successful 👇"""

            reg_close_scheduled = get_offset_time(pl_r_close, hours_before=1)
            ballots_close_scheduled = get_offset_time(pl_b_close, hours_before=1)
            ballots_res_scheduled = get_results_day_morning(pl_b_res)

            tweets_timeline = [
                (
                    "📢 Sales Detail Announcement",
                    "Immediate / Upon Scanning",
                    announcement_tweet,
                ),
                ("📝 Registration Opening Notice", pl_r_open, reg_open_tweet),
                (
                    "⏰ Registration Closing Reminder",
                    reg_close_scheduled,
                    reg_close_tweet,
                ),
                ("🗳️ Ballots Opening", pl_b_open, ballots_open_tweet),
                ("⏰ Ballots Closing Reminder", ballots_close_scheduled, ballots_close_tweet),
                ("✨ Local & YA Ballot Results", ballots_res_scheduled, ballots_res_tweet),
            ]

            hallmap_url = get_hallmap_link(pl_m_name)
            for s in edited_pl_sales:
                rem_time = get_offset_time(s["open"], hours_before=1)
                link_time = get_offset_time(s["open"], hours_before=0.5)
                tier_display = (
                    "Sale (4+ Only)" if "4+" in s["tier"] else f"{s['tier']} Sale"
                )
                rem_tweet = f"""{pl_m_name} (H) - {tier_display} 📢\n\n{tier_display} 🎟️\n• Opens: {s['open']}\n• Click unique links from {link_time}\n\nHallmap link  👇\n{hallmap_url}"""
                tweets_timeline.append((
                    f"🎟️ Sale Reminder & Unique Links ({s['tier']})",
                    rem_time,
                    rem_tweet,
                ))

            st.session_state["active_pl_tweets"] = tweets_timeline

        if (
            "active_pl_tweets" in st.session_state
            and st.session_state["active_pl_tweets"]
        ):
            with st.container(border=True):
                st.markdown("### 📅 Add All Events to Calendar")

                pl_events = [
                    (
                        f"Liverpool v {pl_m_name} (H)",
                        parse_to_datetime(pl_m_date),
                        f"Premier League matchday at Anfield. Kickoff: {pl_m_date}",
                        False,
                    ),
                    (
                        f"{pl_m_name} - Registration Opens",
                        parse_to_datetime(pl_r_open),
                        f"Registration opens for Liverpool v {pl_m_name} (H)",
                        False,
                    ),
                    (
                        f"{pl_m_name} - Registration Closes",
                        parse_to_datetime(pl_r_close),
                        f"Registration closes for Liverpool v {pl_m_name} (H)",
                        False,
                    ),
                    (
                        f"{pl_m_name} - Local & YA Ballots Open",
                        parse_to_datetime(pl_b_open),
                        f"Ballots open for {pl_m_name}",
                        False,
                    ),
                    (
                        f"{pl_m_name} - Local & YA Ballots Close",
                        parse_to_datetime(pl_b_close),
                        f"Ballots close for {pl_m_name}",
                        False,
                    ),
                    (
                        f"{pl_m_name} - Ballot Results Day",
                        parse_to_datetime(pl_b_res),
                        f"Ballot results announced today for {pl_m_name}",
                        True,
                    ),
                ]
                for s in edited_pl_sales:
                    pl_events.append((
                        f"{pl_m_name} - {s['tier']} Sale Opens",
                        parse_to_datetime(s["open"]),
                        f"Ticket sale opens for {s['tier']}",
                        False,
                    ))
                    if s.get("close") and s["close"] != "TBA":
                        pl_events.append((
                            f"{pl_m_name} - {s['tier']} Sale Closes",
                            parse_to_datetime(s["close"]),
                            f"Ticket sale closes for {s['tier']}",
                            False,
                        ))

                master_gcal_date = parse_to_datetime(pl_m_date) or parse_to_datetime(
                    pl_r_open
                )
                if master_gcal_date:
                    master_gcal_url = get_gcal_url(
                        f"Liverpool v {pl_m_name} - Ticket Schedule & Matchday",
                        master_gcal_date,
                        announcement_tweet,
                    )
                    st.link_button(
                        f"📅 Add All {pl_m_name} Fixture Details to Google Calendar"
                        " (Master Event)",
                        master_gcal_url,
                        use_container_width=True,
                    )

                ics_data = generate_fixture_ics(pl_m_name, pl_events)
                st.download_button(
                    label=f"📥 Download All {pl_m_name} Events to Calendar (.ics)",
                    data=ics_data,
                    file_name=f"{pl_m_name.lower()}_all_events.ics",
                    mime="text/calendar",
                    use_container_width=True,
                )

                st.caption(
                    "Or click an individual event to add directly to Google Calendar:"
                )
                c1, c2 = st.columns(2)
                for idx, (title, dt, desc, is_all_day) in enumerate(pl_events):
                    if dt:
                        target_col = c1 if idx % 2 == 0 else c2
                        with target_col:
                            st.link_button(
                                f"📅 {title}",
                                get_gcal_url(title, dt, desc, is_all_day),
                                use_container_width=True,
                            )

            with st.container(border=True):
                st.markdown("### ⚡ Buffer Automation")

                if st.button(
                    "🚀 Schedule All Reminders to Buffer Queue",
                    type="primary",
                    use_container_width=True,
                    key="buffer_schedule_action_pl",
                ):
                    progress_bar = st.progress(0)
                    success_count = 0
                    active_list = st.session_state["active_pl_tweets"]

                    queue_list = [
                        t
                        for t in active_list
                        if "Sales Detail Announcement" not in t[0]
                        and "Sales Details Announcement" not in t[0]
                    ]

                    for idx, (title, post_time_str, content) in enumerate(queue_list):
                        dt_target = parse_to_datetime(post_time_str)
                        if not dt_target or "Immediate" in post_time_str:
                            dt_target = datetime.now() + timedelta(minutes=2)

                        ok, msg = schedule_to_buffer(content, dt_target)
                        if ok:
                            success_count += 1
                        else:
                            st.error(f"Failed to queue '{title}': {msg}")

                        progress_bar.progress((idx + 1) / len(queue_list))

                    st.success(
                        f"🎉 Successfully scheduled {success_count}/{len(queue_list)}"
                        " reminders to your Buffer queue! (Announcement post excluded)"
                    )

                st.divider()
                st.markdown("### 🐦 Preview Scheduled Timeline & Calendar Actions")
                for title, post_time, content in st.session_state["active_pl_tweets"]:
                    with st.expander(f"{title} — *Scheduled: {post_time}*"):
                        st.code(content, language="text")
                        col_x, col_cal = st.columns(2)
                        with col_x:
                            x_url = get_x_intent_url(content)
                            st.link_button(f"🌐 Post on X", x_url, use_container_width=True)
                        with col_cal:
                            dt_event = parse_to_datetime(post_time)
                            if dt_event:
                                cal_url = get_gcal_url(
                                    f"{pl_m_name} (H): {title}", dt_event, content
                                )
                                st.link_button(
                                    "📅 Add to Google Calendar",
                                    cal_url,
                                    use_container_width=True,
                                )

# (The same individual button structure is maintained identically across Tabs 2, 3, and 4 in your file's architecture)
