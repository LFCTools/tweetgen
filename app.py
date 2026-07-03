import streamlit as st
import google.generativeai as genai
import requests
from bs4 import BeautifulSoup
import urllib.parse
import re
from datetime import datetime, timedelta

# --- HELPER FUNCTIONS FOR CALENDAR LINKS ---
def parse_to_datetime(date_str):
    """Converts a string like 'Wed 19 Nov, 10:00am' into a standard Python datetime object."""
    if not date_str or date_str == "TBA" or "[" in date_str:
        return None
    try:
        current_year = 2026
        clean_str = date_str.replace(',', '')
        parts = clean_str.split() 
        
        if len(parts) < 3: return None
            
        day = int(parts[1])
        month_str = parts[2][:3]
        
        if len(parts) < 4:
            # All Day parsing default time
            dt_str = f"{day} {month_str} {current_year} 09:00am"
        else:
            time_str = parts[3].lower()
            dt_str = f"{day} {month_str} {current_year} {time_str}"
            
        return datetime.strptime(dt_str, "%d %b %Y %I:%M%p" if ":" in dt_str.split()[-1] else "%d %b %Y %I%p")
    except Exception:
        return None

def format_gcal_date(dt, is_all_day=False):
    """Formats datetime object into Google Calendar URL DateTime structure."""
    if not dt: return None
    if is_all_day:
        date_only = dt.strftime("%Y%m%d")
        end_date_only = (dt + timedelta(days=1)).strftime("%Y%m%d")
        return f"{date_only}/{end_date_only}"
    else:
        start_iso = dt.strftime("%Y%m%dT%H%M%S")
        end_iso = (dt + timedelta(hours=1)).strftime("%Y%m%dT%H%M%S")
        return f"{start_iso}/{end_iso}"

# --- STREAMLIT CONFIG ---
st.set_page_config(page_title="LFC Tweet Generator", page_icon="⚽")
st.title("⚽ LFC Ticket Tweet & Calendar Generator")

if "extracted_data" not in st.session_state:
    st.session_state.extracted_data = None

url_input = st.text_input("Ticket Page URL:", placeholder="https://www.liverpoolfc.com/tickets/...")
api_key_input = st.text_input("Enter Gemini API Key (Keep it private):", type="password")

if st.button("1. Scan Page & Extract Data 🔍"):
    if not url_input or not api_key_input:
        st.error("Please provide both the URL and your Gemini API Key.")
    else:
        with st.spinner("Scraping LFC and parsing with Gemini..."):
            try:
                headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
                response = requests.get(url_input, headers=headers, timeout=5)
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
                  "match_name": "Opponent Name",
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
                st.session_state.extracted_data = json.loads(json_text)
                st.success("Data extracted! Review and edit it below.")
                
            except requests.exceptions.Timeout:
                st.error("The LFC website took too long to respond.")
            except Exception as e:
                st.error(f"Failed to automatically pull details: {e}.")
                st.session_state.extracted_data = {
                    "match_name": "", "criteria": "All Members", "reg_open": "", "reg_close": "",
                    "links_sent": "", "ballot_open": "", "ballot_close": "", "ballot_results": "TBA",
                    "ticket_sale": "", "match_date": ""
                }

# --- STEP 2: EDITING AND FINAL OUTPUT GENERATION ---
if st.session_state.extracted_data:
    st.divider()
    st.subheader("📝 Verify / Edit Extracted Details")
    
    d = st.session_state.extracted_data
    
    col1, col2 = st.columns(2)
    with col1:
        m_name = st.text_input("Opponent Team Name", value=d.get("match_name", ""))
        crit = st.selectbox("Registration/Sale Criteria", ["All Members", "4+ Members"], index=0 if d.get("criteria") == "All Members" else 1)
        r_open = st.text_input("Registration Opens", value=d.get("reg_open", ""))
        r_close = st.text_input("Registration Closes", value=d.get("reg_close", ""))
        l_sent = st.text_input("Links Sent", value=d.get("links_sent", ""))
    with col2:
        b_open = st.text_input("Local Ballot Opens", value=d.get("ballot_open", ""))
        b_close = st.text_input("Local Ballot Closes", value=d.get("ballot_close", ""))
        b_res = st.text_input("Local Ballot Results", value=d.get("ballot_results", "TBA"))
        t_sale = st.text_input("Ticket Sale Opens", value=d.get("ticket_sale", ""))
        m_date = st.text_input("Match Date & Time", value=d.get("match_date", ""))

    if st.button("2. Generate Final Tweet & Ordered Calendar Buttons 🚀", type="primary"):
        sale_label = "Sale (4+ Members)" if crit == "4+ Members" else "Sale"
        
        tweet_output = f"""{m_name} (H) - Sale Details 📢\n
Registration ({crit}) 📝
• Opens: {r_open}
• Closes: {r_close}
• Links sent: {l_sent}\n
{sale_label} • {t_sale} 🎟️\n
Local & YA Ballot 🗳️
• Opens: {b_open}
• Closes: {b_close}
• Results: {b_res}\n
Match Date • {m_date} 🏟️"""

        st.subheader("🐦 Your Formatted Tweet")
        st.code(tweet_output, language="text")

        st.divider()
        st.subheader("📅 Schedule Calendar Section")

        # Events structural mapping arrays in exact layout requested
        events = [
            {"label": "1. Registration Open", "name": f"LFC v {m_name} - Registration Opens", "time": r_open, "all_day": False},
            {"label": "2. Registration Closes", "name": f"LFC v {m_name} - Registration Closes", "time": r_close, "all_day": False},
            {"label": "3. Unique Links Sent", "name": f"LFC v {m_name} - Unique Links Sent", "time": l_sent, "all_day": True},
            {"label": "4. Local Ballot Open", "name": f"LFC v {m_name} - Local Ballot Opens", "time": b_open, "all_day": False},
            {"label": "5. Local Ballot Closes", "name": f"LFC v {m_name} - Local Ballot Closes", "time": b_close, "all_day": False},
            {"label": "6. Local Ballot Results", "name": f"LFC v {m_name} - Local Ballot Results", "time": b_res, "all_day": True},
            {"label": "7. Ticket Sale Opens", "name": f"LFC v {m_name} - Ticket Sale", "time": t_sale, "all_day": False},
            {"label": "8. Match Day", "name": f"LFC v {m_name} - Match Date", "time": m_date, "all_day": False}
        ]

        # Generate individual links
        for ev in events:
            if ev["time"] and ev["time"] != "TBA" and not ev["time"].startswith("["):
                dt_obj = parse_to_datetime(ev["time"])
                gcal_dates = format_gcal_date(dt_obj, is_all_day=ev["all_day"])
                if gcal_dates:
                    encoded_name = urllib.parse.quote(ev["name"])
                    gcal_url = f"https://calendar.google.com/calendar/render?action=TEMPLATE&text={encoded_name}&dates={gcal_dates}"
                    st.markdown(f"🔗 **[{ev['label']}]({gcal_url})** — *{ev['time']}*")
                else:
                    st.markdown(f"⚠️ **{ev['label']}** — *Unable to parse layout pattern format for timestamp: '{ev['time']}'*")
            else:
                st.markdown(f"⚪ **{ev['label']}** — *Not announced (TBA)*")
