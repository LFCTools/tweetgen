import streamlit as st
import google.generativeai as genai
import requests
from bs4 import BeautifulSoup
import urllib.parse
from datetime import datetime, timedelta

# --- HELPER FUNCTIONS FOR CALENDAR & TIME ---
def parse_to_datetime(date_str):
    if not date_str or date_str == "TBA" or "[" in date_str:
        return None
    try:
        clean_str = date_str.replace(',', '')
        parts = clean_str.split() 
        if len(parts) < 3: return None
            
        day = int(parts[1])
        month_str = parts[2][:3]
        month_num = datetime.strptime(month_str, '%b').month
        
        today = datetime.today()
        current_year = today.year
        if today.month >= 7 and month_num < 7:
            calc_year = current_year + 1
        elif today.month < 7 and month_num >= 7:
            calc_year = current_year - 1
        else:
            calc_year = current_year
        
        if len(parts) < 4:
            dt_str = f"{day} {month_str} {calc_year} 09:00am"
        else:
            time_str = parts[3].lower()
            dt_str = f"{day} {month_str} {calc_year} {time_str}"
            
        return datetime.strptime(dt_str, "%d %b %Y %I:%M%p" if ":" in dt_str.split()[-1] else "%d %b %Y %I%p")
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
    """Returns the correct Discord hallmap message link based on the opponent team."""
    base_msg_url = "https://discord.com/channels/1433125109811511346/1518135652531830785"
    return f"{base_msg_url}/1518135652531830785"

# --- MOBILE-OPTIMIZED UI WIDGET ---
def editable_date_row(label, default_val, key):
    """Creates a single-line toggle that drops down editing tools when active."""
    
    if f"{key}_default" not in st.session_state or st.session_state[f"{key}_default"] != default_val:
        st.session_state[f"{key}_default"] = default_val
        dt_obj = parse_to_datetime(default_val)
        
        st.session_state[f"{key}_tba"] = not bool(dt_obj) or default_val == "TBA"
        st.session_state[f"{key}_allday"] = bool(default_val and default_val != "TBA" and not any(m in default_val.lower() for m in ['am','pm',':']))
        st.session_state[f"{key}_date"] = dt_obj.date() if dt_obj else datetime.today().date()
        st.session_state[f"{key}_time"] = dt_obj.time() if dt_obj else datetime.strptime("10:00am", "%I:%M%p").time()

    is_tba = st.session_state[f"{key}_tba"]
    all_day = st.session_state[f"{key}_allday"]
    d = st.session_state[f"{key}_date"]
    t = st.session_state[f"{key}_time"]

    if is_tba:
        display_val = "TBA"
    elif all_day:
        display_val = d.strftime("%a %d %b")
    else:
        time_formatted = t.strftime("%I:%M%p").lstrip("0").lower()
        display_val = f"{d.strftime('%a %d %b')}, {time_formatted}"

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

if "pl_data" not in st.session_state:
    st.session_state.pl_data = None
if "cup_data" not in st.session_state:
    st.session_state.cup_data = None
if "away_data" not in st.session_state:
    st.session_state.away_data = None

# --- MAIN HEADER ---
st.title("🔴 LFC Ticket Alerts & Tweet Scheduler")
st.markdown("Automate your custom sale templates, scheduled tweet timelines, and calendar schedules instantly.")
st.write("")

# --- TABS ---
tab1, tab2, tab3 = st.tabs(["🏆 Premier League (Home)", "🏅 Cup Games (Home)", "✈️ League Aways"])

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
                with st.spinner("Analyzing ticketing page..."):
                    try:
                        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
                        response = requests.get(pl_url, headers=headers, timeout=5)
                        soup = BeautifulSoup(response.text, 'html.parser')
                        for script in soup(["script", "style", "nav", "footer", "header"]):
                            script.extract()
                        page_text = " ".join(soup.get_text().split())[:20000]

                        genai.configure(api_key=api_key)
                        model = genai.GenerativeModel('gemini-2.5-flash')
                        
                        prompt = f"""
                        Analyze the following raw text from an LFC Premier League ticket page. 
                        Carefully search the sale and registration sections for when unique links are sent to members.
                        Extract unified registration details, local & YA ballot open/close/results dates, link sending dates, and ticket sale opening dates per tier (e.g. 4+ Members, All Members).
                        Respond ONLY with a valid raw JSON object matching these exact keys. 
                        Use abbreviated days (e.g., Wed) and months (e.g., Nov) and format times like 10:00am or 11:00am.
                        If any field is missing, make its value "TBA".
                        
                        Desired JSON Format:
                        {{
                          "match_name": "Only the opponent team name (e.g., Fulham)",
                          "reg_open": "Day Date, Time",
                          "reg_close": "Day Date, Time",
                          "links_sent": "Day Date, Time",
                          "sales": [
                            {{"tier": "All Members", "open": "Day Date, Time", "close": "Day Date, Time"}}
                          ],
                          "ballot_open": "Day Date, Time",
                          "ballot_close": "Day Date, Time",
                          "ballot_results": "TBA or Day Date",
                          "match_date": "Day Date, Time"
                        }}
                        Source Text: {page_text}
                        """
                        ai_response = model.generate_content(prompt)
                        json_text = ai_response.text.strip().replace("```json", "").replace("```", "")
                        import json
                        st.session_state.pl_data = json.loads(json_text)
                        st.toast("✅ Data successfully extracted!")
                        
                    except Exception as e:
                        st.error(f"Failed to automatically pull details: {e}")

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
                    t_close = editable_date_row("Sale Closes", sale.get("close", "TBA"), f"pl_tier_close_{i}")
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

            announcement_tweet = f"""{pl_m_name} (H) - Sale Details 📢\n
Registration (All Members) 📝
• Opens: {pl_r_open}
• Closes: {pl_r_close}
• Sale links sent: {pl_l_sent}\n
Sales 🎟️\n{sales_text_announcement}
Local & YA Ballots 🗳️
• Opens: {pl_b_open}
• Closes: {pl_b_close}
• Results: {pl_b_res}\n
Match Date • {pl_m_date} 🏟️"""

            reg_tweet = f"""{pl_m_name} (H) - Registration 📢

Registration (All Members) 📝
• Opens: Now
• Closes: {pl_r_close}

• Sale links sent: {pl_l_sent}

Sale 🎟️
• {edited_pl_sales[0]['open'] if edited_pl_sales else 'TBA'}"""

            ballots_open_tweet = f"""{pl_m_name} (H) - Ballots 📢

Local Ballots & YA Ballot 🗳️
• Opens: Now
• Closes: {pl_b_close}

• Results: {pl_b_res}

https://ticketing.liverpoolfc.com/tickets/ballots"""

            ballots_close_tweet = f"""{pl_m_name} (H) - Ballots 📢

Local Ballots & YA Ballot 🗳️
• Opens: Now
• Closes: Today {pl_b_close.split(', ')[-1] if ',' in pl_b_close else pl_b_close}

• Results: {pl_b_res}

https://ticketing.liverpoolfc.com/tickets/ballots"""

            ballots_res_tweet = f"""{pl_m_name} (H) - Local & YA Ballots 📢

Local & YA Ballot Results 🗳️
• Results today
• Ensure you have funds in your bank 

Comment below if successful 👇"""

            st.write("")
            with st.container(border=True):
                st.markdown("### 🐦 Scheduled Tweet Timeline")
                
                tweets_timeline = [
                    ("📢 Sales Detail Announcement", "Immediate / Upon Scanning", announcement_tweet),
                    ("📝 Registration Opening/Closing Notice", pl_r_open, reg_tweet),
                    ("🗳️ Ballots Opening", pl_b_open, ballots_open_tweet),
                    ("⏰ Ballots Closing", pl_b_close, ballots_close_tweet),
                    ("✨ Local & YA Ballot Results", pl_b_res, ballots_res_tweet)
                ]

                hallmap_url = get_hallmap_link(pl_m_name)

                for s in edited_pl_sales:
                    rem_time = get_offset_time(s['open'], hours_before=1)
                    link_time = get_offset_time(s['open'], hours_before=0.5)
                    tier_display = "Sale (4+ Only)" if "4+" in s['tier'] else f"{s['tier']} Sale"
                    rem_tweet = f"""{pl_m_name} (H) - {tier_display} 📢

{tier_display} 🎟️
• Opens: {s['open']}
• Click unique links from {link_time}

Hallmap link  👇
{hallmap_url}"""
                    tweets_timeline.append((f"🎟️ Sale Reminder & Unique Links ({s['tier']})", rem_time, rem_tweet))

                for title, post_time, content in tweets_timeline:
                    with st.expander(f"{title} — *Scheduled: {post_time}*"):
                        st.code(content, language="text")
                        x_url = get_x_intent_url(content)
                        st.link_button(f"🌐 Post on X via Browser ({title})", x_url, use_container_width=True)

            with st.container(border=True):
                st.markdown("### 📅 Calendar Schedule Links")
                events = [
                    {"label": "Registration Open", "name": f"{pl_m_name} (H) - Registration Opens", "time": pl_r_open, "all_day": False},
                    {"label": "Registration Closes", "name": f"{pl_m_name} (H) - Registration Closes", "time": pl_r_close, "all_day": False},
                    {"label": "Unique Links Sent", "name": f"{pl_m_name} (H) - Unique Links Sent", "time": pl_l_sent, "all_day": False},
                    {"label": "Ballots Open", "name": f"{pl_m_name} (H) - Ballots Open", "time": pl_b_open, "all_day": False},
                    {"label": "Ballots Close", "name": f"{pl_m_name} (H) - Ballots Close", "time": pl_b_close, "all_day": False},
                    {"label": "Ballots Results", "name": f"{pl_m_name} (H) - Ballots Results", "time": f"{pl_b_res} 09:00am" if pl_b_res != "TBA" else "TBA", "all_day": True}
                ]
                for s in edited_pl_sales:
                    events.append({"label": f"Sale ({s['tier']})", "name": f"{pl_m_name} (H) - Sale ({s['tier']})", "time": s['open'], "all_day": False})
                events.append({"label": "Match Day", "name": f"{pl_m_name} (H) - Match Date", "time": pl_m_date, "all_day": False})

                for i, ev in enumerate(events):
                    if ev["time"] and ev["time"] != "TBA":
                        dt_obj = parse_to_datetime(ev["time"])
                        gcal_dates = format_gcal_date(dt_obj, is_all_day=ev["all_day"])
                        if gcal_dates:
                            encoded_name = urllib.parse.quote(ev["name"])
                            gcal_url = f"https://calendar.google.com/calendar/render?action=TEMPLATE&text={encoded_name}&dates={gcal_dates}"
                            st.link_button(f"📅 Add **{ev['label']}** ({ev['time']})", gcal_url, use_container_width=True)
                        else:
                            st.warning(f"⚠️ Unable to parse format for: {ev['label']}")
                    else:
                        st.button(f"⚪ {ev['label']} (TBA)", disabled=True, use_container_width=True, key=f"tba_btn_pl_{i}")


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
                with st.spinner("Analyzing Cup ticketing page..."):
                    try:
                        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
                        response = requests.get(cup_url, headers=headers, timeout=5)
                        soup = BeautifulSoup(response.text, 'html.parser')
                        for script in soup(["script", "style", "nav", "footer", "header"]):
                            script.extract()
                        page_text = " ".join(soup.get_text().split())[:20000] 

                        genai.configure(api_key=api_key)
                        model = genai.GenerativeModel('gemini-2.5-flash')
                        
                        prompt = f"""
                        Analyze the following raw text from an LFC Cup match ticket page. Extract opponent name, sales tiers with open/close times, local ballot dates, and ACS dates.
                        Respond ONLY with a valid raw JSON object matching the structure below. 
                        Use abbreviated days (e.g., Wed) and months (e.g., Nov) and format times like 10:00am or 11:00am.
                        If any field is missing, make its value "TBA".
                        
                        Desired JSON Format:
                        {{
                          "match_name": "Only the opponent team name (e.g., Spurs)",
                          "sales": [
                            {{"tier": "Credit balance of 2", "open": "Day Date, Time", "close": "Day Date, Time"}}
                          ],
                          "ballot_open": "Day Date, Time",
                          "ballot_close": "Day Date, Time",
                          "ballot_results": "TBA or Day Date",
                          "acs_start": "Day Date",
                          "acs_end": "Day Date",
                          "match_date": "Day Date, Time"
                        }}
                        Source Text: {page_text}
                        """
                        ai_response = model.generate_content(prompt)
                        json_text = ai_response.text.strip().replace("```json", "").replace("```", "")
                        import json
                        st.session_state.cup_data = json.loads(json_text)
                        st.toast("✅ Cup data successfully extracted!")
                        
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

            ballots_res_tweet = f"""{cup_m_name} (H) - Local Ballots 📢

Local Ballot Results 🗳️
• Results today
• Ensure you have funds in your bank 

Comment below if successful 👇"""

            cup_tweets_timeline = [
                ("📢 Cup Sales Announcement", "Immediate / Upon Scanning", announcement_tweet),
                ("🗳️ Local Ballot Results", cup_b_res, ballots_res_tweet)
            ]

            for s in edited_sales:
                opening_tweet = f"""{cup_m_name} (League Cup)  🎟️

Sale ({s['tier']})
• Opens: {s['open']}
• Closes: {s['close']}

No registration needed, pre-queue starts 30 minutes before."""
                cup_tweets_timeline.append((f"🎟️ Sale Opening ({s['tier']})", s['open'], opening_tweet))
                
                if check_sale_duration(s['open'], s['close']):
                    closing_tweet = f"""{cup_m_name} (H) 🎟️

Sale ({s['tier']})
• Opens: {s['open']}
• Closes: {s['close']}"""
                    cup_tweets_timeline.append((f"⏰ Sale Closing ({s['tier']})", s['close'], closing_tweet))

            with st.container(border=True):
                st.markdown("### 🐦 Scheduled Cup Tweet Timeline")
                for title, post_time, content in cup_tweets_timeline:
                    with st.expander(f"{title} — *Scheduled: {post_time}*"):
                        st.code(content, language="text")
                        x_url = get_x_intent_url(content)
                        st.link_button(f"🌐 Post on X via Browser ({title})", x_url, use_container_width=True)

            with st.container(border=True):
                st.markdown("### 📅 Calendar Schedule Links")
                events = [
                    {"label": "ACS Payment Run", "name": f"{cup_m_name} (H) - ACS Payment Run", "time": f"{cup_acs_start} 09:00am" if cup_acs_start != "TBA" else "TBA", "all_day": True},
                    {"label": "Local Ballot Open", "name": f"{cup_m_name} (H) - Local Ballot Opens", "time": cup_b_open, "all_day": False},
                    {"label": "Local Ballot Closes", "name": f"{cup_m_name} (H) - Local Ballot Closes", "time": cup_b_close, "all_day": False},
                    {"label": "Local Ballot Results", "name": f"{cup_m_name} (H) - Local Ballot Results", "time": cup_b_res, "all_day": True}
                ]
                for s in edited_sales:
                    events.append({"label": f"Sale Open ({s['tier']})", "name": f"{cup_m_name} (H) - Sale Opens ({s['tier']})", "time": s['open'], "all_day": False})
                    events.append({"label": f"Sale Close ({s['tier']})", "name": f"{cup_m_name} (H) - Sale Closes ({s['tier']})", "time": s['close'], "all_day": False})
                events.append({"label": "Match Day", "name": f"{cup_m_name} (H) - Match Date", "time": cup_m_date, "all_day": False})

                for i, ev in enumerate(events):
                    if ev["time"] and ev["time"] != "TBA":
                        dt_obj = parse_to_datetime(ev["time"])
                        gcal_dates = format_gcal_date(dt_obj, is_all_day=ev["all_day"])
                        if gcal_dates:
                            encoded_name = urllib.parse.quote(ev["name"])
                            gcal_url = f"https://calendar.google.com/calendar/render?action=TEMPLATE&text={encoded_name}&dates={gcal_dates}"
                            st.link_button(f"📅 Add **{ev['label']}** ({ev['time']})", gcal_url, use_container_width=True)
                        else:
                            st.warning(f"⚠️ Unable to parse format for: {ev['label']}")
                    else:
                        st.button(f"⚪ {ev['label']} (TBA)", disabled=True, use_container_width=True, key=f"tba_btn_cup_{i}_{ev['label']}")


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
                with st.spinner("Analyzing Away ticketing page..."):
                    try:
                        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
                        response = requests.get(away_url, headers=headers, timeout=5)
                        soup = BeautifulSoup(response.text, 'html.parser')
                        for script in soup(["script", "style", "nav", "footer", "header"]):
                            script.extract()
                            
                        page_text = " ".join(soup.get_text().split())[:20000] 

                        genai.configure(api_key=api_key)
                        model = genai.GenerativeModel('gemini-2.5-flash')
                        
                        prompt = f"""
                        Analyze the following raw text from an LFC Away match ticket page. Exclude any disabled/wheelchair/ambulant sales. 
                        Extract opponent name, sales tiers with open/close times, and forwarding deadline if present.
                        Respond ONLY with a valid raw JSON object matching the structure below. 
                        Use abbreviated days (e.g., Wed) and months (e.g., Nov) and format times like 10:00am or 11:00am.
                        If any field is missing, make its value "TBA".
                        
                        Desired JSON Format:
                        {{
                          "match_name": "Only the opponent team name (e.g., Lask)",
                          "sales": [
                            {{"tier": "9+ Away Credit Balance", "open": "Day Date, Time", "close": "Day Date, Time"}}
                          ],
                          "forwarding_deadline": "Day Date, Time",
                          "match_date": "Day Date, Time"
                        }}
                        Source Text: {page_text}
                        """
                        ai_response = model.generate_content(prompt)
                        json_text = ai_response.text.strip().replace("```json", "").replace("```", "")
                        import json
                        st.session_state.away_data = json.loads(json_text)
                        st.toast("✅ Away data successfully extracted!")
                        
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
            
            with st.container(border=True):
                st.markdown("### 🐦 Scheduled Away Tweet Timeline")
                
                away_tweets = []
                for s in edited_away_sales:
                    sale_tweet = f"""{away_m_name} (A) 🎟️

Sale ({s['tier']})
• Opens: {s['open']}
• Closes: {s['close']}
• Guaranteed Sale"""
                    away_tweets.append((f"🎟️ Sale ({s['tier']})", s['open'], sale_tweet))

                if away_fwd != "TBA":
                    fwd_tweet = f"""Forwarding Deadline ➡️
• Closes: {away_fwd}"""
                    away_tweets.append(("➡️ Forwarding Deadline", away_fwd, fwd_tweet))

                for title, post_time, content in away_tweets:
                    with st.expander(f"{title} — *Scheduled: {post_time}*"):
                        st.code(content, language="text")
                        x_url = get_x_intent_url(content)
                        st.link_button(f"🌐 Post on X via Browser ({title})", x_url, use_container_width=True)

            with st.container(border=True):
                st.markdown("### 📅 Calendar Schedule Links")
                events = []
                for s in edited_away_sales:
                    events.append({"label": f"Sale Open ({s['tier']})", "name": f"{away_m_name} (A) - Sale Opens ({s['tier']})", "time": s['open'], "all_day": False})
                    events.append({"label": f"Sale Close ({s['tier']})", "name": f"{away_m_name} (A) - Sale Closes ({s['tier']})", "time": s['close'], "all_day": False})
                if away_fwd != "TBA":
                    events.append({"label": "Forwarding Deadline", "name": f"{away_m_name} (A) - Forwarding Deadline", "time": away_fwd, "all_day": False})
                events.append({"label": "Match Day", "name": f"{away_m_name} (A) - Match Date", "time": away_m_date, "all_day": False})

                for i, ev in enumerate(events):
                    if ev["time"] and ev["time"] != "TBA":
                        dt_obj = parse_to_datetime(ev["time"])
                        gcal_dates = format_gcal_date(dt_obj, is_all_day=ev["all_day"])
                        if gcal_dates:
                            encoded_name = urllib.parse.quote(ev["name"])
                            gcal_url = f"https://calendar.google.com/calendar/render?action=TEMPLATE&text={encoded_name}&dates={gcal_dates}"
                            st.link_button(f"📅 Add **{ev['label']}** ({ev['time']})", gcal_url, use_container_width=True)
                        else:
                            st.warning(f"⚠️ Unable to parse format for: {ev['label']}")
                    else:
                        st.button(f"⚪ {ev['label']} (TBA)", disabled=True, use_container_width=True, key=f"tba_btn_away_{i}_{ev['label']}")
