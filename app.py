import streamlit as st
import google.generativeai as genai
import requests
from bs4 import BeautifulSoup
import urllib.parse
from datetime import datetime, timedelta
import json
import re

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
            "dueAt": due_at_iso
        }
    }

    try:
        response = requests.post(
            "https://api.buffer.com",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}"
            },
            json={"query": mutation, "variables": variables},
            timeout=10
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
    if not date_str or str(date_str).strip().upper() == "TBA" or "[" in str(date_str):
        return None
    try:
        s = str(date_str).replace(',', ' ').replace('-', ' ').strip()
        s = re.sub(r'(\d{1,2})\.(\d{2})', r'\1:\2', s)
        s = re.sub(r'(\d+)(st|nd|rd|th)', r'\1', s, flags=re.IGNORECASE)

        months = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec']
        month_num = None
        for idx, m in enumerate(months, 1):
            if re.search(r'\b' + m, s, re.IGNORECASE):
                month_num = idx
                break

        if not month_num:
            return None

        time_match = re.search(r'(\d{1,2}:\d{2}\s*(?:am|pm)?|\d{1,2}\s*(?:am|pm))', s, re.IGNORECASE)
        time_str = time_match.group(0).strip().lower() if time_match else None
        s_without_time = s.replace(time_match.group(0), ' ') if time_match else s

        year_match = re.search(r'\b(202[4-9]|203[0-9])\b', s_without_time)
        today = datetime.today()
        if year_match:
            year = int(year_match.group(1))
            s_without_time = s_without_time.replace(year_match.group(0), ' ')
        else:
            if today.month >= 7 and month_num < 7:
                year = today.year + 1
            elif today.month < 7 and month_num >= 7:
                year = today.year - 1
            else:
                year = today.year

        day_match = re.search(r'\b([1-9]|[12]\d|3[01])\b', s_without_time)
        if not day_match:
            return None
        day = int(day_match.group(1))

        hour = 9
        minute = 0
        if time_str:
            is_pm = 'pm' in time_str
            is_am = 'am' in time_str
            clean_t = time_str.replace('am', '').replace('pm', '').strip()
            if ':' in clean_t:
                h, m = clean_t.split(':')
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

def format_gcal_date(dt, is_all_day=False):
    if not dt: return None
    if is_all_day:
        date_only = dt.strftime("%Y%m%d")
        end_date_only = (dt + timedelta(days=1)).strftime("%Y%m%d")
        return f"{date_only}/{end_date_only}"
    else:
        start_iso = dt.strftime("%Y%m%dT%H%M%S")
        end_iso = dt.strftime("%Y%m%dT%H%M%S")
        return f"{start_iso}/{end_iso}"

def get_offset_time(date_str, hours_before=1):
    dt = parse_to_datetime(date_str)
    if not dt: return "TBA"
    new_dt = dt - timedelta(hours=hours_before)
    return new_dt.strftime("%a %d %b, %I:%M%p").lstrip("0").lower()

def get_results_day_morning(date_str):
    dt = parse_to_datetime(date_str)
    if not dt: return "TBA"
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
        "manchester city": "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20manchester%20city/2026-10-10_15.00/anfield?hallmap",
        "brighton": "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20brighton%20-%20hove%20albion/2026-10-24_15.00/anfield?hallmap",
        "brighton & hove albion": "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20brighton%20-%20hove%20albion/2026-10-24_15.00/anfield?hallmap",
        "arsenal": "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20arsenal/2026-10-31_15.00/anfield?hallmap",
        "manchester united": "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20manchester%20united/2026-11-21_15.00/anfield?hallmap",
        "sunderland": "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20sunderland/2026-12-2_20.00/anfield?hallmap",
        "leeds united": "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20leeds%20united/2026-12-12_15.00/anfield?hallmap",
        "tottenham": "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20tottenham%20hotspur/2026-12-19_15.00/anfield?hallmap",
        "tottenham hotspur": "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20tottenham%20hotspur/2026-12-19_15.00/anfield?hallmap",
        "coventry city": "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20coventry%20city/2027-1-2_15.00/anfield?hallmap",
        "crystal palace": "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20crystal%20palace/2027-1-16_15.00/anfield?hallmap",
        "everton": "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20everton/2027-1-30_15.00/anfield?hallmap",
        "hull city": "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20hull%20city/2027-2-20_15.00/anfield?hallmap",
        "aston villa": "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20aston%20villa/2027-3-3_20.00/anfield?hallmap",
        "ipswich town": "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20ipswich%20town/2027-3-13_15.00/anfield?hallmap",
        "newcastle united": "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20newcastle%20united/2027-4-10_15.00/anfield?hallmap",
        "chelsea": "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20chelsea/2027-5-1_15.00/anfield?hallmap",
        "brentford": "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20brentford/2027-5-15_15.00/anfield?hallmap",
        "afc bournemouth": "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20afc%20bournemouth/2027-5-30_15.00/anfield?hallmap",
        "bournemouth": "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20afc%20bournemouth/2027-5-30_15.00/anfield?hallmap"
    }
    return hallmap_mapping.get(opponent_name.strip().lower(), "https://ticketing.liverpoolfc.com/")

# --- FIXED MOBILE-OPTIMIZED UI WIDGET ---
def editable_date_row(label, default_val, key):
    raw_val = str(default_val).strip() if default_val else "TBA"

    if f"{key}_default" not in st.session_state or st.session_state[f"{key}_default"] != raw_val:
        st.session_state[f"{key}_default"] = raw_val
        dt_obj = parse_to_datetime(raw_val)
        
        st.session_state[f"{key}_tba"] = (raw_val.upper() == "TBA" or not raw_val)
        st.session_state[f"{key}_allday"] = bool(raw_val and raw_val.upper() != "TBA" and not any(m in raw_val.lower() for m in ['am','pm',':']))
        st.session_state[f"{key}_date"] = dt_obj.date() if dt_obj else datetime.today().date()
        st.session_state[f"{key}_time"] = dt_obj.time() if dt_obj else datetime.strptime("09:00am", "%I:%M%p").time()

    is_tba = st.session_state[f"{key}_tba"]
    all_day = st.session_state[f"{key}_allday"]
    d = st.session_state[f"{key}_date"]
    t = st.session_state[f"{key}_time"]

    if is_tba:
        display_val = "TBA"
    elif all_day:
        display_val = d.strftime("%a %d %b")
    else:
        dt_obj = parse_to_datetime(raw_val)
        if dt_obj:
            time_formatted = dt_obj.strftime("%I:%M%p").lstrip("0").lower()
            display_val = f"{dt_obj.strftime('%a %d %b')}, {time_formatted}"
        else:
            display_val = raw_val

    edit_mode = st.toggle(f"✏️ **{label}** : {display_val}", key=f"toggle_{key}")
    
    if edit_mode:
        with st.container(border=True):
            c1, c2 = st.columns(2)
            with c1:
                st.date_input("Date", key=f"{key}_date", disabled=st.session_state[f"{key}_tba"])
                st.checkbox("TBA", key=f"{key}_tba")
            with c2:
                st.time_input("Time", key=f"{key}_time", disabled=st.session_state[f"{key}_tba"] or st.session_state[f"{key}_allday"])
                st.checkbox("All Day", key=f"{key}_allday", disabled=st.session_state[f"{key}_tba"])
                
    return display_val


# --- STREAMLIT CONFIG & STATE ---
st.set_page_config(page_title="LFC Alerts & Scheduler", page_icon="🔴", layout="centered")

try:
    api_key = st.secrets["GEMINI_API_KEY"]
except KeyError:
    st.error("⚠️ GEMINI_API_KEY is missing from Streamlit Secrets. Please add it to your dashboard to continue.")
    st.stop()

for s_key in ["pl_data", "cup_data", "away_data", "cl_away_data", "active_pl_tweets", "active_cup_tweets", "active_away_tweets", "active_cl_tweets"]:
    if s_key not in st.session_state:
        st.session_state[s_key] = None

st.title("🔴 LFC Ticket Alerts & Tweet Scheduler")
st.markdown("Automate your custom sale templates, scheduled tweet timelines, and calendar schedules instantly.")
st.write("")

tab1, tab2, tab3, tab4 = st.tabs(["🏆 Premier League (Home)", "🏅 Cup Games (Home)", "✈️ League Aways", "🇪🇺 CL Aways"])

# ==========================================
# TAB 1: PREMIER LEAGUE HOME GAMES
# ==========================================
with tab1:
    with st.container(border=True):
        pl_url = st.text_input("🔗 Ticket Page URL (PL):", placeholder="https://www.liverpoolfc.com/tickets/...", key="pl_url")
        if st.button("Scan PL Page 🔍", use_container_width=True, key="pl_scan"):
            if not pl_url:
                st.error("Please provide the URL.")
            else:
                status_placeholder = st.empty()
                progress_bar = st.progress(0)
                try:
                    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
                    response = requests.get(pl_url, headers=headers, timeout=10)
                    soup = BeautifulSoup(response.text, 'html.parser')
                    for script in soup(["script", "style", "nav", "footer", "header"]):
                        script.extract()
                    page_text = " ".join(soup.get_text().split())[:35000]

                    genai.configure(api_key=api_key)
                    model = genai.GenerativeModel('gemini-2.5-flash')

                    max_attempts = 10
                    best_data = None
                    least_tbas = 999

                    for attempt in range(1, max_attempts + 1):
                        status_placeholder.info(f"⏳ Scanning page details (Attempt {attempt}/{max_attempts})...")
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
                        cleaned_json = ai_response.text.strip().replace("```json", "").replace("```", "").strip()
                        extracted = json.loads(cleaned_json)

                        tba_count = 0
                        core_fields = ["reg_open", "reg_close", "links_sent", "ballot_open", "ballot_close", "ballot_results", "match_date"]
                        for field in core_fields:
                            val = str(extracted.get(field, "TBA")).upper()
                            if "TBA" in val or not val.strip():
                                tba_count += 1
                        
                        sales = extracted.get("sales", [])
                        if not sales or any("TBA" in str(s.get("open", "TBA")).upper() for s in sales):
                            tba_count += 1

                        if tba_count < least_tbas:
                            least_tbas = tba_count
                            best_data = extracted

                        if tba_count == 0:
                            status_placeholder.success(f"✅ All fields identified on attempt {attempt}!")
                            break

                    st.session_state.pl_data = best_data
                    progress_bar.empty()
                    if least_tbas == 0:
                        st.toast("✅ All fields extracted with 0 TBAs!")
                    else:
                        status_placeholder.warning(f"Extracted best match ({least_tbas} unlisted/TBA fields after {max_attempts} attempts).")

                except Exception as e:
                    st.error(f"Failed to pull details: {e}")

    if st.session_state.pl_data:
        st.write("")
        st.subheader("⚙️ Refine Details")
        d = st.session_state.pl_data
        
        with st.container(border=True):
            pl_m_name = st.text_input("Opponent Team Name", value=d.get("match_name", ""), key="pl_m_name")

        with st.container(border=True):
            st.markdown("#### 📝 Registration & Links")
            pl_r_open = editable_date_row("Registration Opens", d.get("reg_open", ""), "pl_r_open")
            pl_r_close = editable_date_row("Registration Closes", d.get("reg_close", ""), "pl_r_close")
            pl_l_sent = editable_date_row("Links Sent", d.get("links_sent", ""), "pl_l_sent")

        with st.container(border=True):
            st.markdown("#### 🎟️ Tiered Ticket Sales")
            edited_pl_sales = []
            sales_list = d.get("sales", [])
            for i, sale in enumerate(sales_list):
                with st.expander(f"Sale Tier {i+1} ({sale.get('tier', 'Unknown')})", expanded=True):
                    t_name = st.text_input(f"Criteria", value=sale.get("tier", ""), key=f"pl_tier_name_{i}")
                    t_open = editable_date_row("Sale Opens", sale.get("open", ""), f"pl_tier_open_{i}")
                    t_close = editable_date_row("Closes", sale.get("close", "TBA"), f"pl_tier_close_{i}")
                    edited_pl_sales.append({"tier": t_name, "open": t_open, "close": t_close})

        with st.container(border=True):
            st.markdown("#### 🗳️ Local & YA Ballots")
            pl_b_open = editable_date_row("Ballots Open", d.get("ballot_open", ""), "pl_b_open")
            pl_b_close = editable_date_row("Ballots Close", d.get("ballot_close", ""), "pl_b_close")
            pl_b_res = editable_date_row("Ballots Results", d.get("ballot_results", "TBA"), "pl_b_res")

        with st.container(border=True):
            st.markdown("#### 🏟️ Match Details")
            pl_m_date = editable_date_row("Match Date & Time", d.get("match_date", ""), "pl_m_date")

        st.write("")
        if st.button("Generate PL Tweet Timelines & Calendars 🚀", type="primary", use_container_width=True, key="pl_gen"):
            sales_text_announcement = ""
            for s in edited_pl_sales:
                tier_label = "Sale (4+ Only)" if "4+" in s['tier'] else s['tier']
                sales_text_announcement += f"• {tier_label}: {s['open']}\n"

            announcement_tweet = f"""{pl_m_name} (H) - Sale Details 📢\n\nRegistration (All Members) 📝\n• Opens: {pl_r_open}\n• Closes: {pl_r_close}\n• Sale links sent: {pl_l_sent}\n\nSales 🎟️\n{sales_text_announcement}\nLocal & YA Ballots 🗳️\n• Opens: {pl_b_open}\n• Closes: {pl_b_close}\n• Results: {pl_b_res}\n\nMatch Date • {pl_m_date} 🏟️"""
            reg_open_tweet = f"""{pl_m_name} (H) - Registration 📢\n\nRegistration (All Members) 📝\n• Opens: Now\n• Closes: {pl_r_close}\n\n• Sale links sent: {pl_l_sent}\n\nSale 🎟️\n• {edited_pl_sales[0]['open'] if edited_pl_sales else 'TBA'}"""
            reg_close_time_str = pl_r_close.split(', ')[-1] if ',' in pl_r_close else pl_r_close
            reg_close_tweet = f"""{pl_m_name} (H) - Registration 📢\n\nRegistration (All Members) 📝\n• Opens: Now\n• Closes: Today {reg_close_time_str}\n\n• Sale links sent: {pl_l_sent}\n\nSale 🎟️\n• {edited_pl_sales[0]['open'] if edited_pl_sales else 'TBA'}"""
            
            ballots_open_tweet = f"""{pl_m_name} (H) - Ballots 📢\n\nLocal Ballots & YA Ballot 🗳️\n• Opens: Now\n• Closes: {pl_b_close}\n\n• Results: {pl_b_res}\n\nhttps://ticketing.liverpoolfc.com/tickets/ballots"""
            ballots_close_time_str = pl_b_close.split(', ')[-1] if ',' in pl_b_close else pl_b_close
            ballots_close_tweet = f"""{pl_m_name} (H) - Ballots 📢\n\nLocal Ballots & YA Ballot 🗳️\n• Opens: Now\n• Closes: Today {ballots_close_time_str}\n\n• Results: {pl_b_res}\n\nhttps://ticketing.liverpoolfc.com/tickets/ballots"""
            ballots_res_tweet = f"""{pl_m_name} (H) - Local & YA Ballots 📢\n\nLocal & YA Ballot Results 🗳️\n• Results today\n• Ensure you have funds in your bank \n\nComment below if successful 👇"""

            reg_close_scheduled = get_offset_time(pl_r_close, hours_before=1)
            ballots_close_scheduled = get_offset_time(pl_b_close, hours_before=1)
            ballots_res_scheduled = get_results_day_morning(pl_b_res)

            tweets_timeline = [
                ("📢 Sales Detail Announcement", "Immediate / Upon Scanning", announcement_tweet),
                ("📝 Registration Opening Notice", pl_r_open, reg_open_tweet),
                ("⏰ Registration Closing Reminder", reg_close_scheduled, reg_close_tweet),
                ("🗳️ Ballots Opening", pl_b_open, ballots_open_tweet),
                ("⏰ Ballots Closing Reminder", ballots_close_scheduled, ballots_close_tweet),
                ("✨ Local & YA Ballot Results", ballots_res_scheduled, ballots_res_tweet)
            ]

            hallmap_url = get_hallmap_link(pl_m_name)
            for s in edited_pl_sales:
                rem_time = get_offset_time(s['open'], hours_before=1)
                link_time = get_offset_time(s['open'], hours_before=0.5)
                tier_display = "Sale (4+ Only)" if "4+" in s['tier'] else f"{s['tier']} Sale"
                rem_tweet = f"""{pl_m_name} (H) - {tier_display} 📢\n\n{tier_display} 🎟️\n• Opens: {s['open']}\n• Click unique links from {link_time}\n\nHallmap link  👇\n{hallmap_url}"""
                tweets_timeline.append((f"🎟️ Sale Reminder & Unique Links ({s['tier']})", rem_time, rem_tweet))

            st.session_state["active_pl_tweets"] = tweets_timeline

        if "active_pl_tweets" in st.session_state and st.session_state["active_pl_tweets"]:
            with st.container(border=True):
                st.markdown("### ⚡ Buffer Automation")
                
                if st.button("🚀 Schedule All Reminders to Buffer Queue", type="primary", use_container_width=True, key="buffer_schedule_action_pl"):
                    progress_bar = st.progress(0)
                    success_count = 0
                    active_list = st.session_state["active_pl_tweets"]

                    queue_list = [t for t in active_list if "Sales Detail Announcement" not in t[0] and "Sales Details Announcement" not in t[0]]

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

                    st.success(f"🎉 Successfully scheduled {success_count}/{len(queue_list)} reminders to your Buffer queue! (Announcement post excluded)")

                st.divider()
                st.markdown("### 🐦 Preview Scheduled Timeline")
                for title, post_time, content in st.session_state["active_pl_tweets"]:
                    with st.expander(f"{title} — *Scheduled: {post_time}*"):
                        st.code(content, language="text")
                        x_url = get_x_intent_url(content)
                        st.link_button(f"🌐 Post on X via Browser ({title})", x_url, use_container_width=True)


# ==========================================
# TAB 2: CUP GAMES
# ==========================================
with tab2:
    with st.container(border=True):
        cup_url = st.text_input("🔗 Ticket Page URL (Cup):", placeholder="https://www.liverpoolfc.com/tickets/...", key="cup_url")
        if st.button("Scan Cup Page 🔍", use_container_width=True, key="cup_scan"):
            if not cup_url:
                st.error("Please provide the URL.")
            else:
                status_placeholder = st.empty()
                progress_bar = st.progress(0)
                try:
                    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
                    response = requests.get(cup_url, headers=headers, timeout=10)
                    soup = BeautifulSoup(response.text, 'html.parser')
                    for script in soup(["script", "style", "nav", "footer", "header"]):
                        script.extract()
                    page_text = " ".join(soup.get_text().split())[:35000] 

                    genai.configure(api_key=api_key)
                    model = genai.GenerativeModel('gemini-2.5-flash')
                    
                    max_attempts = 10
                    best_data = None
                    least_tbas = 999

                    for attempt in range(1, max_attempts + 1):
                        status_placeholder.info(f"⏳ Scanning Cup details (Attempt {attempt}/{max_attempts})...")
                        progress_bar.progress(attempt / max_attempts)

                        prompt = f"""
                        Analyze this raw text from an LFC Cup match ticket page.
                        Extract opponent name, sales tiers with open/close times, local ballot dates, and ACS dates.
                        DO NOT return 'TBA' if the date exists anywhere in the text.
                        
                        Desired JSON Format:
                        {{
                          "match_name": "Spurs",
                          "sales": [
                            {{"tier": "Credit balance of 2", "open": "Day Date, Time", "close": "Day Date, Time"}}
                          ],
                          "ballot_open": "Day Date, Time",
                          "ballot_close": "Day Date, Time",
                          "ballot_results": "Day Date",
                          "acs_start": "Day Date",
                          "acs_end": "Day Date",
                          "match_date": "Day Date, Time"
                        }}
                        Source Text: {page_text}
                        """
                        ai_response = model.generate_content(prompt)
                        cleaned_json = ai_response.text.strip().replace("```json", "").replace("```", "").strip()
                        extracted = json.loads(cleaned_json)

                        tba_count = 0
                        core_fields = ["match_name", "ballot_open", "ballot_close", "ballot_results", "acs_start", "acs_end", "match_date"]
                        for field in core_fields:
                            val = str(extracted.get(field, "TBA")).upper()
                            if "TBA" in val or not val.strip():
                                tba_count += 1
                        
                        sales = extracted.get("sales", [])
                        if not sales or any("TBA" in str(s.get("open", "TBA")).upper() for s in sales):
                            tba_count += 1

                        if tba_count < least_tbas:
                            least_tbas = tba_count
                            best_data = extracted

                        if tba_count == 0:
                            status_placeholder.success(f"✅ All fields identified on attempt {attempt}!")
                            break

                    st.session_state.cup_data = best_data
                    progress_bar.empty()
                    if least_tbas == 0:
                        st.toast("✅ Cup data successfully extracted with 0 TBAs!")
                    else:
                        status_placeholder.warning(f"Extracted best match ({least_tbas} unlisted/TBA fields after {max_attempts} attempts).")

                except Exception as e:
                    st.error(f"Failed to automatically pull details: {e}")

    if st.session_state.cup_data:
        st.write("")
        st.subheader("⚙️ Refine Details")
        cd = st.session_state.cup_data
        
        with st.container(border=True):
            cup_m_name = st.text_input("Opponent Team Name", value=cd.get("match_name", ""), key="cup_m_name")
        
        with st.container(border=True):
            st.markdown("#### 🎟️ Tiered Sales")
            edited_sales = []
            for i, sale in enumerate(cd.get("sales", [])):
                with st.expander(f"Sale Tier {i+1} ({sale.get('tier', 'Unknown')})", expanded=True):
                    t_name = st.text_input(f"Criteria", value=sale.get("tier", ""), key=f"tier_name_{i}")
                    t_open = editable_date_row("Opens", sale.get("open", ""), f"tier_open_{i}")
                    t_close = editable_date_row("Closes", sale.get("close", ""), f"tier_close_{i}")
                    edited_sales.append({"tier": t_name, "open": t_open, "close": t_close})

        with st.container(border=True):
            st.markdown("#### 🗳️ Local Ballot (No YA for Cup)")
            cup_b_open = editable_date_row("Local Ballot Opens", cd.get("ballot_open", ""), "cup_b_open")
            cup_b_close = editable_date_row("Local Ballot Closes", cd.get("ballot_close", ""), "cup_b_close")
            cup_b_res = editable_date_row("Local Ballot Results", cd.get("ballot_results", "TBA"), "cup_b_res")

        with st.container(border=True):
            st.markdown("#### 💰 Auto Cup Scheme & Match Details")
            cup_acs_start = editable_date_row("ACS Payment Start", cd.get("acs_start", ""), "cup_acs_start")
            cup_acs_end = editable_date_row("ACS Payment End", cd.get("acs_end", ""), "cup_acs_end")
            cup_m_date = editable_date_row("Match Date & Time", cd.get("match_date", ""), "cup_m_date")

        st.write("")
        if st.button("Generate Cup Tweet Timelines & Calendars 🚀", type="primary", use_container_width=True, key="cup_gen"):
            sales_formatted_text = ""
            for s in edited_sales:
                sales_formatted_text += f"Sale ({s['tier']})\n• Opens: {s['open']}\n• Closes: {s['close']}\n\n"

            announcement_tweet = f"""{cup_m_name} (H) - Sale Details 📢\n\n{sales_formatted_text}Local Ballot 🗳️\n• Opens: {cup_b_open}\n• Closes: {cup_b_close}\n• Results: {cup_b_res}\n\nACS Payment Run 💰\n• {cup_acs_start} - {cup_acs_end}\n\nMatch Date • {cup_m_date} 🏟️"""
            ballots_res_tweet = f"""{cup_m_name} (H) - Local Ballots 📢\n\nLocal Ballot Results 🗳️\n• Results today\n• Ensure you have funds in your bank \n\nComment below if successful 👇"""

            cup_ballots_res_scheduled = get_results_day_morning(cup_b_res)

            cup_tweets_timeline = [
                ("📢 Cup Sales Announcement", "Immediate / Upon Scanning", announcement_tweet),
                ("🗳️ Local Ballot Opening", cup_b_open, f"""{cup_m_name} (H) - Local Ballot 📢\n\nLocal Ballot 🗳️\n• Opens: Now\n• Closes: {cup_b_close}\n\nhttps://ticketing.liverpoolfc.com/tickets/ballots"""),
                ("⏰ Local Ballot Closing Reminder", get_offset_time(cup_b_close, hours_before=1), f"""{cup_m_name} (H) - Local Ballot 📢\n\nLocal Ballot 🗳️\n• Opens: Now\n• Closes: Today {cup_b_close.split(', ')[-1] if ',' in cup_b_close else cup_b_close}\n\nhttps://ticketing.liverpoolfc.com/tickets/ballots"""),
                ("🗳️ Local Ballot Results", cup_ballots_res_scheduled, ballots_res_tweet)
            ]

            for s in edited_sales:
                opening_tweet = f"""{cup_m_name} (League Cup)  🎟️\n\nSale ({s['tier']})\n• Opens: {s['open']}\n• Closes: {s['close']}\n\nNo registration needed, pre-queue starts 30 minutes before."""
                cup_tweets_timeline.append((f"🎟️ Sale Reminder ({s['tier']})", get_offset_time(s['open'], hours_before=1), opening_tweet))
                
                if check_sale_duration(s['open'], s['close']):
                    closing_tweet = f"""{cup_m_name} (H) 🎟️\n\nSale ({s['tier']})\n• Opens: {s['open']}\n• Closes: {s['close']}"""
                    cup_tweets_timeline.append((f"⏰ Sale Closing Reminder ({s['tier']})", get_offset_time(s['close'], hours_before=1), closing_tweet))

            st.session_state["active_cup_tweets"] = cup_tweets_timeline

        if "active_cup_tweets" in st.session_state and st.session_state["active_cup_tweets"]:
            with st.container(border=True):
                st.markdown("### ⚡ Buffer Automation")
                
                if st.button("🚀 Schedule All Reminders to Buffer Queue", type="primary", use_container_width=True, key="buffer_schedule_action_cup"):
                    progress_bar = st.progress(0)
                    success_count = 0
                    active_list = st.session_state["active_cup_tweets"]

                    queue_list = [t for t in active_list if "Sales Announcement" not in t[0] and "Sales Detail Announcement" not in t[0]]

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

                    st.success(f"🎉 Successfully scheduled {success_count}/{len(queue_list)} reminders to your Buffer queue! (Announcement post excluded)")

                st.divider()
                st.markdown("### 🐦 Preview Scheduled Timeline")
                for title, post_time, content in st.session_state["active_cup_tweets"]:
                    with st.expander(f"{title} — *Scheduled: {post_time}*"):
                        st.code(content, language="text")
                        x_url = get_x_intent_url(content)
                        st.link_button(f"🌐 Post on X via Browser ({title})", x_url, use_container_width=True)


# ==========================================
# TAB 3: LEAGUE AWAYS
# ==========================================
with tab3:
    with st.container(border=True):
        away_url = st.text_input("🔗 Ticket Page URL (Away):", placeholder="https://www.liverpoolfc.com/tickets/...", key="away_url")
        if st.button("Scan Away Page 🔍", use_container_width=True, key="away_scan"):
            if not away_url:
                st.error("Please provide the URL.")
            else:
                status_placeholder = st.empty()
                progress_bar = st.progress(0)
                try:
                    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
                    response = requests.get(away_url, headers=headers, timeout=10)
                    soup = BeautifulSoup(response.text, 'html.parser')
                    for script in soup(["script", "style", "nav", "footer", "header"]):
                        script.extract()
                    page_text = " ".join(soup.get_text().split())[:35000] 

                    genai.configure(api_key=api_key)
                    model = genai.GenerativeModel('gemini-2.5-flash')

                    max_attempts = 10
                    best_data = None
                    least_tbas = 999

                    for attempt in range(1, max_attempts + 1):
                        status_placeholder.info(f"⏳ Scanning Away details (Attempt {attempt}/{max_attempts})...")
                        progress_bar.progress(attempt / max_attempts)

                        prompt = f"""
                        Analyze this raw text from an LFC Away match ticket page. Exclude disabled/wheelchair sales.
                        Extract opponent name, sales tiers with open/close times, and forwarding deadline.
                        DO NOT return 'TBA' if the date exists anywhere in the text.
                        
                        Desired JSON Format:
                        {{
                          "match_name": "Lask",
                          "sales": [
                            {{"tier": "9+ Away Credit Balance", "open": "Day Date, Time", "close": "Day Date, Time"}}
                          ],
                          "forwarding_deadline": "Day Date, Time",
                          "match_date": "Day Date, Time"
                        }}
                        Source Text: {page_text}
                        """
                        ai_response = model.generate_content(prompt)
                        cleaned_json = ai_response.text.strip().replace("```json", "").replace("```", "").strip()
                        extracted = json.loads(cleaned_json)

                        tba_count = 0
                        core_fields = ["match_name", "forwarding_deadline", "match_date"]
                        for field in core_fields:
                            val = str(extracted.get(field, "TBA")).upper()
                            if "TBA" in val or not val.strip():
                                tba_count += 1
                        
                        sales = extracted.get("sales", [])
                        if not sales or any("TBA" in str(s.get("open", "TBA")).upper() for s in sales):
                            tba_count += 1

                        if tba_count < least_tbas:
                            least_tbas = tba_count
                            best_data = extracted

                        if tba_count == 0:
                            status_placeholder.success(f"✅ All fields identified on attempt {attempt}!")
                            break

                    st.session_state.away_data = best_data
                    progress_bar.empty()
                    if least_tbas == 0:
                        st.toast("✅ Away data successfully extracted with 0 TBAs!")
                    else:
                        status_placeholder.warning(f"Extracted best match ({least_tbas} unlisted/TBA fields after {max_attempts} attempts).")

                except Exception as e:
                    st.error(f"Failed to automatically pull details: {e}")

    if st.session_state.away_data:
        st.write("")
        st.subheader("⚙️ Refine Details")
        ad = st.session_state.away_data
        
        with st.container(border=True):
            away_m_name = st.text_input("Opponent Team Name", value=ad.get("match_name", ""), key="away_m_name")
            away_fwd = editable_date_row("Forwarding Deadline", ad.get("forwarding_deadline", "TBA"), "away_fwd")
        
        with st.container(border=True):
            st.markdown("#### 🎟️ Tiered Sales")
            edited_away_sales = []
            for i, sale in enumerate(ad.get("sales", [])):
                with st.expander(f"Sale Tier {i+1} ({sale.get('tier', 'Unknown')})", expanded=True):
                    t_name = st.text_input(f"Criteria", value=sale.get("tier", ""), key=f"away_tier_name_{i}")
                    t_open = editable_date_row("Opens", sale.get("open", ""), f"away_tier_open_{i}")
                    t_close = editable_date_row("Closes", sale.get("close", ""), f"away_tier_close_{i}")
                    edited_away_sales.append({"tier": t_name, "open": t_open, "close": t_close})

        with st.container(border=True):
            st.markdown("#### 🏟️ Match Details")
            away_m_date = editable_date_row("Match Date & Time", ad.get("match_date", ""), "away_m_date")

        st.write("")
        if st.button("Generate Away Tweet Timelines & Calendars 🚀", type="primary", use_container_width=True, key="away_gen"):
            announcement_blocks = []
            for s in edited_away_sales:
                announcement_blocks.append(f"Sale ({s['tier']})\n• Opens: {s['open']}\n• Closes: {s['close']}")
            sales_blocks_text = "\n\n".join(announcement_blocks)

            announcement_tweet = f"""{away_m_name} (A) - Sale Details 📢\n\n{sales_blocks_text}\n\nMatch Date • {away_m_date} 🏟️"""

            away_tweets = [
                ("📢 Sales Detail Announcement", "Immediate / Upon Scanning", announcement_tweet)
            ]

            for s in edited_away_sales:
                sale_tweet = f"""{away_m_name} (A) 🎟️\n\nSale ({s['tier']})\n• Opens: {s['open']}\n• Closes: {s['close']}\n• Guaranteed Sale"""
                away_tweets.append((f"🎟️ Sale Reminder ({s['tier']})", get_offset_time(s['open'], hours_before=1), sale_tweet))

            if away_fwd != "TBA":
                fwd_tweet = f"""Forwarding Deadline ➡️\n• Closes: {away_fwd}"""
                away_tweets.append(("➡️ Forwarding Deadline Reminder", get_offset_time(away_fwd, hours_before=1), fwd_tweet))

            st.session_state["active_away_tweets"] = away_tweets

        if "active_away_tweets" in st.session_state and st.session_state["active_away_tweets"]:
            with st.container(border=True):
                st.markdown("### ⚡ Buffer Automation")
                
                if st.button("🚀 Schedule All Reminders to Buffer Queue", type="primary", use_container_width=True, key="buffer_schedule_action_away"):
                    progress_bar = st.progress(0)
                    success_count = 0
                    active_list = st.session_state["active_away_tweets"]

                    queue_list = [t for t in active_list if "Sales Detail Announcement" not in t[0] and "Sales Details Announcement" not in t[0]]

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

                    st.success(f"🎉 Successfully scheduled {success_count}/{len(queue_list)} reminders to your Buffer queue! (Announcement post excluded)")

                st.divider()
                st.markdown("### 🐦 Preview Scheduled Timeline")
                for title, post_time, content in st.session_state["active_away_tweets"]:
                    with st.expander(f"{title} — *Scheduled: {post_time}*"):
                        st.code(content, language="text")
                        x_url = get_x_intent_url(content)
                        st.link_button(f"🌐 Post on X via Browser ({title})", x_url, use_container_width=True)


# ==========================================
# TAB 4: CHAMPIONS LEAGUE AWAYS
# ==========================================
with tab4:
    with st.container(border=True):
        cl_url = st.text_input("🔗 Ticket Page URL (CL Away):", placeholder="https://www.liverpoolfc.com/tickets/...", key="cl_url")

        if st.button("Scan CL Away Page 🔍", use_container_width=True, key="cl_scan"):
            if not cl_url:
                st.error("Please provide the URL.")
            else:
                status_placeholder = st.empty()
                progress_bar = st.progress(0)
                try:
                    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
                    response = requests.get(cl_url, headers=headers, timeout=10)
                    soup = BeautifulSoup(response.text, 'html.parser')
                    for script in soup(["script", "style", "nav", "footer", "header"]):
                        script.extract()
                    page_text = " ".join(soup.get_text().split())[:35000] 

                    genai.configure(api_key=api_key)
                    model = genai.GenerativeModel('gemini-2.5-flash')

                    max_attempts = 10
                    best_data = None
                    least_tbas = 999

                    for attempt in range(1, max_attempts + 1):
                        status_placeholder.info(f"⏳ Scanning CL Away details (Attempt {attempt}/{max_attempts})...")
                        progress_bar.progress(attempt / max_attempts)

                        prompt = f"""
                        Analyze this raw text from an LFC Champions League / European Away match ticket page. Exclude disabled/wheelchair sales.
                        Extract opponent name, match date, and sales tiers.
                        CRITICAL INSTRUCTIONS:
                        1. For 'tier', simplify the criteria name to something clean like '9+ Games' or '9+ Away Credit Balance'.
                        2. Look inside EACH sale section for the sentence mentioning 'Forwarding Deadline' and extract its exact date and time into 'forwarding_deadline' for that tier.
                        3. For 'info', extract whether it states 'Guaranteed Sale' (or 'guaranteed'), 'Subject to availability', or leave empty if not stated.
                        DO NOT return 'TBA' if the date exists anywhere in the text.

                        Desired JSON Format:
                        {{
                          "match_name": "LASK",
                          "sales": [
                            {{
                              "tier": "9+ Games",
                              "open": "Wed 23 Sep, 8:15am",
                              "close": "Thu 24 Sep, 7:30am",
                              "info": "Guaranteed Sale",
                              "forwarding_deadline": "Thu 24 Sep, 11:00am"
                            }}
                          ],
                          "match_date": "Wed 14 Oct, 5:45pm"
                        }}
                        Source Text: {page_text}
                        """
                        ai_response = model.generate_content(prompt)
                        cleaned_json = ai_response.text.strip().replace("```json", "").replace("```", "").strip()
                        extracted = json.loads(cleaned_json)

                        tba_count = 0
                        core_fields = ["match_name", "match_date"]
                        for field in core_fields:
                            val = str(extracted.get(field, "TBA")).upper()
                            if "TBA" in val or not val.strip():
                                tba_count += 1
                        
                        sales = extracted.get("sales", [])
                        if not sales:
                            tba_count += 1
                        else:
                            for s in sales:
                                if "TBA" in str(s.get("open", "TBA")).upper():
                                    tba_count += 1
                                if "TBA" in str(s.get("forwarding_deadline", "TBA")).upper():
                                    tba_count += 1

                        if tba_count < least_tbas:
                            least_tbas = tba_count
                            best_data = extracted

                        if tba_count == 0:
                            status_placeholder.success(f"✅ All fields identified on attempt {attempt}!")
                            break

                    st.session_state.cl_away_data = best_data
                    progress_bar.empty()
                    if least_tbas == 0:
                        st.toast("✅ CL Away data successfully extracted with 0 TBAs!")
                    else:
                        status_placeholder.warning(f"Extracted best match ({least_tbas} unlisted/TBA fields after {max_attempts} attempts).")

                except Exception as e:
                    st.error(f"Failed to automatically pull details: {e}")

    if st.session_state.cl_away_data:
        st.write("")
        st.subheader("⚙️ Refine Details")
        cld = st.session_state.cl_away_data
        
        with st.container(border=True):
            cl_m_name = st.text_input("Opponent Team Name", value=cld.get("match_name", ""), key="cl_m_name")

        with st.container(border=True):
            st.markdown("#### 🎟️ Tiered Sales & Forwarding Deadlines")
            edited_cl_sales = []
            for i, sale in enumerate(cld.get("sales", [])):
                with st.expander(f"Sale Tier {i+1} ({sale.get('tier', 'Unknown')})", expanded=True):
                    t_name = st.text_input(f"Criteria", value=sale.get("tier", ""), key=f"cl_tier_name_{i}")
                    t_open = editable_date_row("Opens", sale.get("open", ""), f"cl_tier_open_{i}")
                    t_close = editable_date_row("Closes", sale.get("close", ""), f"cl_tier_close_{i}")
                    t_info = st.text_input("Information / Guarantee", value=sale.get("info", ""), key=f"cl_tier_info_{i}")
                    t_fwd = editable_date_row("Forwarding Deadline", sale.get("forwarding_deadline", "TBA"), f"cl_tier_fwd_{i}")
                    edited_cl_sales.append({"tier": t_name, "open": t_open, "close": t_close, "info": t_info, "forwarding_deadline": t_fwd})

        with st.container(border=True):
            cl_m_date = editable_date_row("Match Date & Time", cld.get("match_date", ""), "cl_m_date")

        st.write("")
        if st.button("Generate CL Away Tweet Timelines & Calendars 🚀", type="primary", use_container_width=True, key="cl_gen"):
            announcement_blocks = []
            for s in edited_cl_sales:
                info_tag = ""
                if "guaranteed" in s['info'].lower() and "non" not in s['info'].lower():
                    info_tag = " - Guaranteed"
                elif s['info'].strip():
                    info_tag = f" - {s['info'].strip()}"
                
                tier_label = s['tier']
                announcement_blocks.append(f"Sale ({tier_label}{info_tag})\n• Opens: {s['open']}\n• Closes: {s['close']}")
            
            sales_blocks_text = "\n\n".join(announcement_blocks)
            cl_announcement_tweet = f"""{cl_m_name} (A) - Sale Details 📢\n\n{sales_blocks_text}\n\nMatch Date • {cl_m_date} 🏟️"""

            cl_tweets = [
                ("📢 CL Away Sales Details Announcement", "Immediate / Upon Scanning", announcement_tweet)
            ]

            for s in edited_cl_sales:
                open_time_part = s['open'].split(', ')[-1] if ', ' in s['open'] else s['open']
                
                info_line = ""
                if s['info'].strip():
                    info_line = f"• {s['info'].strip()}\n"

                fwd_deadline_text = s['forwarding_deadline'].replace(',', ' -') if ',' in s['forwarding_deadline'] else s['forwarding_deadline']

                sale_tweet = f"""{cl_m_name} (A) 🎟️\n\nSale ({s['tier']})\n• Opens: Today - {open_time_part}\n• Closes: {s['close'].replace(',', ' -')}\n{info_line}\nForwarding Deadline ➡️\n• Closes: {fwd_deadline_text}"""
                
                cl_tweets.append((f"🎟️ Sale Reminder ({s['tier']})", get_offset_time(s['open'], hours_before=1), sale_tweet))

            st.session_state["active_cl_tweets"] = cl_tweets

        if "active_cl_tweets" in st.session_state and st.session_state["active_cl_tweets"]:
            with st.container(border=True):
                st.markdown("### ⚡ Buffer Automation")
                
                if st.button("🚀 Schedule All Reminders to Buffer Queue", type="primary", use_container_width=True, key="buffer_schedule_action_cl"):
                    progress_bar = st.progress(0)
                    success_count = 0
                    active_list = st.session_state["active_cl_tweets"]

                    queue_list = [t for t in active_list if "Sales Detail Announcement" not in t[0] and "Sales Details Announcement" not in t[0]]

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

                    st.success(f"🎉 Successfully scheduled {success_count}/{len(queue_list)} reminders to your Buffer queue! (Announcement post excluded)")

                st.divider()
                st.markdown("### 🐦 Preview Scheduled Timeline")
                for title, post_time, content in st.session_state["active_cl_tweets"]:
                    with st.expander(f"{title} — *Scheduled: {post_time}*"):
                        st.code(content, language="text")
                        x_url = get_x_intent_url(content)
                        st.link_button(f"🌐 Post on X via Browser ({title})", x_url, use_container_width=True)