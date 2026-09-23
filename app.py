import streamlit as st
import google.generativeai as genai
import requests
from bs4 import BeautifulSoup
import urllib.parse
from datetime import datetime, timedelta
import re

# --- ROBUST HELPER FUNCTIONS FOR CALENDAR & TIME ---
def parse_to_datetime(date_str):
    if not date_str or date_str == "TBA" or "[" in date_str:
        return None
    try:
        clean = date_str.replace(',', '').strip()
        # Normalize dot times like 8.15am -> 8:15am
        clean = re.sub(r'(\d{1,2})\.(\d{2})(am|pm)', r'\1:\2\3', clean, flags=re.IGNORECASE)
        # Remove full weekday names if present
        for w in ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']:
            clean = clean.replace(w, '').replace(w.capitalize(), '').strip()
        
        parts = clean.split()
        if len(parts) < 2: return None
            
        # Extract day, month, time
        day = int(parts[0])
        month_str = parts[1][:3]
        
        today = datetime.today()
        current_year = today.year
        month_num = datetime.strptime(month_str, '%b').month
        if today.month >= 7 and month_num < 7:
            calc_year = current_year + 1
        elif today.month < 7 and month_num >= 7:
            calc_year = current_year - 1
        else:
            calc_year = current_year
        
        if len(parts) < 3:
            dt_str = f"{day} {month_str} {calc_year} 09:00am"
        else:
            time_str = parts[2].lower()
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
    hallmap_mapping = {
        "manchester city": "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20manchester%20city/2026-10-10_15.00/anfield?hallmap",
        "brighton": "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20brighton%20-%20hove%20albion/2026-10-24_15.00/anfield?hallmap",
        "arsenal": "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20arsenal/2026-10-31_15.00/anfield?hallmap",
        "manchester united": "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20manchester%20united/2026-11-21_15.00/anfield?hallmap",
        "sunderland": "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20sunderland/2026-12-2_20.00/anfield?hallmap",
        "leeds united": "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20leeds%20united/2026-12-12_15.00/anfield?hallmap",
        "tottenham": "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20tottenham%20hotspur/2026-12-19_15.00/anfield?hallmap",
        "coventry city": "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20coventry%20city/2027-1-2_15.00/anfield?hallmap",
        "crystal palace": "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20crystal%20palace/2027-1-16_15.00/anfield?hallmap",
        "everton": "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20everton/2027-1-30_15.00/anfield?hallmap",
        "hull city": "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20hull%20city/2027-2-20_15.00/anfield?hallmap",
        "aston villa": "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20aston%20villa/2027-3-3_20.00/anfield?hallmap",
        "ipswich town": "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20ipswich%20town/2027-3-13_15.00/anfield?hallmap",
        "newcastle united": "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20newcastle%20united/2027-4-10_15.00/anfield?hallmap",
        "chelsea": "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20chelsea/2027-5-1_15.00/anfield?hallmap",
        "brentford": "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20brentford/2027-5-15_15.00/anfield?hallmap",
        "afc bournemouth": "https://ticketing.liverpoolfc.com/en-GB/events/liverpool%20v%20afc%20bournemouth/2027-5-30_15.00/anfield?hallmap"
    }
    key = opponent_name.strip().lower()
    return hallmap_mapping.get(key, "https://ticketing.liverpoolfc.com/")

# --- MOBILE-OPTIMIZED UI WIDGET ---
def editable_date_row(label, default_val, key):
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

for state_key in ["pl_data", "cup_data", "away_data", "cl_away_data"]:
    if state_key not in st.session_state:
        st.session_state[state_key] = None

# --- MAIN HEADER ---
st.title("🔴 LFC Ticket Alerts & Tweet Scheduler")
st.markdown("Automate your custom sale templates, scheduled tweet timelines, and calendar schedules instantly.")
st.write("")

# --- TABS ---
tab1, tab2, tab3, tab4 = st.tabs(["🏆 Premier League (Home)", "🏅 Cup Games (Home)", "✈️ League Aways", "🇪🇺 CL Aways"])

# ==========================================
# TAB 4: CHAMPIONS LEAGUE AWAYS (Updated & Robust)
# ==========================================
with tab4:
    with st.container(border=True):
        cl_url = st.text_input("🔗 Ticket Page URL (CL Away):", placeholder="https://www.liverpoolfc.com/tickets/...", key="cl_url")

        if st.button("Scan CL Away Page 🔍", use_container_width=True, key="cl_scan"):
            if not cl_url:
                st.error("Please provide the URL.")
            else:
                with st.spinner("Analyzing CL Away ticketing page..."):
                    try:
                        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
                        response = requests.get(cl_url, headers=headers, timeout=5)
                        soup = BeautifulSoup(response.text, 'html.parser')
                        for script in soup(["script", "style", "nav", "footer", "header"]):
                            script.extract()
                            
                        page_text = " ".join(soup.get_text().split())[:20000] 

                        genai.configure(api_key=api_key)
                        model = genai.GenerativeModel('gemini-2.0-flash')
                        
                        prompt = f"""
                        Analyze the following raw text from an LFC Champions League Away match ticket page. 
                        Find the sales eligibility table. Extract:
                        1. Match opponent name (e.g., LASK).
                        2. Each tier row with:
                           - Sale Eligibility / tier name (e.g., Match Credit Balance of 9 or more)
                           - Sale Starts (e.g., 8.15am Wednesday 23 September)
                           - Sale Ends (e.g., 7.30am Thursday 24 September)
                           - Information (e.g., Guaranteed Sale, Non Guaranteed, Subject to availability)
                           - Ticket Forwarding Deadline (e.g., 11am Thursday 24 September)
                        3. Match date and time if present.

                        Respond ONLY with a valid raw JSON object matching the structure below. 
                        If any field is missing, make its value "TBA".
                        
                        Desired JSON Format:
                        {{
                          "match_name": "LASK",
                          "sales": [
                            {{
                              "tier": "Match Credit Balance of 9 or more",
                              "open": "23 Sep 2026 8:15am",
                              "close": "24 Sep 2026 7:30am",
                              "info": "Guaranteed Sale",
                              "forwarding_deadline": "24 Sep 2026 11:00am"
                            }}
                          ],
                          "match_date": "14 Oct 2026 5:45pm"
                        }}
                        Source Text: {page_text}
                        """
                        ai_response = model.generate_content(prompt)
                        json_text = ai_response.text.strip().replace("```json", "").replace("```", "")
                        import json
                        st.session_state.cl_away_data = json.loads(json_text)
                        st.toast("✅ CL Away data successfully extracted!")
                        
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
                    t_info = st.text_input("Information", value=sale.get("info", ""), key=f"cl_tier_info_{i}")
                    t_fwd = editable_date_row("Forwarding Deadline", sale.get("forwarding_deadline", "TBA"), f"cl_tier_fwd_{i}")
                    edited_cl_sales.append({"tier": t_name, "open": t_open, "close": t_close, "info": t_info, "forwarding_deadline": t_fwd})

        with st.container(border=True):
            st.markdown("#### 🏟️ Match Details")
            cl_m_date = editable_date_row("Match Date & Time", cld.get("match_date", ""), "cl_m_date")

        st.write("")
        if st.button("Generate CL Away Tweet Timelines & Calendars 🚀", type="primary", use_container_width=True, key="cl_gen"):
            
            with st.container(border=True):
                st.markdown("### 🐦 Scheduled CL Away Tweet Timeline")
                
                cl_away_tweets = []
                for s in edited_cl_sales:
                    info_text = s['info'].strip()
                    info_line = f"• {info_text}\n" if info_text and any(k in info_text.lower() for k in ['guaranteed', 'subject']) else ""
                    
                    sale_tweet = f"""{cl_m_name} (A) 🎟️

Sale ({s['tier']})
• Opens: {s['open']}
• Closes: {s['close']}
{info_line}Forwarding Deadline ➡️
• Closes: {s['forwarding_deadline']}"""
                    cl_away_tweets.append((f"🎟️ Sale ({s['tier']})", s['open'], sale_tweet))

                for title, post_time, content in cl_away_tweets:
                    with st.expander(f"{title} — *Scheduled: {post_time}*"):
                        st.code(content, language="text")
                        x_url = get_x_intent_url(content)
                        st.link_button(f"🌐 Post on X via Browser ({title})", x_url, use_container_width=True)

            with st.container(border=True):
                st.markdown("### 📅 Calendar Schedule Links")
                events = []
                for s in edited_cl_sales:
                    events.append({"label": f"Sale Open ({s['tier']})", "name": f"{cl_m_name} (A) - Sale Opens ({s['tier']})", "time": s['open'], "all_day": False})
                    events.append({"label": f"Sale Close ({s['tier']})", "name": f"{cl_m_name} (A) - Sale Closes ({s['tier']})", "time": s['close'], "all_day": False})
                    if s['forwarding_deadline'] != "TBA":
                        events.append({"label": f"Forwarding Deadline ({s['tier']})", "name": f"{cl_m_name} (A) - Forwarding Deadline ({s['tier']})", "time": s['forwarding_deadline'], "all_day": False})
                events.append({"label": "Match Day", "name": f"{cl_m_name} (A) - Match Date", "time": cl_m_date, "all_day": False})

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
                        st.button(f"⚪ {ev['label']} (TBA)", disabled=True, use_container_width=True, key=f"tba_btn_cl_{i}_{ev['label']}")
