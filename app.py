import hashlib
import json
import re

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup
from google import genai
from google.genai import types

from helpers import (
    TBA,
    TWEET_LIMIT,
    buffer_due_at,
    build_ics,
    format_datetime,
    gcal_url,
    get_hallmap_link,
    get_x_intent_url,
    has_time,
    now_uk,
    parse_to_datetime,
    shorten_tier_name,
    strip_json_fences,
    to_display,
    tweet_length,
)
from templates import CLASSIFY_PROMPT, MATCH_TYPES

GEMINI_MODEL = "gemini-2.5-flash"
PAGE_TEXT_LIMIT = 40000
MIN_PAGE_TEXT = 300
AUTO = "auto"


# ==========================================
# SECRETS
# ==========================================
def secret(name):
    try:
        return st.secrets.get(name)
    except Exception:  # no secrets file at all
        return None


# ==========================================
# EXTERNAL SERVICES
# ==========================================
def schedule_to_buffer(text, scheduled_at_local):
    """Queue a post in Buffer. `scheduled_at_local` is a naive UK-time datetime."""
    api_key, channel_id = secret("BUFFER_API_KEY"), secret("BUFFER_CHANNEL_ID")
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


@st.cache_data(ttl=600, show_spinner=False)
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
def ask_gemini(prompt_template, page_text, _api_key):
    prompt = prompt_template.replace("<<PAGE_TEXT>>", page_text)
    ai_response = get_gemini_client(_api_key).models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0),
    )
    data = json.loads(strip_json_fences(ai_response.text or ""))
    if not isinstance(data, dict):
        raise ValueError("Gemini did not return a JSON object.")
    return data


# ==========================================
# STATE
# ==========================================
def dt_or_none(value):
    return None if value is None or pd.isna(value) else pd.Timestamp(value).to_pydatetime()


def build_dates_df(cfg, data):
    rows, index = [], []
    for key, label, all_day_default in cfg["dates"]:
        raw = data.get(key, TBA)
        dt = parse_to_datetime(raw)
        rows.append({"Event": label, "All day": (not has_time(raw)) if dt else all_day_default, "Date & time": dt})
        index.append(key)
    df = pd.DataFrame(rows, index=index)
    df["Date & time"] = pd.to_datetime(df["Date & time"])
    return df


def build_sales_df(cfg, data):
    extras = cfg["sale_extras"]
    rows = []
    for sale in data.get("sales") or []:
        tier = shorten_tier_name(str(sale.get("tier") or ""))
        if not tier:
            continue  # wheelchair / ambulant / disabled tiers are excluded
        row = {
            "Tier": tier,
            "Opens": parse_to_datetime(sale.get("open")),
            "Closes": parse_to_datetime(sale.get("close")),
        }
        if "info" in extras:
            row["Info"] = str(sale.get("info") or "")
        if "forwarding_deadline" in extras:
            row["Forwarding deadline"] = parse_to_datetime(sale.get("forwarding_deadline"))
        rows.append(row)

    columns = ["Tier", "Opens", "Closes"]
    if "info" in extras:
        columns.append("Info")
    if "forwarding_deadline" in extras:
        columns.append("Forwarding deadline")
    df = pd.DataFrame(rows, columns=columns)
    for col in ("Opens", "Closes", "Forwarding deadline"):
        if col in df:
            df[col] = pd.to_datetime(df[col])
    df["Tier"] = df["Tier"].astype("string")
    if "Info" in df:
        df["Info"] = df["Info"].astype("string")
    return df


def load_match(match_type, data, url=None):
    cfg = MATCH_TYPES[match_type]
    st.session_state.scan_id = st.session_state.get("scan_id", 0) + 1
    st.session_state.match = {
        "type": match_type,
        "data": data,
        "url": url,
        "dates_df": build_dates_df(cfg, data),
        "sales_df": build_sales_df(cfg, data),
    }


def start_over():
    keep = {"scheduled_ids", "scan_id"}
    for k in list(st.session_state.keys()):
        if k not in keep:
            del st.session_state[k]


# ==========================================
# STEP 1 — SCAN
# ==========================================
def render_scan_step(gemini_key):
    st.markdown("#### 1 · Scan a ticket page")
    with st.container(border=True):
        type_options = [AUTO, *MATCH_TYPES]
        chosen = st.segmented_control(
            "Match type",
            type_options,
            default=AUTO,
            format_func=lambda k: "✨ Auto-detect" if k == AUTO else MATCH_TYPES[k]["label"],
            key="type_choice",
        ) or AUTO
        url = st.text_input("Ticket page URL", placeholder="https://www.liverpoolfc.com/tickets/...", key="url")
        force = st.checkbox("Re-read the page (ignore the 10-minute cache)", key="force")

        c1, c2 = st.columns([2, 1])
        scan = c1.button("🔍 Scan page", type="primary", width="stretch", disabled=not gemini_key)
        manual = c2.button("✍️ Enter manually", width="stretch")
        if not gemini_key:
            st.caption("Scanning is off because GEMINI_API_KEY isn't set — you can still enter details manually.")

    if manual:
        if chosen == AUTO:
            st.error("Pick a match type above first, then press Enter manually.")
        else:
            load_match(chosen, {})
            st.toast("Blank form ready — fill in the details below.")

    if scan:
        url = (url or "").strip()
        if not url:
            st.error("Please provide the URL.")
            return
        if not url.startswith(("http://", "https://")):
            st.error("The URL must start with http:// or https://")
            return
        with st.status("Scanning ticket page…", expanded=False) as status:
            try:
                if force:
                    fetch_page_text.clear(url)
                status.update(label="Downloading page…")
                page_text = fetch_page_text(url)

                match_type = chosen
                if match_type == AUTO:
                    status.update(label="Working out the match type…")
                    if force:
                        ask_gemini.clear(CLASSIFY_PROMPT, page_text, gemini_key)
                    match_type = ask_gemini(CLASSIFY_PROMPT, page_text, gemini_key).get("match_type")
                    if match_type not in MATCH_TYPES:
                        raise ValueError("Couldn't tell what kind of match this is — pick the type above and scan again.")

                status.update(label="Extracting dates…")
                prompt = MATCH_TYPES[match_type]["prompt"]
                if force:
                    ask_gemini.clear(prompt, page_text, gemini_key)
                data = ask_gemini(prompt, page_text, gemini_key)
            except requests.HTTPError as e:
                status.update(label="Scan failed", state="error")
                st.error(f"The ticket page returned an error ({e.response.status_code}). Check the URL.")
            except requests.RequestException as e:
                status.update(label="Scan failed", state="error")
                st.error(f"Could not load the ticket page: {e}")
            except json.JSONDecodeError:
                status.update(label="Scan failed", state="error")
                st.error("Gemini returned something that wasn't valid JSON. Try scanning again.")
            except Exception as e:
                status.update(label="Scan failed", state="error")
                st.error(f"Failed to automatically pull details: {e}")
            else:
                load_match(match_type, data, url)
                status.update(label=f"Scanned — {MATCH_TYPES[match_type]['label']}", state="complete")


# ==========================================
# STEP 2 — DETAILS
# ==========================================
def render_details_step(match):
    cfg = MATCH_TYPES[match["type"]]
    data = match["data"]
    sid = st.session_state.scan_id

    st.markdown("#### 2 · Check the details")
    detected = "Detected" if match["url"] else "Type"
    st.caption(f"{detected}: **{cfg['label']}**. Wrong type? Pick the right one above and scan again.")

    details = {}
    with st.container(border=True):
        details["name"] = st.text_input("Opponent", value=data.get("match_name", "") or "", key=f"name_{sid}")
        if match["type"] == "cup_home":
            details["competition"] = st.text_input(
                "Competition", value=data.get("competition", "") or "League Cup", key=f"comp_{sid}"
            )

    with st.container(border=True):
        st.markdown("**🗓️ Key dates**")
        st.caption("Tap a cell to change it. Clear a date to mark it TBA. Tick *All day* for dates with no time.")
        dates = st.data_editor(
            match["dates_df"],
            key=f"dates_{sid}",
            hide_index=True,
            width="stretch",
            num_rows="fixed",
            column_config={
                "Event": st.column_config.TextColumn(disabled=True, width=104),
                "All day": st.column_config.CheckboxColumn("All day", width=76),
                "Date & time": st.column_config.DatetimeColumn(format="ddd D MMM, h:mma", step=60, width=160),
            },
        )
        for key, _, _ in cfg["dates"]:
            details[key] = to_display(dt_or_none(dates.at[key, "Date & time"]), bool(dates.at[key, "All day"]))

    with st.container(border=True):
        st.markdown("**🎟️ Sale tiers**")
        st.caption("Add or remove rows with the table controls. Empty = TBA. Swipe the table sideways to see every column.")
        dt_col = st.column_config.DatetimeColumn(format="ddd D MMM, h:mma", step=60, width="small")
        column_config = {"Tier": st.column_config.TextColumn(required=True, width="small"), "Opens": dt_col, "Closes": dt_col}
        if "Forwarding deadline" in match["sales_df"]:
            column_config["Forwarding deadline"] = dt_col
        sales_df = st.data_editor(
            match["sales_df"],
            key=f"sales_{sid}",
            hide_index=True,
            width="stretch",
            num_rows="dynamic",
            column_config=column_config,
        )
        sales = []
        for _, row in sales_df.iterrows():
            tier = row.get("Tier")
            if tier is None or pd.isna(tier) or not str(tier).strip():
                continue
            sale = {
                "tier": str(tier).strip(),
                "open": to_display(dt_or_none(row.get("Opens"))),
                "close": to_display(dt_or_none(row.get("Closes"))),
            }
            if "Info" in sales_df:
                info = row.get("Info")
                sale["info"] = "" if info is None or pd.isna(info) else str(info)
            if "Forwarding deadline" in sales_df:
                sale["forwarding_deadline"] = to_display(dt_or_none(row.get("Forwarding deadline")))
            sales.append(sale)
        details["sales"] = sales
        if not sales:
            st.warning("No sale tiers yet — add at least one row.")

    if match["type"] == "pl_home":
        with st.container(border=True):
            auto_hallmap = get_hallmap_link(details["name"], details["match_date"])
            override = st.text_input("Hallmap link (optional override)", key=f"hallmap_{sid}", placeholder=auto_hallmap)
            details["hallmap_url"] = override.strip() or auto_hallmap
            st.caption(f"Using: {details['hallmap_url']}")

    return details


# ==========================================
# STEP 3 — TWEETS
# ==========================================
def _short_hash(text):
    return hashlib.md5(text.encode()).hexdigest()[:10]


def render_tweets_step(tweets, cal_prefix):
    """Shows each tweet in an editable box. Returns the tweets with final text + include flags."""
    sid = st.session_state.scan_id
    st.markdown("#### 3 · Review tweets")
    st.caption(
        "Tweets update automatically as you change the details. You can edit any text here — "
        "edits are kept until you change a detail that tweet depends on."
    )

    final = []
    for i, t in enumerate(tweets):
        generated_length = tweet_length(t["text"])
        flag = " ⚠️" if generated_length > TWEET_LIMIT else ""
        with st.expander(f"{t['title']} · {t['when']}{flag}", expanded=(i == 0)):
            text = st.text_area(
                "Tweet text",
                value=t["text"],
                key=f"tw_{sid}_{i}_{_short_hash(t['text'])}",
                height=220,
                label_visibility="collapsed",
            )
            length = tweet_length(text)
            if length > TWEET_LIMIT:
                st.warning(f"About {length}/{TWEET_LIMIT} characters — too long for a standard X account (fine with X Premium).")
            else:
                st.caption(f"≈ {length}/{TWEET_LIMIT} characters (links count as 23, emoji and • as 2)")

            include = True
            if t["buffer"]:
                include = st.checkbox("Include in Buffer schedule", value=True, key=f"inc_{sid}_{t['title']}")
            else:
                st.caption("Post this one yourself now with the button below — it isn't sent to Buffer.")

            c1, c2 = st.columns(2)
            c1.link_button("🌐 Post on X now", get_x_intent_url(text), width="stretch")
            dt = parse_to_datetime(t["when"])
            if dt:
                c2.link_button("📅 Calendar reminder", gcal_url(f"{cal_prefix}: {t['title']}", dt), width="stretch")
        final.append({**t, "text": text, "include": include})
    return final


# ==========================================
# STEP 4 — SCHEDULE & CALENDAR
# ==========================================
def render_schedule_step(tweets, match_name, events):
    st.markdown("#### 4 · Schedule & calendar")
    scheduled = st.session_state.setdefault("scheduled_ids", set())
    now = now_uk()

    with st.container(border=True):
        st.markdown("**⚡ Buffer**")
        rows, to_send = [], []
        for t in tweets:
            if not t["buffer"]:
                continue
            dt = parse_to_datetime(t["when"])
            length = tweet_length(t["text"])
            tid = f"{match_name}|{t['title']}|{dt.isoformat() if dt else ''}"
            if tid in scheduled:
                status = "✅ Sent"
            elif not t["include"]:
                status = "⏸️ Off"
            elif not dt:
                status = "⏭️ TBA"
            elif dt <= now:
                status = "⏭️ Past"
            elif length > TWEET_LIMIT:
                status = f"⏭️ {length} chars"
            else:
                status = "🕒 Send"
                to_send.append((t, dt, tid))
            rows.append(
                {
                    "": status,
                    "When (UK)": format_datetime(dt).replace(":00", "") if dt else TBA,
                    "Tweet": t["title"],
                    "UTC": buffer_due_at(dt)[11:16] if dt else "—",
                }
            )
        if rows:
            st.dataframe(rows, hide_index=True, width="stretch")
            st.caption("🕒 will send · ✅ already sent · ⏸️ unticked · ⏭️ skipped (TBA, already passed or too long)")

        buffer_ready = bool(secret("BUFFER_API_KEY") and secret("BUFFER_CHANNEL_ID"))
        if st.button(
            f"🚀 Schedule {len(to_send)} reminder(s) to Buffer",
            type="primary",
            width="stretch",
            key="buffer_go",
            disabled=not (to_send and buffer_ready),
        ):
            prog = st.progress(0.0)
            ok_count, errors = 0, []
            for idx, (t, dt, tid) in enumerate(to_send):
                ok, msg = schedule_to_buffer(t["text"], dt)
                if ok:
                    ok_count += 1
                    scheduled.add(tid)
                else:
                    errors.append(f"Failed to queue '{t['title']}': {msg}")
                prog.progress((idx + 1) / len(to_send))
            st.session_state.buffer_result = (ok_count, len(to_send), errors)
            st.rerun()

        result = st.session_state.pop("buffer_result", None)
        if result:
            ok_count, total, errors = result
            for err in errors:
                st.error(err)
            if ok_count:
                st.success(f"🎉 Scheduled {ok_count}/{total} reminders to Buffer!")
        if not buffer_ready:
            st.caption("Add BUFFER_API_KEY and BUFFER_CHANNEL_ID to your secrets to enable scheduling.")
        else:
            st.caption("Each reminder is only sent once per session. If you change a time after scheduling, delete the old post in Buffer.")

    with st.container(border=True):
        st.markdown("**📅 Calendar**")
        dated = [ev for ev in events if parse_to_datetime(ev["time"])]
        slug = re.sub(r"[^a-z0-9]+", "-", match_name.lower()).strip("-") or "match"
        st.download_button(
            f"📥 Download all {len(dated)} dates (.ics)",
            data=build_ics(dated),
            file_name=f"lfc-{slug}-dates.ics",
            mime="text/calendar",
            width="stretch",
            disabled=not dated,
        )
        st.caption("Opens in Apple Calendar, Outlook or Google Calendar and adds every date at once.")
        with st.expander("Or add dates one at a time (Google Calendar)"):
            for ev in dated:
                st.link_button(
                    f"📅 {ev['label']} ({ev['time']})",
                    gcal_url(ev["name"], parse_to_datetime(ev["time"]), is_all_day=ev["all_day"]),
                    width="stretch",
                )


# ==========================================
# SIDEBAR
# ==========================================
def render_sidebar(gemini_key):
    with st.sidebar:
        st.markdown("### ⚙️ Setup")
        checks = [
            ("Gemini (scanning)", bool(gemini_key)),
            ("Buffer API key", bool(secret("BUFFER_API_KEY"))),
            ("Buffer channel", bool(secret("BUFFER_CHANNEL_ID"))),
        ]
        for label, ok in checks:
            st.markdown(f"{'✅' if ok else '❌'} {label}")
        st.caption(f"UK time now: {format_datetime(now_uk())}")
        st.divider()
        scheduled = len(st.session_state.get("scheduled_ids", ()))
        st.markdown(f"**{scheduled}** reminder(s) scheduled this session")
        if st.button("🔄 Start a new match", width="stretch"):
            start_over()
            st.rerun()


# ==========================================
# APP
# ==========================================
st.set_page_config(page_title="LFC Alerts & Scheduler", page_icon="🔴", layout="centered")

gemini_api_key = secret("GEMINI_API_KEY")
render_sidebar(gemini_api_key)

st.markdown("## 🔴 LFC Ticket Alerts")
st.caption("Scan a ticket page → check the dates → review tweets → schedule to Buffer and your calendar.")

render_scan_step(gemini_api_key)

match = st.session_state.get("match")
if match:
    st.divider()
    details = render_details_step(match)
    cfg = MATCH_TYPES[match["type"]]
    generated = cfg["tweets"](details)
    events = cfg["events"](details)

    st.divider()
    final_tweets = render_tweets_step(generated, f"{details['name']} {cfg['suffix']}")

    st.divider()
    render_schedule_step(final_tweets, details["name"], events)
