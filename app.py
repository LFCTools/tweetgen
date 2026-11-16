import hashlib
import json
import re

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup
from google import genai
from google.genai import types

import importlib

import helpers as _helpers_module
import templates as _templates_module

# Streamlit Cloud can keep an old copy of these modules loaded after a deploy, which
# breaks imports of anything new (ImportError). Reloading them on every run is cheap.
importlib.reload(_helpers_module)
importlib.reload(_templates_module)

from helpers import (
    qstash_label,
    uk_to_utc,
    find_acs_dates,
    is_set,
    related_page_links,
    TBA,
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
from datetime import timedelta
from pathlib import Path

from templates import CLASSIFY_PROMPT, IMAGES, MATCH_TYPES, without_footer

GEMINI_MODEL = "gemini-2.5-flash"
PAGE_TEXT_LIMIT = 40000
MIN_PAGE_TEXT = 300
AUTO = "auto"
IMAGES_DIR = Path(__file__).parent / "images"
# Public URL Buffer fetches images from. The repo is public, so GitHub serves the files in images/.
DEFAULT_IMAGE_BASE_URL = "https://raw.githubusercontent.com/LFCTools/tweetgen/main/images/"
NO_IMAGE = "none"
DISCORD_LIMIT = 2000  # Discord message length limit
PREMIUM_LIMIT = 25000  # X Premium post length; set X_CHAR_LIMIT = 280 in secrets for a standard account


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
def image_url(image_key):
    if not image_key or image_key == NO_IMAGE:
        return None
    base = secret("IMAGE_BASE_URL") or DEFAULT_IMAGE_BASE_URL
    return base.rstrip("/") + "/" + IMAGES[image_key][1]


def char_limit():
    try:
        return int(secret("X_CHAR_LIMIT") or PREMIUM_LIMIT)
    except (TypeError, ValueError):
        return PREMIUM_LIMIT


def schedule_to_buffer(text, scheduled_at_local, image=None):
    """Queue a post in Buffer. `scheduled_at_local` is a naive UK-time datetime, `image` a public URL."""
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
    if image:
        variables["input"]["assets"] = [{"image": {"url": image}}]
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
    # JSON response mode is only available on the v1beta API (the library defaults to v1).
    return genai.Client(api_key=api_key, http_options=types.HttpOptions(api_version="v1beta"))


@st.cache_data(ttl=600, show_spinner=False)
def fetch_page(url):
    """Download a page; returns its visible text and the links in its main content."""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    response = requests.get(url, headers=headers, timeout=15)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    # Collect links before stripping the page chrome — the ticket-info link can live in a header block.
    links = [a.get("href") for a in soup.find_all("a") if a.get("href")]
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.extract()
    page_text = " ".join(soup.get_text().split())
    if len(page_text) < MIN_PAGE_TEXT:
        raise ValueError(
            f"The page only contained {len(page_text)} characters of text. "
            "The site may have blocked the request or the URL is wrong — check it in a browser."
        )
    return {"text": page_text[:PAGE_TEXT_LIMIT], "links": links}


def gather_pages(url, extra_url=None, force=False):
    """Read the match page plus its companion ticket-info page (found automatically or given).

    Returns (combined text for Gemini, list of URLs read, list of warnings).
    """
    if force:
        fetch_page.clear(url)
    main = fetch_page(url)
    others = []
    if extra_url:
        others.append(extra_url)
    for link in related_page_links(url, main["links"]):
        if link not in others:
            others.append(link)

    def page_info(page_url, page):
        text = page["text"]
        return {
            "url": page_url,
            "chars": len(text),
            "mentions_acs": bool(re.search(r"\bACS\b|auto cup", text, re.IGNORECASE)),
            "ticket_info_links": sorted({h for h in page["links"] if "ticket-info" in h})[:10],
        }

    parts, sources, warnings = [f"=== MAIN PAGE: {url} ===\n{main['text']}"], [url], []
    pages = [page_info(url, main)]
    for other in others[:2]:
        try:
            if force:
                fetch_page.clear(other)
            page = fetch_page(other)
            parts.append(f"=== ADDITIONAL PAGE (same match): {other} ===\n{page['text']}")
            sources.append(other)
            pages.append(page_info(other, page))
        except Exception as e:  # the main page is enough to carry on
            warnings.append(f"Couldn't read {other} ({e}) — carried on with the main page.")
    st.session_state.scan_pages = pages
    return "\n\n".join(parts), sources, warnings


@st.cache_data(ttl=600, show_spinner=False)
def ask_gemini(prompt_template, page_text, _api_key):
    prompt = prompt_template.replace("<<PAGE_TEXT>>", page_text)
    client = get_gemini_client(_api_key)
    try:
        ai_response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0),
        )
    except Exception as e:
        if "json mode" not in str(e).lower():
            raise
        # Fallback: plain text reply; the prompt already demands JSON and fences are stripped below.
        ai_response = client.models.generate_content(
            model=GEMINI_MODEL, contents=prompt, config=types.GenerateContentConfig(temperature=0)
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


def load_match(match_type, data, url=None, sources=None):
    cfg = MATCH_TYPES[match_type]
    st.session_state.scan_id = st.session_state.get("scan_id", 0) + 1
    st.session_state.match = {
        "type": match_type,
        "data": data,
        "url": url,
        "sources": sources or [],
        "dates_df": build_dates_df(cfg, data),
        "sales_df": build_sales_df(cfg, data),
    }


def start_over():
    keep = {"scheduled_buffer", "scheduled_discord", "scan_id"}
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
        extra_url = st.text_input(
            "Extra info page (optional)",
            placeholder="https://www.liverpoolfc.com/tickets/ticket-info/...",
            key="extra_url",
            help="Pages with ACS dates, allocation or prices. If the match page links to one, it's read automatically.",
        )
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

    with st.expander("Scan keeps failing? Paste the page text instead"):
        st.caption(
            "Open the ticket page on your phone or computer, select all the text (Ctrl/Cmd + A), "
            "copy it and paste it here. This skips downloading the page, so it works even if the "
            "LFC site blocks the app."
        )
        pasted = st.text_area("Page text", key="pasted_text", height=150, label_visibility="collapsed")
        scan_pasted = st.button("🔍 Scan pasted text", width="stretch", disabled=not gemini_key, key="scan_pasted")

    if scan:
        url = (url or "").strip()
        if not url:
            st.error("Please provide the URL.")
            return
        if not url.startswith(("http://", "https://")):
            st.error("The URL must start with http:// or https://")
            return
        extra_url = (extra_url or "").strip()
        if extra_url and not extra_url.startswith(("http://", "https://")):
            st.error("The extra info page URL must start with http:// or https://")
            return
        run_scan(chosen, gemini_key, url=url, extra_url=extra_url or None, force=force)

    if scan_pasted:
        text = " ".join((pasted or "").split())
        if len(text) < MIN_PAGE_TEXT:
            st.error("That doesn't look like the full page — paste all of the ticket page text.")
            return
        run_scan(chosen, gemini_key, page_text=text[:PAGE_TEXT_LIMIT])


def describe_error(step, e):
    if isinstance(e, requests.HTTPError):
        code = e.response.status_code if e.response is not None else "?"
        if code in (401, 403, 429):
            return (
                f"The LFC site refused the request (HTTP {code}) — it is probably blocking automated "
                "downloads from the app's server. Use **Paste the page text instead** below the Scan button."
            )
        if code == 404:
            return "The ticket page wasn't found (HTTP 404). Check the URL."
        return f"The ticket page returned an error (HTTP {code})."
    if isinstance(e, requests.Timeout):
        return "The LFC site took too long to respond. Try again, or paste the page text instead."
    if isinstance(e, requests.RequestException):
        return f"Could not load the ticket page: {e}"
    if isinstance(e, json.JSONDecodeError):
        return "Gemini returned something that wasn't valid JSON. Try scanning again."
    msg = str(e)
    if "gemini" in step.lower() and any(k in msg.lower() for k in ("api key", "api_key", "permission", "unauthenticated")):
        return f"Gemini rejected the API key — check GEMINI_API_KEY in your Streamlit secrets. ({msg})"
    if "gemini" in step.lower() and any(k in msg.lower() for k in ("quota", "429", "resource_exhausted")):
        return f"Gemini's usage limit was hit — wait a minute and try again. ({msg})"
    return f"{type(e).__name__}: {msg}"


def run_scan(chosen, gemini_key, url=None, page_text=None, force=False, extra_url=None):
    step = "downloading the page"
    error = None
    sources, warnings = [], []
    with st.status("Scanning ticket page…", expanded=False) as status:
        try:
            if page_text is None:
                status.update(label="Downloading page…")
                page_text, sources, warnings = gather_pages(url, extra_url, force)

            match_type = chosen
            if match_type == AUTO:
                step = "asking Gemini for the match type"
                status.update(label="Working out the match type…")
                if force:
                    ask_gemini.clear(CLASSIFY_PROMPT, page_text, gemini_key)
                match_type = ask_gemini(CLASSIFY_PROMPT, page_text, gemini_key).get("match_type")
                if match_type not in MATCH_TYPES:
                    raise ValueError("Couldn't tell what kind of match this is — pick the type above and scan again.")

            step = "asking Gemini for the dates"
            status.update(label="Extracting dates…")
            prompt = MATCH_TYPES[match_type]["prompt"]
            if force:
                ask_gemini.clear(prompt, page_text, gemini_key)
            data = ask_gemini(prompt, page_text, gemini_key)
            if match_type == "cup_home" and not (is_set(data.get("acs_start")) and is_set(data.get("acs_end"))):
                acs_start, acs_end = find_acs_dates(page_text)  # backup if Gemini missed them
                if acs_start:
                    data = {**data, "acs_start": acs_start, "acs_end": acs_end}
        except Exception as e:
            error = describe_error(step, e)
            status.update(label=f"Scan failed while {step}", state="error")
        else:
            load_match(match_type, data, url, sources)
            status.update(label=f"Scanned — {MATCH_TYPES[match_type]['label']}", state="complete")
    for w in warnings:
        st.warning(w)
    if error:
        st.error(error)


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
    if len(match.get("sources", [])) > 1:
        st.caption("Read " + " + ".join(f"[page {i + 1}]({u})" for i, u in enumerate(match["sources"])))
    pages = st.session_state.get("scan_pages") if match.get("sources") else None
    if pages:
        with st.expander(f"🔎 What the scan read ({len(pages)} page{'s' if len(pages) != 1 else ''})"):
            for i, pg in enumerate(pages, 1):
                st.markdown(
                    f"**Page {i}:** {pg['url']}  \n"
                    f"{pg['chars']:,} characters of text · mentions ACS: {'✅ yes' if pg['mentions_acs'] else '❌ no'}"
                )
                if pg["ticket_info_links"]:
                    st.caption("Ticket-info links on this page: " + ", ".join(pg["ticket_info_links"]))
                else:
                    st.caption("No ticket-info links found on this page.")
            if len(pages) == 1:
                st.caption("Only one page was read. Paste the ticket-info page into **Extra info page** and scan again.")

    details = {}
    with st.container(border=True):
        details["name"] = st.text_input("Opponent", value=data.get("match_name", "") or "", key=f"name_{sid}")
        for key, label in cfg.get("text_fields", []):
            value = data.get(key, "")
            if isinstance(value, list):
                value = "\n".join(str(v) for v in value)
            value = "" if str(value).strip().upper() == TBA else str(value)
            if "one per line" in label:
                details[key] = st.text_area(label, value=value, key=f"{key}_{sid}", height=100)
            else:
                details[key] = st.text_input(label, value=value, key=f"{key}_{sid}")
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
        flag = " ⚠️" if generated_length > char_limit() else ""
        with st.expander(f"{t['title']} · {t['when']}{flag}", expanded=(i == 0)):
            text = st.text_area(
                "Tweet text",
                value=t["text"],
                key=f"tw_{sid}_{i}_{_short_hash(t['text'])}",
                height=220,
                label_visibility="collapsed",
            )
            length = tweet_length(text)
            limit = char_limit()
            if length > limit:
                st.warning(f"About {length}/{limit} characters — over your X post limit. Trim it before posting.")
            elif limit > 280:
                st.caption(f"≈ {length} characters")
            else:
                st.caption(f"≈ {length}/{limit} characters (links count as 23, emoji and • as 2)")

            image_options = [NO_IMAGE, *IMAGES]
            image = st.selectbox(
                "Image",
                image_options,
                index=image_options.index(t["image"]) if t["image"] in IMAGES else 0,
                format_func=lambda k: "No image" if k == NO_IMAGE else IMAGES[k][0],
                key=f"img_{sid}_{t['title']}",
            )
            if image != NO_IMAGE and (IMAGES_DIR / IMAGES[image][1]).exists():
                st.image(str(IMAGES_DIR / IMAGES[image][1]), width="stretch")

            if t["immediate"]:
                st.caption(
                    "If ticked in step 4 this goes to Buffer and posts about 2 minutes after you press Schedule. "
                    "Or post it yourself — the X button below can't attach the image, so add it by hand."
                )

            c1, c2 = st.columns(2)
            c1.link_button("🌐 Post on X now", get_x_intent_url(text), width="stretch")
            dt = parse_to_datetime(t["when"])
            if dt:
                c2.link_button("📅 Calendar reminder", gcal_url(f"{cal_prefix}: {t['title']}", dt), width="stretch")
        final.append({**t, "text": text, "image": None if image == NO_IMAGE else image})
    return final


# ==========================================
# STEP 4 — SCHEDULE & CALENDAR
# ==========================================
def schedule_to_discord(text, scheduled_at_local, image=None, immediate=False, labels=()):
    """Queue a Discord webhook post via QStash, delivered at `scheduled_at_local` (UK time).

    `labels` are shown against the message in the QStash console and can be used to filter its logs.
    """
    token, webhook = secret("QSTASH_TOKEN"), secret("DISCORD_WEBHOOK_URL")
    if not token:
        return False, "QSTASH_TOKEN is missing from Streamlit secrets."
    if not webhook:
        return False, "DISCORD_WEBHOOK_URL is missing from Streamlit secrets."
    base = (secret("QSTASH_URL") or "https://qstash.upstash.io").rstrip("/")

    payload = {"content": text, "allowed_mentions": {"parse": []}}
    if image:
        payload["embeds"] = [{"image": {"url": image}}]
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    if labels:
        headers["Upstash-Label"] = ",".join(labels)
    if not immediate:
        headers["Upstash-Not-Before"] = str(int(uk_to_utc(scheduled_at_local).timestamp()))
    try:
        response = requests.post(f"{base}/v2/publish/{webhook}", headers=headers, json=payload, timeout=10)
        if response.status_code >= 400:
            try:
                detail = response.json().get("error") or response.text
            except ValueError:
                detail = response.text
            return False, f"QStash error (HTTP {response.status_code}): {detail[:300]}"
        return True, "Scheduled successfully"
    except Exception as e:
        return False, str(e)


def _buffer_send(text, dt, image, t, match_label):
    return schedule_to_buffer(text, dt, image=image)


def _discord_send(text, dt, image, t, match_label):
    when = "now" if t["immediate"] else format_datetime(dt).replace(":00", "")
    post_label = qstash_label(match_label, t["title"], when)
    return schedule_to_discord(text, dt, image=image, immediate=t["immediate"], labels=(post_label, qstash_label(match_label)))


CHANNELS = {
    "buffer": {
        "name": "Buffer",
        "title": "⚡ Buffer (X)",
        "icon": "🚀",
        "text": lambda text: text,
        "length": tweet_length,
        "limit": lambda: char_limit(),
        "ready": lambda: bool(secret("BUFFER_API_KEY") and secret("BUFFER_CHANNEL_ID")),
        "send": _buffer_send,
        "caption": "🖼️ = has an image. Times are UK time (converted to UTC for Buffer automatically).",
        "setup": "Add BUFFER_API_KEY and BUFFER_CHANNEL_ID to your secrets to enable scheduling.",
        "note": "Each post is only sent once per session. If you change a time after scheduling, delete the old post in Buffer.",
    },
    "discord": {
        "name": "Discord",
        "title": "💬 Discord",
        "icon": "💬",
        "text": without_footer,
        "length": len,
        "limit": lambda: DISCORD_LIMIT,
        "ready": lambda: bool(secret("QSTASH_TOKEN") and secret("DISCORD_WEBHOOK_URL")),
        "send": _discord_send,
        "caption": "Same posts without the X footer and hashtags. Delivered at the UK times shown via QStash.",
        "setup": "Add QSTASH_TOKEN, QSTASH_URL and DISCORD_WEBHOOK_URL to your secrets to enable Discord.",
        "note": "Each post is only sent once per session. In the QStash console each message is labelled with the match, post and time (filter by the match label, e.g. everton-a). Cancel a post there.",
    },
}


def render_channel(channel, tweets, match_name, match_label):
    """Tick list + send button for one destination (Buffer or Discord)."""
    cfg = CHANNELS[channel]
    scheduled = st.session_state.setdefault(f"scheduled_{channel}", set())
    now = now_uk()
    sid = st.session_state.scan_id
    limit = cfg["limit"]()

    with st.container(border=True):
        st.markdown(f"**{cfg['title']}** — tick the posts to schedule")
        to_send = []
        for t in tweets:
            immediate = t["immediate"]
            dt = now + timedelta(minutes=2) if immediate else parse_to_datetime(t["when"])
            text = cfg["text"](t["text"])
            length = cfg["length"](text)
            tid = f"{match_name}|{t['title']}|" + ("now" if immediate else (dt.isoformat() if dt else ""))

            # Reasons a post can't be sent; such rows are shown unticked and greyed out.
            if tid in scheduled:
                reason = f"✅ already in {cfg['name']}"
            elif not dt:
                reason = "time is TBA"
            elif dt <= now:
                reason = "time has passed"
            elif length > limit:
                reason = f"too long ({length} chars)"
            else:
                reason = None

            when = "posts now" if immediate else format_datetime(dt).replace(":00", "")
            label = f"{t['title']} · {when if dt else TBA}{' 🖼️' if t['image'] else ''}"
            if reason:
                label += f" — {reason}"
            include = st.checkbox(
                label,
                value=not reason,
                disabled=bool(reason),
                key=f"{channel}_send_{sid}_{t['title']}_{'off' if reason else 'on'}",
            )
            if include and not reason:
                to_send.append((t, text, dt, tid))
        st.caption(cfg["caption"])

        ready = cfg["ready"]()
        if st.button(
            f"{cfg['icon']} Schedule {len(to_send)} post(s) to {cfg['name']}",
            type="primary",
            width="stretch",
            key=f"{channel}_go",
            disabled=not (to_send and ready),
        ):
            prog = st.progress(0.0)
            ok_count, errors = 0, []
            for idx, (t, text, dt, tid) in enumerate(to_send):
                if t["immediate"]:
                    dt = now_uk() + timedelta(minutes=2)
                ok, msg = cfg["send"](text, dt, image_url(t["image"]), t, match_label)
                if ok:
                    ok_count += 1
                    scheduled.add(tid)
                else:
                    errors.append(f"Failed to schedule '{t['title']}': {msg}")
                prog.progress((idx + 1) / len(to_send))
            st.session_state[f"{channel}_result"] = (ok_count, len(to_send), errors)
            st.rerun()

        result = st.session_state.pop(f"{channel}_result", None)
        if result:
            ok_count, total, errors = result
            for err in errors:
                st.error(err)
            if ok_count:
                st.success(f"🎉 Scheduled {ok_count}/{total} posts to {cfg['name']}!")
        if not ready:
            st.caption(cfg["setup"])
        else:
            st.caption(cfg["note"])


def render_schedule_step(tweets, match_name, events, match_label):
    st.markdown("#### 4 · Schedule & calendar")
    for channel in CHANNELS:
        render_channel(channel, tweets, match_name, match_label)

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
            ("QStash (Discord)", bool(secret("QSTASH_TOKEN"))),
            ("Discord webhook", bool(secret("DISCORD_WEBHOOK_URL"))),
        ]
        for label, ok in checks:
            st.markdown(f"{'✅' if ok else '❌'} {label}")
        st.caption(f"UK time now: {format_datetime(now_uk())}")
        st.divider()
        for channel, ch in CHANNELS.items():
            st.markdown(f"**{len(st.session_state.get(f'scheduled_{channel}', ()))}** post(s) sent to {ch['name']} this session")
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
    render_schedule_step(final_tweets, details["name"], events, f"{details['name']} {cfg['suffix']}")
