import streamlit as st
import google.generativeai as genai
import requests
from bs4 import BeautifulSoup
import urllib.parse
from datetime import datetime, timedelta

# --- HELPER FUNCTIONS FOR CALENDAR LINKS ---
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
        end_iso = (dt + timedelta(hours=1)).strftime("%Y%m%dT%H%M%S")
        return f"{start_iso}/{end_iso}"

# --- CUSTOM UI WIDGET ---
def datetime_editor(label, dt_str, key):
    dt_obj = parse_to_datetime(dt_str)
    is_tba_init = not bool(dt_obj) or dt_str == "TBA"
    
    default_date = dt_obj.date() if dt_obj else datetime.today().date()
    default_time = dt_obj.time() if dt_obj else datetime.strptime("10:00am", "%I:%M%p").time()
    
    is_allday_init = False
    if dt_str and dt_str != "TBA" and not any(m in dt_str.lower() for m in ['am','pm',':']):
        is_allday_init = True
        
    st.markdown(f"**{label}**")
    col1, col2, col3, col4 = st.columns([2, 1.5, 1, 1])
    
    with col4:
        st.write("") # vertical alignment
        is_tba = st.checkbox("TBA", value=is_tba_init, key=f"{key}_tba")
    with col3:
        st.write("") # vertical alignment
        all_day = st.checkbox("All Day", value=is_allday_init, key=f"{key}_allday", disabled=is_tba)
    with col1:
        d = st.date_input("Date", value=default_date, key=f"{key}_date", label_visibility="collapsed", disabled=is_tba)
    with col2:
        t = st.time_input("Time", value=default_time, key=f"{key}_time", label_visibility="collapsed", disabled=is_tba or all_day)
        
    st.write("") # Spacing after each row
    
    if is_tba: return "TBA"
    if all_day: return d.strftime("%a %d %b")
    
    time_formatted = t.strftime("%I:%M%p").lstrip("0").lower()
    return f"{d.strftime('%a %d %b')}, {time_formatted}"

# --- STREAMLIT CONFIG & STATE ---
st.set_page_config(page_title="LFC Alerts", page_icon="🔴", layout="centered")

if "pl_data" not in st.session_state:
    st.session_state.pl_data = None
if "cup_data" not in st.session_state:
    st.session_state.cup_data = None

# --- SIDEBAR ---
with st.sidebar:
    st.image("https://upload.wikimedia.org/wikipedia/en/thumb/0/0c/Liverpool_FC.svg/1200px-Liverpool_FC.svg.png", width=80)
    st.title("Settings")
    api_key_input = st.text_input("Gemini API Key:", type="password", help="Required to parse the LFC website text.")
    st.divider()
    st.caption("Developed for LFC Ticket Alerts. Generates perfectly formatted tweets and Google Calendar schedule links.")

# --- MAIN HEADER ---
st.title("🔴 LFC Ticket Alerts")
st.markdown("Automate your Twitter sale alerts and calendar schedules instantly.")
st.write("")

# --- TABS ---
tab1, tab2 = st.tabs(["🏆 Premier League (Home)", "🏅 Cup Games (Home)"])

# ==========================================
# TAB 1: PREMIER LEAGUE HOME GAMES
# ==========================================
with tab1:
    with st.container(border=True):
        pl_url = st.text_input("🔗 Ticket Page URL (PL):", placeholder="https://www.liverpoolfc.com/tickets/...", key="pl_url")
        if st.button("Scan PL Page 🔍", use_container_width=True, key="pl_scan"):
            if not pl_url or not api_key_input:
                st.error("Please provide both the URL and your Gemini API Key in the sidebar.")
            else:
                with st.spinner("Analyzing ticketing page..."):
                    try:
                        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
                        response = requests.get(pl_url, headers=headers, timeout=5)
                        soup = BeautifulSoup(response.text, 'html.parser')
                        for script in soup(["script", "style", "nav", "footer", "header"]):
                            script.extract()
                        page_text = " ".join(soup.get_text().split())[:3000]

                        genai.configure(api_key=api_key_input)
                        model = genai.GenerativeModel('gemini-2.5-flash')
                        
                        prompt = f"""
                        Analyze the following raw text from an LFC ticket page. Extract the exact names and dates.
                        Respond ONLY with a valid raw JSON object matching these exact keys. 
                        Use abbreviated days (e.g., Wed) and months (e.g., Nov) and format times like 10:00am or 11:00am.
                        If any field is missing or not declared yet, make its value "TBA".
                        
                        Desired JSON Format:
                        {{
                          "match_name": "Only the opponent team name (e.g., Sunderland)",
                          "criteria": "All Members or 4+ Members",
                          "reg_open": "Day Date, Time",
                          "reg_close": "Day Date, Time",
                          "links_sent": "Day Date",
                          "ballot_open": "Day Date, Time",
                          "ballot_close": "Day Date, Time",
                          "ballot_results": "TBA or Day Date",
                          "ticket_sale": "Day Date, Time",
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
            col1, col2 = st.columns(2)
            with col1:
                pl_m_name = st.text_input("Opponent Team Name", value=d.get("match_name", ""), key="pl_m_name")
            with col2:
                pl_crit = st.selectbox("Sale Criteria", ["All Members", "4+ Members"], index=0 if d.get("criteria") == "All Members" else 1, key="pl_crit")

        with st.container(border=True):
            st.markdown("#### 📝 Registration & Sale")
            pl_r_open = datetime_editor("Registration Opens", d.get("reg_open", ""), "pl_r_open")
            pl_r_close = datetime_editor("Registration Closes", d.get("reg_close", ""), "pl_r_close")
            pl_l_sent = datetime_editor("Links Sent", d.get("links_sent", ""), "pl_l_sent")
            pl_t_sale = datetime_editor("Ticket Sale Opens", d.get("ticket_sale", ""), "pl_t_sale")

        with st.container(border=True):
            st.markdown("#### 🗳️ Local Ballot")
            pl_b_open = datetime_editor("Local Ballot Opens", d.get("ballot_open", ""), "pl_b_open")
            pl_b_close = datetime_editor("Local Ballot Closes", d.get("ballot_close", ""), "pl_b_close")
            pl_b_res = datetime_editor("Local Ballot Results", d.get("ballot_results", "TBA"), "pl_b_res")

        with st.container(border=True):
            st.markdown("#### 🏟️ Match Details")
            pl_m_date = datetime_editor("Match Date & Time", d.get("match_date", ""), "pl_m_date")

        st.write("")
        if st.button("Generate PL Alerts 🚀", type="primary", use_container_width=True, key="pl_gen"):
            sale_label = "Sale (4+ Members)" if pl_crit == "4+ Members" else "Sale"
            
            tweet_output = f"""{pl_m_name} (H) - Sale Details 📢\n
Registration ({pl_crit}) 📝
• Opens: {pl_r_open}
• Closes: {pl_r_close}
• Links sent: {pl_l_sent}\n
{sale_label} • {pl_t_sale} 🎟️\n
Local & YA Ballot 🗳️
• Opens: {pl_b_open}
• Closes: {pl_b_close}
• Results: {pl_b_res}\n
Match Date • {pl_m_date} 🏟️"""

            st.write("")
            with st.container(border=True):
                st.markdown("### 🐦 Generated Tweet")
                st.code(tweet_output, language="text")

            with st.container(border=True):
                st.markdown("### 📅 Calendar Schedule Links")
                
                events = [
                    {"label": "Registration Open", "name": f"{pl_m_name} (H) - Registration Opens", "time": pl_r_open, "all_day": False},
                    {"label": "Registration Closes", "name": f"{pl_m_name} (H) - Registration Closes", "time": pl_r_close, "all_day": False},
                    {"label": "Unique Links Sent", "name": f"{pl_m_name} (H) - Unique Links Sent", "time": f"{pl_l_sent} 09:00am" if pl_l_sent != "TBA" else "TBA", "all_day": True},
                    {"label": "Local Ballot Open", "name": f"{pl_m_name} (H) - Local Ballot Opens", "time": pl_b_open, "all_day": False},
                    {"label": "Local Ballot Closes", "name": f"{pl_m_name} (H) - Local Ballot Closes", "time": pl_b_close, "all_day": False},
                    {"label": "Local Ballot Results", "name": f"{pl_m_name} (H) - Local Ballot Results", "time": f"{pl_b_res} 09:00am" if pl_b_res != "TBA" else "TBA", "all_day": True},
                    {"label": "Ticket Sale Opens", "name": f"{pl_m_name} (H) - Ticket Sale", "time": pl_t_sale, "all_day": False},
                    {"label": "Match Day", "name": f"{pl_m_name} (H) - Match Date", "time": pl_m_date, "all_day": False}
                ]

                for i, ev in enumerate(events):
                    if ev["time"] and ev["time"] != "TBA":
                        dt_obj = parse_to_datetime(ev["time"])
                        gcal_dates = format_gcal_date(dt_obj, is_all_day=ev["all_day"])
                        if gcal_dates:
                            encoded_name = urllib.parse.quote(ev["name"])
                            gcal_url = f"https://calendar.google.com/calendar/render?action=TEMPLATE&text={encoded_name}&dates={gcal_dates}"
                            
                            display_time = ev['time'].replace(' 09:00am', '')
                            st.link_button(f"📅 Add **{ev['label']}** ({display_time})", gcal_url, use_container_width=True)
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
            if not cup_url or not api_key_input:
                st.error("Please provide both the URL and your Gemini API Key in the sidebar.")
            else:
                with st.spinner("Analyzing Cup ticketing page..."):
                    try:
                        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
                        response = requests.get(cup_url, headers=headers, timeout=5)
                        soup = BeautifulSoup(response.text, 'html.parser')
                        for script in soup(["script", "style", "nav", "footer", "header"]):
                            script.extract()
                        page_text = " ".join(soup.get_text().split())[:3500] 

                        genai.configure(api_key=api_key_input)
                        model = genai.GenerativeModel('gemini-2.5-flash')
                        
                        prompt = f"""
                        Analyze the following raw text from an LFC Cup match ticket page. Extract the exact names and dates.
                        Respond ONLY with a valid raw JSON object matching the structure below. 
                        Use abbreviated days (e.g., Wed) and months (e.g., Nov) and format times like 10:00am or 11:00am.
                        If any field is missing, make its value "TBA".
                        For 'sales', create an array of objects for each credit/game tier you find. Ensure 'tier' reflects the requirement (e.g., "5+ Games", "4+ Credit balance").
                        
                        Desired JSON Format:
                        {{
                          "match_name": "Only the opponent team name",
                          "sales": [
                            {{"tier": "5+ Games", "open": "Day Date, Time", "close": "Day Date, Time"}},
                            {{"tier": "4+ Games", "open": "Day Date, Time", "close": "Day Date, Time"}}
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
                    st.write("")
                    t_open = datetime_editor("Opens", sale.get("open", ""), f"tier_open_{i}")
                    t_close = datetime_editor("Closes", sale.get("close", ""), f"tier_close_{i}")
                    edited_sales.append({"tier": t_name, "open": t_open, "close": t_close})

        with st.container(border=True):
            st.markdown("#### 🗳️ Local Ballot")
            cup_b_open = datetime_editor("Local Ballot Opens", cd.get("ballot_open", ""), "cup_b_open")
            cup_b_close = datetime_editor("Local Ballot Closes", cd.get("ballot_close", ""), "cup_b_close")
            cup_b_res = datetime_editor("Local Ballot Results", cd.get("ballot_results", "TBA"), "cup_b_res")

        with st.container(border=True):
            st.markdown("#### 💰 Auto Cup Scheme & Match Details")
            cup_acs_start = datetime_editor("ACS Payment Start", cd.get("acs_start", ""), "cup_acs_start")
            cup_acs_end = datetime_editor("ACS Payment End", cd.get("acs_end", ""), "cup_acs_end")
            cup_m_date = datetime_editor("Match Date & Time", cd.get("match_date", ""), "cup_m_date")

        st.write("")
        if st.button("Generate Cup Alerts 🚀", type="primary", use_container_width=True, key="cup_gen"):
            
            sales_formatted_text = ""
            for s in edited_sales:
                sales_formatted_text += f"Sale ({s['tier']})\n• Opens: {s['open']}\n• Closes: {s['close']}\n\n"

            tweet_output = f"""{cup_m_name} (H) - Sale Details 📢\n\n{sales_formatted_text}Local Ballot 🗳️
• Opens: {cup_b_open}
• Closes: {cup_b_close}
• Results: {cup_b_res}\n
ACS Payment Run 💰
• {cup_acs_start} - {cup_acs_end}\n
Match Date • {cup_m_date} 🏟️"""

            with st.container(border=True):
                st.markdown("### 🐦 Generated Cup Tweet")
                st.code(tweet_output.strip(), language="text")

            with st.container(border=True):
                st.markdown("### 📅 Calendar Schedule Links")

                events = [
                    {"label": "ACS Payment Run", "name": f"{cup_m_name} (H) - ACS Payment Run", "time": f"{cup_acs_start} 09:00am" if cup_acs_start != "TBA" else "TBA", "all_day": True},
                    {"label": "Local Ballot Open", "name": f"{cup_m_name} (H) - Local Ballot Opens", "time": cup_b_open, "all_day": False},
                    {"label": "Local Ballot Closes", "name": f"{cup_m_name} (H) - Local Ballot Closes", "time": cup_b_close, "all_day": False},
                    {"label": "Local Ballot Results", "name": f"{cup_m_name} (H) - Local Ballot Results", "time": f"{cup_b_res} 09:00am" if cup_b_res != "TBA" else "TBA", "all_day": True}
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
                            
                            display_time = ev['time'].replace(' 09:00am', '')
                            st.link_button(f"📅 Add **{ev['label']}** ({display_time})", gcal_url, use_container_width=True)
                        else:
                            st.warning(f"⚠️ Unable to parse format for: {ev['label']}")
                    else:
                        st.button(f"⚪ {ev['label']} (TBA)", disabled=True, use_container_width=True, key=f"tba_btn_cup_{i}_{ev['label']}")
