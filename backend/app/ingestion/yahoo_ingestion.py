from __future__ import annotations

"""
Yahoo Finance earnings-call transcript ingestion.

Strategy
- Discover transcript URLs for a ticker using Playwright (handles dynamic pages).
- Fetch transcript page HTML with requests + cookies from Playwright storage_state.
- Parse transcript content via one of two paths:

  PATH A (sometimes present):
    transcriptContent is embedded in HTML inside a big JSON blob in <script>.

  PATH B (more reliable):
    transcriptContent is loaded from an internal XHR endpoint:
      /xhr/transcript?eventType=earnings_call&quartrId=...&eventId=...&crumb=...

  We now support both, with PATH B as a fallback.

Paywall behavior
- Some historical transcripts are paywalled. Yahoo may still show speaker mapping and
  only a tiny stub (e.g., 0-1 turns). We mark paywalled=True but keep what we can.
"""

import html as html_lib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright


# ------------------------
# User agents
# ------------------------

UA_HTTP = {
    "User-Agent": "Mozilla/5.0",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

UA_PW = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
)


# ------------------------
# Models
# ------------------------

@dataclass(frozen=True)
class TranscriptMeta:
    ticker: str
    url: str
    quarter: str | None
    year: int | None
    event_id: int | None


@dataclass(frozen=True)
class TranscriptParsed:
    ticker: str
    url: str
    event_id: int | None
    company_id: int | None
    version: str | None
    speaker_map: dict[int, dict]
    turns: list[dict]
    paywalled: bool = False


# ------------------------
# Errors
# ------------------------

class YahooConsentError(RuntimeError):
    """Raised when Yahoo redirects to consent.yahoo.com (cookies expired)."""


class YahooTranscriptParseError(RuntimeError):
    """Raised when we can't find or interpret transcript content."""


# ------------------------
# Cookies
# ------------------------

def load_playwright_cookies_into_session(state_path: Path, sess: requests.Session) -> None:
    """
    Loads cookies from a Playwright storage_state.json file into a requests session.
    """
    if not state_path.exists():
        return

    data: dict[str, Any] = json.loads(state_path.read_text(encoding="utf-8"))
    for c in data.get("cookies", []):
        if c.get("domain") and c.get("name") and c.get("value") is not None:
            sess.cookies.set(
                name=c["name"],
                value=c["value"],
                domain=c["domain"],
                path=c.get("path", "/"),
            )


# ------------------------
# Consent handling
# ------------------------

def try_accept_consent(page) -> bool:
    """
    Best-effort clicker for Yahoo consent dialogs.
    """
    candidates = [
        "button:has-text('Accept all')",
        "button:has-text('I accept')",
        "button:has-text('Agree')",
        "button:has-text('Accept')",
        "#agree",
        "button[name='agree']",
    ]
    for sel in candidates:
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=2000):
                loc.click(timeout=3000)
                return True
        except Exception:
            continue
    return False


def refresh_consent_cookies(storage_state_path: Path) -> None:
    """
    Opens browser to refresh Yahoo consent cookies.
    Uses headless mode in Docker/CI environments.
    """
    # Ensure parent directory exists (required for Playwright to write the file)
    storage_state_path.parent.mkdir(parents=True, exist_ok=True)
    
    url = "https://finance.yahoo.com"
    with sync_playwright() as p:
        # Use headless mode if running in Docker or HEADLESS env var is set
        headless = os.environ.get("HEADLESS", "false").lower() == "true"
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            user_agent=UA_PW,
            viewport={"width": 1280, "height": 900},
            locale="en-US",
        )
        page = context.new_page()

        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2500)
        try_accept_consent(page)

        # Time to click manually if needed
        page.wait_for_timeout(4000)

        context.storage_state(path=str(storage_state_path))
        context.close()
        browser.close()


def check_cookie_validity(storage_state_path: Path) -> dict:
    """
    Checks if Yahoo Finance cookies are valid by attempting a quick navigation.
    Returns dict with 'valid' (bool) and 'message' (str).
    """
    if not storage_state_path.exists():
        return {"valid": False, "message": "No cookie file found"}

    url = "https://finance.yahoo.com/quote/AAPL/earnings-calls"

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=UA_PW,
                viewport={"width": 1280, "height": 900},
                locale="en-US",
                storage_state=str(storage_state_path),
            )
            page = context.new_page()

            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)

            # Check if we got redirected to consent page
            if "consent.yahoo.com" in page.url:
                context.close()
                browser.close()
                return {"valid": False, "message": "Cookies expired - consent required"}

            context.close()
            browser.close()
            return {"valid": True, "message": "Cookies are valid"}

    except Exception as e:
        return {"valid": False, "message": f"Error checking cookies: {type(e).__name__}"}


# ------------------------
# Discovery
# ------------------------

def discover_transcripts(ticker: str, storage_state_path: Path) -> list[TranscriptMeta]:
    """
    Uses Playwright (headless) with stored consent to list transcript URLs.
    """
    url = f"https://finance.yahoo.com/quote/{ticker}/earnings-calls"
    out: list[TranscriptMeta] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=UA_PW,
            viewport={"width": 1280, "height": 900},
            locale="en-US",
            storage_state=str(storage_state_path) if storage_state_path.exists() else None,
        )
        page = context.new_page()

        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3500)

        if "consent.yahoo.com" in page.url:
            context.close()
            browser.close()
            raise YahooConsentError(
                "Redirected to consent.yahoo.com. Need to refresh cookies."
            )

        base = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
        hrefs = page.eval_on_selector_all(
            "a[href]",
            "els => els.map(e => e.getAttribute('href')).filter(Boolean)",
        )

        seen: set[str] = set()
        for h in hrefs:
            full = urljoin(base, h)
            if f"/quote/{ticker}/earnings/" in full and "earnings_call" in full:
                if full in seen:
                    continue
                seen.add(full)

                m = re.search(
                    rf"/quote/{re.escape(ticker)}/earnings/"
                    rf"{re.escape(ticker)}-(Q[1-4])-(\d{{4}})-earnings_call-(\d+)\.html",
                    full,
                )
                quarter = m.group(1) if m else None
                year = int(m.group(2)) if m else None
                event_id = int(m.group(3)) if m else None

                out.append(
                    TranscriptMeta(
                        ticker=ticker,
                        url=full,
                        quarter=quarter,
                        year=year,
                        event_id=event_id,
                    )
                )

        context.close()
        browser.close()

    out.sort(key=lambda x: (x.event_id or 0), reverse=True)
    return out


# ------------------------
# Fetch + parse helpers
# ------------------------

def _is_paywall_html(html: str) -> bool:
    lowered = html.lower()
    markers = [
        "upgrade to unlock our full archive",
        "a silver or gold subscription plan is required",
        "subscription plan is required to access historical transcripts",
        "unlock our full archive of historical earnings transcripts",
    ]
    return any(m in lowered for m in markers)


def _extract_xhr_transcript_url(html: str, page_url: str) -> Optional[str]:
    """
    Extracts the internal XHR transcript endpoint from the HTML if present.
    Example in HTML (often entity-escaped):
      data-url="/xhr/transcript?eventType=earnings_call&amp;quartrId=4742&amp;eventId=369370...".
    """
    m = re.search(r'data-url="([^"]*?/xhr/transcript[^"]+)"', html)
    if not m:
        return None

    raw = html_lib.unescape(m.group(1))  # converts &amp; -> &
    base = f"{urlparse(page_url).scheme}://{urlparse(page_url).netloc}"
    return urljoin(base, raw)


def _try_parse_transcriptcontent_from_cached_blob(html: str) -> Optional[dict[str, Any]]:
    """
    Some saved pages contain a cached XHR response like:
      {"status":200,...,"body":"{\"transcriptContent\":{...}}"}
    We attempt to locate and parse that directly from HTML as a fast path.
    """
    # Find a nearby JSON snippet that contains "body":"{\"transcriptContent\"..."
    # Keep it conservative to avoid huge regex backtracking.
    idx = html.find('"transcriptContent"')
    if idx == -1:
        return None

    # Look backwards a bit for a JSON object start
    start = max(0, idx - 2000)
    window = html[start : idx + 2000]

    # Locate `"body":"{...transcriptContent...}"`
    m = re.search(r'"body"\s*:\s*"(\{\\\"transcriptContent\\\".*)"', window)
    if not m:
        return None

    body_escaped = m.group(1)
    # body is a JSON string with escaped quotes; decode it by wrapping in quotes then json.loads
    try:
        body_json_text = json.loads(f'"{body_escaped}"')
        body_obj = json.loads(body_json_text)
    except Exception:
        return None

    tc = body_obj.get("transcriptContent")
    return tc if isinstance(tc, dict) else None


def _extract_embedded_json_objects(soup: BeautifulSoup) -> list[dict[str, Any]]:
    """
    Yahoo embeds JSON blobs inside <script> tags.
    We scan scripts and try to parse JSON objects that mention transcript/transcriptContent.
    """
    objs: list[dict[str, Any]] = []

    for s in soup.find_all("script"):
        blob = s.string
        if not blob:
            continue
        if ("transcriptContent" not in blob) and ("transcript" not in blob):
            continue

        # Try: root.App.main = {...};
        m = re.search(r"root\.App\.main\s*=\s*({.*?})\s*;\s*$", blob, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(1))
                if isinstance(data, dict):
                    objs.append(data)
            except Exception:
                pass

        # Try: first JSON object in the script (best-effort)
        m2 = re.search(r"({.*})", blob, re.DOTALL)
        if m2:
            try:
                data2 = json.loads(m2.group(1))
                if isinstance(data2, dict):
                    objs.append(data2)
            except Exception:
                pass

    return objs


def _find_transcript_content(objs: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """
    Find a dict that contains transcriptContent, possibly nested.
    Returns the *transcriptContent dict*.
    """
    for data in objs:
        if "transcriptContent" in data and isinstance(data["transcriptContent"], dict):
            return data["transcriptContent"]

        stack: list[Any] = [data]
        while stack:
            cur = stack.pop()
            if isinstance(cur, dict):
                tc = cur.get("transcriptContent")
                if isinstance(tc, dict):
                    return tc
                stack.extend(cur.values())
            elif isinstance(cur, list):
                stack.extend(cur)

    return None


def _build_speaker_map(tc: dict[str, Any]) -> dict[int, dict]:
    out: dict[int, dict] = {}
    speaker_mapping = tc.get("speaker_mapping", []) or []
    for item in speaker_mapping:
        if not isinstance(item, dict):
            continue
        sid = item.get("speaker")
        sdata = item.get("speaker_data") or {}
        if isinstance(sid, int):
            out[sid] = {
                "name": sdata.get("name"),
                "role": sdata.get("role"),
                "company": sdata.get("company"),
            }
    return out


def _extract_turns(tc: dict[str, Any]) -> list[dict]:
    """
    Extract transcript turns from various formats.

    New format (2026+):
      tc["transcript"] = {"text": "...", "paragraphs": [{ speaker, text, start, end }, ...]}

    Old format:
      tc["transcript"] = [{ speaker, text, start, end }, ...]
    """
    # First check if transcript is a dict with paragraphs (new format)
    transcript_val = tc.get("transcript")
    if isinstance(transcript_val, dict) and "paragraphs" in transcript_val:
        paragraphs = transcript_val["paragraphs"]
        if isinstance(paragraphs, list) and paragraphs:
            turns: list[dict] = []
            for it in paragraphs:
                if not isinstance(it, dict):
                    continue

                speaker = it.get("speaker")
                if speaker is None:
                    speaker = it.get("speaker_id") or it.get("spk")

                text = it.get("text")
                if text is None:
                    text = it.get("content") or it.get("utterance") or it.get("line")

                if not text:
                    continue

                rec = {"speaker": speaker, "text": str(text)}

                if isinstance(it.get("start"), (int, float)):
                    rec["start"] = float(it["start"])
                if isinstance(it.get("end"), (int, float)):
                    rec["end"] = float(it["end"])

                turns.append(rec)

            if turns:
                return turns

    # Fallback to old format: check for list-based transcript keys
    candidate_keys = ["transcript", "transcript_segments", "segments", "content", "turns", "items"]
    for k in candidate_keys:
        v = tc.get(k)
        if not isinstance(v, list) or not v:
            continue

        turns: list[dict] = []
        for it in v:
            if not isinstance(it, dict):
                continue

            speaker = it.get("speaker")
            if speaker is None:
                speaker = it.get("speaker_id") or it.get("spk")

            text = it.get("text")
            if text is None:
                text = it.get("content") or it.get("utterance") or it.get("line")

            if not text:
                continue

            rec = {"speaker": speaker, "text": str(text)}

            if isinstance(it.get("start"), (int, float)):
                rec["start"] = float(it["start"])
            if isinstance(it.get("end"), (int, float)):
                rec["end"] = float(it["end"])

            turns.append(rec)

        if turns:
            return turns

    return []


def _fetch_xhr_transcript(sess: requests.Session, xhr_url: str) -> dict[str, Any]:
    """
    Calls the internal Yahoo XHR transcript endpoint and returns transcriptContent dict.
    The XHR usually returns JSON like:
      { status: 200, body: "{ \"transcriptContent\": {...} }" }
    Sometimes it may return directly the body JSON.
    """
    r = sess.get(
        xhr_url,
        timeout=30,
        allow_redirects=True,
        headers={**UA_HTTP, "Accept": "application/json, text/plain, */*"},
    )

    if "consent.yahoo.com" in str(r.url):
        raise YahooConsentError("Consent expired during XHR fetch.")

    # First try to parse as JSON response
    try:
        j = r.json()
    except Exception as e:
        raise YahooTranscriptParseError(f"XHR did not return JSON: {type(e).__name__}: {e}") from e

    # If it contains body string, parse it
    if isinstance(j, dict) and isinstance(j.get("body"), str):
        try:
            body_obj = json.loads(j["body"])
        except Exception as e:
            raise YahooTranscriptParseError(
                f"XHR body could not be decoded as JSON: {type(e).__name__}: {e}"
            ) from e
        tc = body_obj.get("transcriptContent")
        if isinstance(tc, dict):
            return tc

    # Or maybe it returned the transcriptContent directly
    if isinstance(j, dict) and isinstance(j.get("transcriptContent"), dict):
        return j["transcriptContent"]

    # Or maybe it returned the body JSON directly
    if isinstance(j, dict) and "event_id" in j and "speaker_mapping" in j:
        return j

    raise YahooTranscriptParseError("XHR JSON did not contain transcriptContent.")


def fetch_and_parse_transcript(url: str, ticker: str, storage_state_path: Path) -> TranscriptParsed:
    """
    Fetch transcript page HTML and parse transcriptContent.
    Uses embedded JSON if available; otherwise falls back to XHR endpoint.
    """
    sess = requests.Session()
    sess.headers.update(UA_HTTP)
    load_playwright_cookies_into_session(storage_state_path, sess)

    try:
        r = sess.get(url, timeout=30, allow_redirects=True)
    except requests.RequestException as e:
        raise YahooTranscriptParseError(f"HTTP request failed: {type(e).__name__}: {e}") from e

    if "consent.yahoo.com" in str(r.url):
        raise YahooConsentError("Consent expired during fetch.")

    html = r.text or ""
    paywall_html = _is_paywall_html(html)

    # PATH 0: cached blob (often in saved pages)
    tc = _try_parse_transcriptcontent_from_cached_blob(html)

    # PATH A: embedded JSON in scripts
    if tc is None:
        soup = BeautifulSoup(html, "html.parser")
        objs = _extract_embedded_json_objects(soup)
        tc = _find_transcript_content(objs)

    # PATH B: XHR fallback
    if tc is None:
        xhr_url = _extract_xhr_transcript_url(html, page_url=url)
        if xhr_url:
            tc = _fetch_xhr_transcript(sess, xhr_url)

    if tc is None:
        raise YahooTranscriptParseError("Could not locate transcriptContent in embedded JSON or XHR.")

    speaker_map = _build_speaker_map(tc)
    turns = _extract_turns(tc)

    paywalled = bool(paywall_html)
    if not paywalled and len(speaker_map) >= 3 and len(turns) <= 1:
        paywalled = True

    return TranscriptParsed(
        ticker=ticker,
        url=url,
        event_id=tc.get("event_id"),
        company_id=tc.get("company_id"),
        version=tc.get("version"),
        speaker_map=speaker_map,
        turns=turns,
        paywalled=paywalled,
    )
