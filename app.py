import streamlit as st
import google.generativeai as genai
import requests
from bs4 import BeautifulSoup
import urllib.parse
import re
from datetime import datetime, timedelta

# --- HELPER FUNCTIONS FOR CALENDAR LINKS ---
def extract_from_tweet(tweet):
    """Parses the Gemini-generated tweet to extract dates cleanly."""
    data = {
        "match_name": "LFC Match", "reg_open": "TBA", "reg_close": "TBA",
        "links_sent": "TBA", "sale_date": "TBA", "ballot_open": "TBA", "ballot_close": "TBA"
    }
    try:
        # Extract Match Name
        m_name = re.search(r'^(.*?)\s*\(H\)', tweet)
        if m_name: data["match_name"] = m_name.group(1).strip()
        
        # Split tweet into sections for accurate extraction
        blocks = tweet.split('\n\n')
        for block in blocks:
            if 'Registration' in block:
                o = re.search(r'Opens:\s*(.*)', block)
                if o: data["reg_open"] = o.group(1).strip()
                c = re.search(r'Closes:\s*(.*)', block)
                if c: data["reg_close"] = c.group(1).strip()
                l = re.search(r'Links sent:\s*(.*)', block)
                if l: data["links_sent"] = l.group(1).strip()
            elif 'Sale' in block and '🎟️' in block:
                s = re.search(r'Sale.*?•\s*(.*?)\s*🎟️', block)
                if s: data["sale_date"] = s.group(1).strip()
            elif 'Ballot' in block:
                o = re.search(r'Opens:\s*(.*)', block)
                if o: data["ballot_open"] = o.group(1).strip()
                c = re.search(r'Closes:\s*(.*)', block)
                if c: data["ballot_close"] = c.group(1).strip()
    except Exception as e:
        pass # Fallback to TBA if regex fails
    return data

def format_gcal_date(date_str, is_all_day=False):
    """Formats 'Wed 19 Nov, 10:00am' into Google Calendar URL DateTime structure."""
    if not date_str or date_str == "TBA" or "[" in date_str:
        return None
        
    try:
        current_year = 2026
        clean_str = date_str.replace(',', '')
        parts = clean_str.split() 
        
        if len(parts) < 3: return None
            
        day = int(parts[1])
        month_str = parts[2][:3]
        
        if is_all_day or len(parts) < 4:
            # All Day Event Format: YYYYMMDD/YYYYMMDD (End date is exclusive)
            dt_str = f"{day} {month_str} {current_year}"
            dt = datetime.strptime(dt_str, "%d %b %Y")
            date_only = dt.strftime("%Y%m%d")
            end_dt = dt + timedelta(days=1)
            end_date_only = end_dt.strftime("%Y%m%d")
            return f"{date_only}/{end_date_only}"
        else:
            # Specific Time Format: YYYYMMDDTHHMMSS/YYYYMMDDTHHMMSS
            time_str = parts[3].lower()
            dt_str = f"{day} {month_str} {current_year} {time_str}"
            dt = datetime.strptime(dt_str, "%d %b %Y %I:%M%p" if ":" in time_str else "%d %b %Y %I%p")
            
            start_iso = dt.strftime("%Y%m%dT%H%M%S")
            end_dt = dt + timedelta(hours=1) # Default 1-hour block
            end_iso = end_dt.strftime("%Y%m%dT%H%M%S")
            # Omitting the 'Z' timezone marker forces it to adapt to the user's local UK timezone
            return f"{start_iso}/{end_iso}"
            
    except Exception as e:
        return None
# ------------------------------------------

# App Title & UI Layout
st.set_page_config(page_title="LFC Tweet Generator", page_icon="⚽")
st.title("⚽ LFC Ticket Tweet Generator")
st.write("Paste the Liverpool FC ticket page URL below to generate your formatted tweet.")

# Input fields
url_input = st.text_input("Ticket Page URL:", placeholder="https://www.liverpoolfc.com/tickets/...")
api_key_input = st.text_input("Enter Gemini API Key (Keep it private):", type="password")

if st.button("Generate Tweet & Calendar Alerts ✨"):
    if not url_input or not api_key_input:
        st.error("Please provide both the URL and your Gemini API Key.")
    else:
        with st.spinner("Processing..."):
            try:
                # 1. Optimized Web Scraper
                headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
                response = requests.get(url_input, headers=headers, timeout=5)
                
                # 2. Extract ONLY raw text and strip out heavy scripts/styles immediately
                soup = BeautifulSoup(response.text, 'html.parser')
                for script in soup(["script", "style", "nav", "footer", "header"]):
                    script.extract()
                
                # Get clean text chunks
                page_text = " ".join(soup.get_text().split())[:3000]

                # 3. Configure Gemini
                genai.configure(api_key=api_key_input)
                model = genai.GenerativeModel('gemini-2.5-flash')
                
                # 4. Construct the prompt
                prompt = f"""
                You are a social media manager. Take this raw website text and format it into a single tweet using the exact template structure below. 

                Rules:
                - ALWAYS use abbreviated days of the week (e.g., Mon, Tues, Wed, Thurs, Fri, Sat, Sun).
                - ALWAYS use abbreviated months (e.g., Jan, Feb, Mar, Apr, May, Jun, Jul, Aug, Sep, Oct, Nov, Dec).
                - Do not add text before or after the template. Only output the final tweet text.

                Template:
                [Opponent Team Name] (H) - Sale Details 📢

                Registration (All Members) 📝
                • Opens: [Day Date, Time]
                • Closes: [Day Date, Time]
                • Links sent: [Day Date]

                Sale • [Day Date, Time] 🎟️

                Local & YA Ballot 🗳️
                • Opens: [Day Date, Time]
                • Closes: [Day Date, Time]
                • Results:

                Match Date • [Day Date, Time] 🏟️

                Source Text to extract from:
                {page_text}
                """

                # 5. Generate text
                ai_response = model.generate_content(prompt)
                tweet = ai_response.text

                # 6. Output Result to User
                st.success("Generated Successfully!")
                st.text_area("Your Tweet:", value=tweet, height=300)
                st.caption("💡 Highlight the text inside the box above to copy it directly!")

                # 7. Generate Calendar Buttons
                st.divider()
                st.subheader("📅 Google Calendar Links")
                
                cal_data = extract_from_tweet(tweet)
                
                events = [
                    {"name": f"LFC v {cal_data['match_name']} - Registration Opens", "time": cal_data['reg_open'], "all_day": False},
                    {"name": f"LFC v {cal_data['match_name']} - Registration Closes", "time": cal_data['reg_close'], "all_day": False},
                    {"name": f"LFC v {cal_data['match_name']} - Unique Link Sent", "time": cal_data['links_sent'], "all_day": True},
                    {"name": f"LFC v {cal_data['match_name']} - Ticket Sale", "time": cal_data['sale_date'], "all_day": False},
                    {"name": f"LFC v {cal_data['match_name']} - Local Ballot Opens", "time": cal_data['ballot_open'], "all_day": False},
                    {"name": f"LFC v {cal_data['match_name']} - Local Ballot Closes", "time": cal_data['ballot_close'], "all_day": False}
                ]

                # Loop and render buttons dynamically
                cols = st.columns(2) # Show buttons cleanly in a grid
                col_index = 0
                
                for ev in events:
                    if ev["time"] != "TBA" and not ev["time"].startswith("["):
                        gcal_dates = format_gcal_date(ev["time"], is_all_day=ev["all_day"])
                        if gcal_dates:
                            encoded_name = urllib.parse.quote(ev["name"])
                            gcal_url = f"https://calendar.google.com/calendar/render?action=TEMPLATE&text={encoded_name}&dates={gcal_dates}"
                            
                            # Render into the columns
                            with cols[col_index % 2]:
                                st.markdown(f"**[{ev['name'].split(' - ')[1]}]({gcal_url})** <br/> *(Starts: {ev['time']})*", unsafe_allow_html=True)
                            col_index += 1

            except requests.exceptions.Timeout:
                st.error("The LFC website took too long to respond. Please try again.")
            except Exception as e:
                st.error(f"An error occurred: {e}")
