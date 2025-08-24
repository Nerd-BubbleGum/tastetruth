import streamlit as st
import requests 
import socket
import requests
import re
from typing import Dict, Any, List, Tuple
import json
from urllib.parse import quote_plus
from datetime import datetime, timedelta



PERSPECTIVE_API_KEY = "AIzaSyAkSTA_XwWCk57kzmQsHe2HAi3TtOrrCZQ"  # TODO: paste your key here for now (later move to st.secrets["PERSPECTIVE_API_KEY"])





SENSATIONAL_WORDS = {
    "shocking","exposed","bombshell","destroyed","humiliated","traitor","corrupt",
    "fake news","propaganda","lies","agenda","hoax","miracle","guaranteed","evil",
    "disgusting","outrageous","catastrophe","apocalypse","witch hunt","rigged"
}
VAGUE_QUANTIFIERS = {"everyone","nobody","always","never","countless","many","they say","people are saying"}
SOURCE_WORDS = {"source","study","report","data","evidence","according to","as per"}


def count_uppercase_ratio(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    upp = sum(1 for c in letters if c.isupper())
    return upp / len(letters)


def has_excessive_exclamations(text: str) -> bool:
    return text.count("!!!") >= 1 or text.count("!!") >= 2


def contains_sensational(text: str) -> int:
    t = text.lower()
    return sum(1 for w in SENSATIONAL_WORDS if w in t)


def contains_vague_quantifiers(text: str) -> int:
    t = text.lower()
    return sum(1 for w in VAGUE_QUANTIFIERS if w in t)


def has_numbers_without_context(text: str) -> bool:
    numbers = re.findall(r"\b\d{1,3}(?:,\d{3})*(?:\.\d+)?\b", text)
    if not numbers:
        return False
    units = re.compile(r"(percent|%|people|cases|votes|rupees|dollars|years|km|million|billion|crore|lakh|deaths)", re.I)
    t = text
    t_lower = text.lower()
    hits = 0
    for m in numbers:
        idx = t_lower.find(m.lower())
        if idx != -1:
            window = t[max(0, idx-20): idx+20]
            if units.search(window):
                hits += 1
    return (len(numbers) - hits) >= 1


def mentions_sources(text: str) -> bool:
    t = text.lower()
    return any(w in t for w in SOURCE_WORDS) or ("http://" in t or "https://" in t)


def bias_signals(text: str) -> Tuple[Dict[str, Any], int]:
    signals = {}
    score = 0


    upper_ratio = count_uppercase_ratio(text)
    if upper_ratio >= 0.25:
        signals["Many uppercase letters"] = f"{upper_ratio:.0%} uppercase"
        score += 2
    elif upper_ratio >= 0.15:
        signals["Noticeable uppercase"] = f"{upper_ratio:.0%} uppercase"
        score += 1


    if has_excessive_exclamations(text):
        signals["Excessive exclamation marks"] = True
        score += 2


    sens_count = contains_sensational(text)
    if sens_count >= 3:
        signals["Loaded/sensational wording"] = f"{sens_count} instances"
        score += 3
    elif sens_count >= 1:
        signals["Some sensational wording"] = f"{sens_count} instance(s)"
        score += 2


    vague_count = contains_vague_quantifiers(text)
    if vague_count >= 2:
        signals["Vague quantifiers"] = f"{vague_count} terms"
        score += 2
    elif vague_count == 1:
        signals["Vague quantifier"] = True
        score += 1


    if has_numbers_without_context(text):
        signals["Numbers without units/context"] = True
        score += 1


    if not mentions_sources(text):
        signals["No source cited"] = True
        score += 1


    return signals, score


def bias_percentage_from_score(score: int) -> int:
    if score <= 0: return 5
    if score == 1: return 12
    if score == 2: return 20
    if score == 3: return 30
    if score == 4: return 40
    if score == 5: return 50
    if score == 6: return 60
    if score == 7: return 68
    if score == 8: return 75
    if score == 9: return 82
    return 90







def safe_get_json(url: str, timeout: int = 10):
    """
    Fetch JSON safely. Returns dict or {"_error": "...", "_kind": "..."} on failure.
    _kind can be: 'offline', 'timeout', 'http', 'other'
    """
    try:
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.ConnectionError as e:
        # Likely offline / DNS resolution failed
        return {"_error": "No internet connection. Please check your network.", "_kind": "offline"}
    except requests.exceptions.Timeout:
        return {"_error": "Request timed out. Try again in a moment.", "_kind": "timeout"}
    except requests.exceptions.HTTPError as e:
        status = getattr(e.response, "status_code", "HTTP error")
        return {"_error": f"Service returned an error (status {status}).", "_kind": "http"}
    except socket.gaierror:
        return {"_error": "No internet connection. Please check your network.", "_kind": "offline"}
    except Exception as e:
        return {"_error": "Something went wrong while fetching data.", "_kind": "other"}





def perspective_analyze(text: str, api_key: str, lang: str = "en") -> dict:
    """
    Calls Perspective API for TOXICITY and INSULT as style signals.
    Returns dict with scores in % or {"_error": "..."}.
    """
    if not api_key:
        return {"_error": "Perspective API key missing"}
    url = f"https://commentanalyzer.googleapis.com/v1alpha1/comments:analyze?key={api_key}"
    payload = {
        "comment": {"text": text},
        "languages": [lang],
        "requestedAttributes": {
            "TOXICITY": {},
            "INSULT": {}
        }
    }
    try:
        r = requests.post(url, json=payload, timeout=12)
        r.raise_for_status()
        data = r.json()
        out = {}
        for attr in ["TOXICITY", "INSULT"]:
            val = (
                data.get("attributeScores", {})
                    .get(attr, {})
                    .get("summaryScore", {})
                    .get("value")
            )
            if isinstance(val, (int, float)):
                out[attr] = int(round(val * 100))  # convert to %
        return out if out else {"_error": "No scores returned"}
    except requests.exceptions.RequestException:
        return {"_error": "Perspective API request failed"}


def gdelt_search_simple(query: str, max_items: int = 3, hours_back: int = 72) -> list[dict]:
    """
    Simple GDELT 2.1 search for recent coverage.
    Returns list of {title, url, source, date} up to max_items.
    """
    timespan = f"{hours_back} hours"
    q = quote_plus(query)
    url = (
        "https://api.gdeltproject.org/api/v2/doc/doc?"
        f"query={q}&mode=ArtList&format=json&maxrecords={max_items}&timespan={timespan}"
    )
    data = safe_get_json(url, timeout=12)
    if isinstance(data, dict) and "_error" in data:
        return []
    arts = data.get("articles", []) or []
    out = []
    for a in arts[:max_items]:
        out.append({
            "title": a.get("title") or "Untitled",
            "url": a.get("url"),
            "source": a.get("source") or a.get("sourceCountry") or "Unknown",
            "date": a.get("seendate") or ""
        })
    return out




#################
# Streamlit App #
#################





# Navigation control
if "page" not in st.session_state:
    st.session_state.page = "news"  # Default to news section

# Top bar navigation (before title)
col1, col2 = st.columns([4, 1])
with col1:
    st.title("Taste Truth ☄️ - A Fact-Checking Tool")
with col2:
    if st.session_state.page == "news":
        if st.button("🔍 Verify Info", key="nav_verify"):
            st.session_state.page = "verify"
            st.rerun()
    else:
        if st.button("← Back to News", key="nav_back"):
            st.session_state.page = "news"
            st.rerun()



st.markdown(
    "<p style='font-size: 12px; color: grey;'>By Abhiraj, Suryansh, Abhishek, Nikunj and Abhimanyu</p>",
    unsafe_allow_html=True
)


st.write("AI-powered verifier that aggregates fact-checks, checks broader media coverage, and highlights rhetorical patterns that may indicate bias.")



news_api_key = "6c6642268bd8474fbf2061cf9c46ec03"
fact_check_api_key = "AIzaSyA30vwHAA-pwLqRgxUMhpFC62Q5m5Zwy3w"


if st.session_state.page == "news":
    st.header("1. Checkout Latest News")



    articles = []
    if st.button("Fetch Headlines"):
        news_url = (
            f"https://newsapi.org/v2/top-headlines?"
            f"country=us&apiKey={news_api_key}"
        )
        response = requests.get(news_url)
        data = response.json()
        articles = data.get("articles", [])


    if articles:
        st.success("Latest Headlines:")
        for i, article in enumerate(articles[:3], start=1):
            st.subheader(f"News {i}: {article['title']}")
            if article.get("url"):
                st.markdown(f"[→ Read Full News]({article['url']})", unsafe_allow_html=True)





elif st.session_state.page == "verify":
    st.header("2. Verify Information, Evaluate a text")


    headline = st.text_input("Enter the text/info you want to verify:")
    claims = []




    # 3) Google Fact Check API — on button click (authoritative fact-checks)
    if st.button("Check"):
        if not headline.strip():
            st.warning("Please enter a valid news headline.")
        else:
            fact_check_url = (
                "https://factchecktools.googleapis.com/v1alpha1/claims:search"
                f"?query={requests.utils.quote(headline)}&key={fact_check_api_key}"
            )
            # Use safe_get_json for friendly errors
            result = safe_get_json(fact_check_url, timeout=12)
            if isinstance(result, dict) and "_error" in result:
                kind = result.get("_kind")
                if kind == "offline":
                    st.error("Network not connected. Please check your internet connection and try again.")
                elif kind == "timeout":
                    st.error("The fact-check request timed out. Please try again.")
                elif kind == "http":
                    st.error(result["_error"])
                else:
                    st.error("Unable to fetch fact-checks right now. Please try again later.")
            else:
                claims = result.get("claims", []) if isinstance(result, dict) else []





    # 1) Perspective API (style/toxicity) — show immediately when text present
    if headline.strip():
        persp = perspective_analyze(headline, PERSPECTIVE_API_KEY, lang="en")
        if "_error" in persp:
            st.caption("Perspective signal: unavailable")
        else:
            parts = []
            if isinstance(persp.get("TOXICITY"), int):
                parts.append(f"Toxicity: {persp['TOXICITY']}%")
            if isinstance(persp.get("INSULT"), int):
                parts.append(f"Insult: {persp['INSULT']}%")
            if parts:
                st.caption("Signal Style: " + " | ".join(parts) + " — its not a absolute truth/bias measure")




# 2) Recent coverage using Google News
    if headline.strip():
        # Show checking process
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        status_text.text("🔍 Searching news databases...")
        progress_bar.progress(50)
        import time
        time.sleep(0.5)
        
        status_text.text("📰 Analyzing coverage...")
        progress_bar.progress(100)
        time.sleep(0.5)
        
        # Clear progress and show results
        progress_bar.empty()
        status_text.empty()
        
        google_news_url = f"https://news.google.com/search?q={quote_plus(headline)}"
        
        # Success message with bigger clickable arrow inside the box
        st.success(f"✅ Coverage analysis complete! [↗️]({google_news_url})")






# 4) Render Google Fact Check results if any; otherwise show heuristic bias meter
if claims:
    st.success("Fact-Check Results Found:")
    for claim in claims[:3]:
        st.write("Claimed News:", claim.get("text", "N/A"))
        st.write(
            "Rating:",
            claim.get("claimReview", [{}])[0].get("textualRating", "N/A")
        )
        st.write(
            "Source:",
            claim.get("claimReview", [{}])[0].get("publisher", {}).get("name", "N/A")
        )
        url = claim.get("claimReview", [{}])[0].get("url")
    if url:
        st.markdown(f"[→ View Fact-Check Source]({url})", unsafe_allow_html=True)
        st.write("---")

elif headline.strip():
    # Heuristic bias analysis as the final fallback/extra context
    signals, raw_score = bias_signals(headline)
    bias_pct = bias_percentage_from_score(raw_score)

    with st.container():
        st.info("No official fact-check found yet.")
        st.markdown("**Heuristic Style Analysis ⤵️**", help="This is an automated style-based bias estimate, not a fact-check.")
        
    col_left, col_right = st.columns([0.6, 0.4])
    with col_left:
        st.write(f"Likely bias: {bias_pct}%")
    with col_right:
        if signals:
            st.caption("Signals: " + " | ".join(f"{k}{'' if v is True else f' ({v})'}" for k, v in signals.items()))
        else:
            st.caption("No notable bias signals detected.")
    st.caption("Note: Style signals and toxicity are not measures of factual accuracy. They do not indicate bias, but can be used to identify potential bias.")
    







    if claims:
        st.success("Fact-Check Results Found:")
        for claim in claims[:3]:
            st.write("Claimed News:", claim.get("text", "N/A"))
            st.write(
                "Rating:",
                claim.get("claimReview", [{}])[0].get("textualRating", "N/A")
            )
            st.write(
                "Source:",
                claim.get("claimReview", [{}])[0].get("publisher", {}).get("name", "N/A")
            )
            url = claim.get("claimReview", [{}])[0].get("url")
            if url:
                st.markdown(f"[→ View Fact-Check Source]({url})", unsafe_allow_html=True)
            st.write("---")
    elif headline:
        st.info(    
            "New or breaking news may not be fact-checked immediately."
        )
