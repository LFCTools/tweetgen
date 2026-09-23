import streamlit as st
import google.generativeai as genai
import requests
from bs4 import BeautifulSoup
import urllib.parse
from datetime import datetime, timedelta
import re
import json

# --- ROBUST HELPER FUNCTIONS FOR CALENDAR & TIME ---
def parse_to_datetime(date_str):
    if not date_str or date_str == "TBA" or "[" in date_str:
        return None
    try:
        clean = date_str.replace(',', '').strip()
        clean = re.sub(r'(\d{1,2})\.(\d{2})(am|pm)', r'\1:\2\3', clean, flags=re.IGNORECASE)
        for w in ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']:
            clean = clean.replace(w, '').replace(w.capitalize(), '').strip()
        
        parts = clean.split()
        if len(parts) < 2: return None
            
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

def parse_accordion_html_blocks(soup):
    sales_data = []
    sections = soup.find_all('section', {'data-testid': 'ticketing-accordion-list-item'})
    
    for sec in sections:
        title_el = sec.find('span', {'data-testid': 'ticketing-accordion-list-item__title'})
        assistive_el = sec.find('span', {'data-testid': 'ticketing-accordion-list-item__assistive-text'})
        sale_date_el = sec.find('time', {'data-testid': 'ticketing-accordion-list-item__sale-date'})
        body_div = sec.find('div', {'data-testid': 'ticketing-accordion-list-item__body'})
        
        tier_title = title_el.get_text(strip=True) if title_el else ""
        tier_assistive = assistive_el.get_text(strip=True) if assistive_el else ""
        tier_full = f"{tier_title} {tier_assistive}".strip()
        
        open_time = sale_date_el.get_text(strip=True) if sale_date_el else "TBA"
        close_time = "TBA"
        info = ""
        forwarding_deadline = "TBA"
        
        if body_div:
            body_text = body_div.get_text(separator=' ', strip=True)
            
            # Parse close time from 'until [close time]'
            match_until = re.search(r'until\s+(.*?)(?:\.|$)', body_text, re.IGNORECASE)
            if match_until:
                close_time = match_until.group(1).strip()
                
            # Parse Guarantee status
            if 'guaranteed' in body_text.lower():
                info = "Guaranteed Sale"
            elif 'subject to availability' in body_text.lower():
                info = "Subject to availability"
                
            # Parse Forwarding Deadline
            match_fwd = re.search(r'Forwarding Deadline.*?is\s+(.*?)(?:\.|$)', body_text, re.IGNORECASE)
            if match_fwd:
                forwarding_deadline = match_fwd.group(1).strip()
                
        sales_data.append({
            "tier": tier_full if tier_full else "Members Sale",
            "open": open_time,
            "close": close_time,
            "info": info,
            "forwarding_deadline": forwarding_deadline
        })
    return sales_data

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
                        headers = {'User-Agent': 'Mozilla/5.0'}
                        response = requests.get(pl_url, headers=headers, timeout=5)
                        soup = BeautifulSoup(response.text, 'html.parser')
                        for script in soup(["script", "style", "nav", "footer", "header"]):
                            script.extract()
                        page_text = " ".join(soup.get_text().split())[:20000]

                        genai.configure(api_key=api_key)
                        model = genai.GenerativeModel('gemini-2.5-flash')
                        prompt = f"""
                        Analyze the following raw text from an LFC Premier League ticket page. Look for unique link text (e.g. 'sent a unique link on Friday 2 October').
                        Desired JSON Format:
                        {{
                          "match_name": "Fulham",
                          "reg_open": "Day Date, Time",
                          "reg_close": "Day Date, Time",
                          "links_sent": "Day Date, Time",
                          "sales": [{{"tier": "All Members", "open": "Day Date, Time", "close": "Day Date, Time"}}],
                          "ballot_open": "Day Date, Time",
                          "ballot_close": "Day Date, Time",
                          "ballot_results": "TBA",
                          "match_date": "Day Date, Time"
                        }}
                        Source Text: {page_text}
                        """
                        ai_response = model.generate_content(prompt)
                        json_text = ai_response.text.strip().replace("```json", "").replace("```", "")
                        st.session_state.pl_data = json.loads(json_text)
                        st.toast("✅ Data successfully extracted!")
                    except Exception as e:
                        st.error(f"Failed to automatically pull details: {e}")

    if st.session_state.pl_data:
        st.write("")
        d = st.session_state.pl_data
        pl_m_name = st.text_input("Opponent Team Name", value=d.get("match_name", ""), key="pl_m_name")
        pl_r_open = editable_date_row("Registration Opens", d.get("reg_open", ""), "pl_r_open")
        pl_r_close = editable_date_row("Registration Closes", d.get("reg_close", ""), "pl_r_close")
        pl_l_sent = editable_date_row("Links Sent", d.get("links_sent", ""), "pl_l_sent")
        
        edited_pl_sales = []
        for i, sale in enumerate(d.get("sales", [])):
            with st.expander(f"Sale Tier {i+1} ({sale.get('tier', 'Unknown')})", expanded=True):
                t_name = st.text_input(f"Criteria", value=sale.get("tier", ""), key=f"pl_tier_name_{i}")
                t_open = editable_date_row("Sale Opens", sale.get("open", ""), f"pl_tier_open_{i}")
                t_close = editable_date_row("Closes", sale.get("close", "TBA"), f"pl_tier_close_{i}")
                edited_pl_sales.append({"tier": t_name, "open": t_open, "close": t_close})

        pl_b_open = editable_date_row("Ballots Open", d.get("ballot_open", ""), "pl_b_open")
        pl_b_close = editable_date_row("Ballots Close", d.get("ballot_close", ""), "pl_b_close")
        pl_b_res = editable_date_row("Ballots Results", d.get("ballot_results", "TBA"), "pl_b_res")
        pl_m_date = editable_date_row("Match Date & Time", d.get("match_date", ""), "pl_m_date")

        if st.button("Generate PL Tweet Timelines & Calendars 🚀", type="primary", use_container_width=True, key="pl_gen"):
            sales_text_announcement = "".join([f"• {'Sale (4+ Only)' if '4+' in s['tier'] else s['tier']}: {s['open']}\n" for s in edited_pl_sales])
            announcement_tweet = f"""{pl_m_name} (H) - Sale Details 📢\n\nRegistration (All Members) 📝\n• Opens: {pl_r_open}\n• Closes: {pl_r_close}\n• Sale links sent: {pl_l_sent}\n\nSales 🎟️\n{sales_text_announcement}\nLocal & YA Ballots 🗳️\n• Opens: {pl_b_open}\n• Closes: {pl_b_close}\n• Results: {pl_b_res}\n\nMatch Date • {pl_m_date} 🏟️"""
            
            reg_open_tweet = f"""{pl_m_name} (H) - Registration 📢\n\nRegistration (All Members) 📝\n• Opens: Now\n• Closes: {pl_r_close}\n\n• Sale links sent: {pl_l_sent}\n\nSale 🎟️\n• {edited_pl_sales[0]['open'] if edited_pl_sales else 'TBA'}"""
            reg_close_time_str = pl_r_close.split(', ')[-1] if ',' in pl_r_close else pl_r_close
            reg_close_tweet = f"""{pl_m_name} (H) - Registration 📢\n\nRegistration (All Members) 📝\n• Opens: Now\n• Closes: Today {reg_close_time_str}\n\n• Sale links sent: {pl_l_sent}\n\nSale 🎟️\n• {edited_pl_sales[0]['open'] if edited_pl_sales else 'TBA'}"""
            
            ballots_open_tweet = f"""{pl_m_name} (H) - Ballots 📢\n\nLocal Ballots & YA Ballot 🗳️\n• Opens: Now\n• Closes: {pl_b_close}\n\n• Results: {pl_b_res}\n\nhttps://ticketing.liverpoolfc.com/tickets/ballots"""
            ballots_close_time_str = pl_b_close.split(', ')[-1] if ',' in pl_b_close else pl_b_close
            ballots_close_tweet = f"""{pl_m_name} (H) - Ballots 📢\n\nLocal Ballots & YA Ballot 🗳️\n• Opens: Now\n• Closes: Today {ballots_close_time_str}\n\n• Results: {pl_b_res}\n\nhttps://ticketing.liverpoolfc.com/tickets/ballots"""
            ballots_res_tweet = f"""{pl_m_name} (H) - Local & YA Ballots 📢\n\nLocal & YA Ballot Results 🗳️\n• Results today\n• Ensure you have funds in your bank \n\nComment below if successful 👇"""

            tweets_timeline = [
                ("📢 Sales Detail Announcement", "Immediate", announcement_tweet),
                ("📝 Registration Opening Notice", pl_r_open, reg_open_tweet),
                ("⏰ Registration Closing Notice", pl_r_close, reg_close_tweet),
                ("🗳️ Ballots Opening", pl_b_open, ballots_open_tweet),
                ("⏰ Ballots Closing", pl_b_close, ballots_close_tweet),
                ("✨ Local & YA Ballot Results", pl_b_res, ballots_res_tweet)
            ]
            hallmap_url = get_hallmap_link(pl_m_name)
            for s in edited_pl_sales:
                rem_time = get_offset_time(s['open'], hours_before=1)
                link_time = get_offset_time(s['open'], hours_before=0.5)
                tier_display = "Sale (4+ Only)" if "4+" in s['tier'] else f"{s['tier']} Sale"
                rem_tweet = f"""{pl_m_name} (H) - {tier_display} 📢\n\n{tier_display} 🎟️\n• Opens: {s['open']}\n• Click unique links from {link_time}\n\nHallmap link  👇\n{hallmap_url}"""
                tweets_timeline.append((f"🎟️ Sale Reminder ({s['tier']})", rem_time, rem_tweet))

            for title, post_time, content in tweets_timeline:
                with st.expander(f"{title} — *Scheduled: {post_time}*"):
                    st.code(content, language="text")
                    st.link_button(f"🌐 Post on X via Browser", get_x_intent_url(content), use_container_width=True)


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
                with st.spinner("Analyzing Cup page..."):
                    try:
                        headers = {'User-Agent': 'Mozilla/5.0'}
                        response = requests.get(cup_url, headers=headers, timeout=5)
                        soup = BeautifulSoup(response.text, 'html.parser')
                        for script in soup(["script", "style", "nav", "footer", "header"]):
                            script.extract()
                        page_text = " ".join(soup.get_text().split())[:20000]
                        genai.configure(api_key=api_key)
                        model = genai.GenerativeModel('gemini-2.5-flash')
                        prompt = f"Extract LFC Cup ticket details into JSON format with match_name, sales array (tier, open, close), ballot_open, ballot_close, ballot_results, acs_start, acs_end, match_date. Source: {page_text}"
                        res = model.generate_content(prompt)
                        st.session_state.cup_data = json.loads(res.text.strip().replace("```json", "").replace("```", ""))
                        st.toast("✅ Cup data extracted!")
                    except Exception as e:
                        st.error(f"Error: {e}")
    if st.session_state.cup_data:
        st.write("Cup data loaded successfully.")


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
                with st.spinner("Analyzing Away page..."):
                    try:
                        headers = {'User-Agent': 'Mozilla/5.0'}
                        response = requests.get(away_url, headers=headers, timeout=5)
                        soup = BeautifulSoup(response.text, 'html.parser')
                        for script in soup(["script", "style", "nav", "footer", "header"]):
                            script.extract()
                        page_text = " ".join(soup.get_text().split())[:20000]
                        genai.configure(api_key=api_key)
                        model = genai.GenerativeModel('gemini-2.5-flash')
                        prompt = f"Extract LFC Away ticket details into JSON with match_name, sales array (tier, open, close), forwarding_deadline, match_date. Source: {page_text}"
                        res = model.generate_content(prompt)
                        st.session_state.away_data = json.loads(res.text.strip().replace("```json", "").replace("```", ""))
                        st.toast("✅ Away data extracted!")
                    except Exception as e:
                        st.error(f"Error: {e}")
    if st.session_state.away_data:
        st.write("League Away data loaded successfully.")


# ==========================================
# TAB 4: CHAMPIONS LEAGUE AWAYS (Direct HTML Accordion Parser)
# ==========================================
with tab4:
    with st.container(border=True):
        cl_url = st.text_input("🔗 Ticket Page URL (CL Away):", placeholder="https://www.liverpoolfc.com/tickets/...", key="cl_url")
        if st.button("Scan CL Away Page 🔍", use_container_width=True, key="cl_scan"):
            if not cl_url:
                st.error("Please provide the URL.")
            else:
                with st.spinner("Analyzing CL Away accordion blocks..."):
                    try:
                        headers = {'User-Agent': 'Mozilla/5.0'}
                        response = requests.get(cl_url, headers=headers, timeout=5)
                        soup = BeautifulSoup(response.text, 'html.parser')
                        
                        # Directly extract using our custom accordion parser function
                        extracted_sales = parse_accordion_html_blocks(soup)
                        
                        # Extract opponent name via Gemini
                        for script in soup(["script", "style", "nav", "footer", "header"]):
                            script.extract()
                        page_text = " ".join(soup.get_text().split())[:10000]

                        genai.configure(api_key=api_key)
                        model = genai.GenerativeModel('gemini-2.5-flash')
                        match_res = model.generate_content(f"Extract only the opponent team name (e.g., LASK) from this text: {page_text[:3000]}")
                        match_name = match_res.text.strip().replace("`", "")

                        st.session_state.cl_away_data = {
                            "match_name": match_name,
                            "sales": extracted_sales,
                            "match_date": "TBA"
                        }
                        st.toast("✅ CL Away accordion blocks parsed successfully!")
                    except Exception as e:
                        st.error(f"Failed to automatically pull details: {e}")

    if st.session_state.cl_away_data:
        st.write("")
        cld = st.session_state.cl_away_data
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
        if st.button("Generate CL Away Tweet Timelines & Calendars 🚀", type="primary", use_container_width=Key, key="cl_gen"):
            sales_text_announcement = "".join([f"• {s['tier']}: {s['open']}\n" for s in edited_cl_sales])
            announcement_tweet = f"""{cl_m_name} (A) - Sale Details 📢\n\nSales 🎟️\n{sales_text_announcement}\nMatch Date • {cl_m_date} 🏟️"""

            cl_tweets_timeline = [
                ("📢 CL Away Sales Announcement", "Immediate", announcement_tweet)
            ]

            for s in edited_cl_sales:
                info_text = s['info'].strip()
                info_line = f"• {info_text}\n" if info_text else ""
                sale_tweet = f"""{cl_m_name} (A) 🎟️\n\nSale ({s['tier']})\n• Opens: {s['open']}\n• Closes: {s['close']}\n{info_line}Forwarding Deadline ➡️\n• Closes: {s['forwarding_deadline']}"""
                cl_tweets_timeline.append((f"🎟️ Sale ({s['tier']})", s['open'], sale_tweet))

            with st.container(border=True):
                st.markdown("### 🐦 Scheduled CL Away Tweet Timeline")
                for title, post_time, content in cl_tweets_timeline:
                    with st.expander(f"{title} — *Scheduled: {post_time}*"):
                        st.code(content, language="text")
                        st.link_button(f"🌐 Post on X via Browser", get_x_intent_url(content), use_container_width=True)
                    
