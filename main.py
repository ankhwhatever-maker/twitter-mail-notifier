import html
import json
import logging
import os
import re
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

import resend

USERNAME = os.environ["X_USERNAME"].lstrip("@")
TO_EMAIL = os.environ["TO_EMAIL"]

resend.api_key = os.environ["RESEND_API_KEY"]

LAST_FILE = Path("last_post.txt")
DIAGNOSTICS_DIR = Path("diagnostics")
MAX_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 3
YAHOO_REALTIME_URL = "https://search.yahoo.co.jp/realtime/search"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

if not re.fullmatch(r"[A-Za-z0-9_]{1,15}", USERNAME):
    raise ValueError("X_USERNAME must be a valid X username")

POST_PATH = re.compile(
    rf"^/{re.escape(USERNAME)}/status/(?P<id>\d+)(?:/.*)?$",
    re.IGNORECASE,
)
NEXT_DATA_PATTERN = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
    re.DOTALL,
)


def get_last_post_id():
    if LAST_FILE.exists():
        value = LAST_FILE.read_text(encoding="utf-8").strip()

        if not value:
            return None
        if value.isdigit():
            return value

        parsed = parse_post_url(value)
        if parsed:
            logger.info("Migrating saved state from URL to post ID")
            return parsed["id"]

        raise RuntimeError(f"Invalid state in {LAST_FILE}: {value!r}")

    return None


def save_last_post_id(post_id):
    LAST_FILE.write_text(post_id, encoding="utf-8")


def parse_post_url(url):
    parsed = urlparse(url)
    if parsed.hostname not in {"x.com", "www.x.com", "twitter.com", "www.twitter.com"}:
        return None

    match = POST_PATH.fullmatch(parsed.path)
    if not match:
        return None

    post_id = match.group("id")
    return {
        "id": post_id,
        "url": f"https://x.com/{USERNAME}/status/{post_id}",
    }


def extract_yahoo_posts(document):
    match = NEXT_DATA_PATTERN.search(document)
    if not match:
        raise RuntimeError("Yahoo realtime response did not contain __NEXT_DATA__")

    try:
        page_data = json.loads(match.group(1))["props"]["pageProps"]["pageData"]
        entries = page_data["timeline"]["entry"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise RuntimeError("Yahoo realtime response format has changed") from exc

    posts = []
    seen_ids = set()
    for entry in entries:
        if str(entry.get("screenName", "")).lower() != USERNAME.lower():
            continue
        parsed = parse_post_url(str(entry.get("url", "")))
        if parsed is None or parsed["id"] in seen_ids:
            continue
        seen_ids.add(parsed["id"])
        posts.append({**parsed, "text": str(entry.get("displayTextBody", "")).strip()})

    if not posts:
        raise RuntimeError(f"Yahoo realtime returned no posts for @{USERNAME}")
    return posts


def save_diagnostics(document, error):
    DIAGNOSTICS_DIR.mkdir(exist_ok=True)
    (DIAGNOSTICS_DIR / "yahoo-realtime.html").write_text(document, encoding="utf-8")
    (DIAGNOSTICS_DIR / "error.txt").write_text(str(error), encoding="utf-8")


def get_posts():
    url = f"{YAHOO_REALTIME_URL}?{urlencode({'p': f'ID:{USERNAME}'})}"
    last_error = None
    document = ""

    for attempt in range(1, MAX_ATTEMPTS + 1):
        logger.info("Loading Yahoo realtime for @%s (attempt %d/%d)", USERNAME, attempt, MAX_ATTEMPTS)
        request = Request(url, headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
            "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
        })
        try:
            with urlopen(request, timeout=30) as response:
                document = response.read().decode("utf-8", errors="replace")
                logger.info("Loaded Yahoo realtime with HTTP %d (%d characters)", response.status, len(document))
            posts = extract_yahoo_posts(document)
            logger.info("Extracted %d posts for @%s", len(posts), USERNAME)
            return posts
        except HTTPError as exc:
            document = exc.read().decode("utf-8", errors="replace")
            last_error = RuntimeError(f"Yahoo realtime returned HTTP {exc.code}")
        except (URLError, TimeoutError, RuntimeError) as exc:
            last_error = exc

        logger.warning("Attempt %d/%d failed: %s", attempt, MAX_ATTEMPTS, last_error)
        if attempt < MAX_ATTEMPTS:
            time.sleep(RETRY_DELAY_SECONDS * attempt)

    save_diagnostics(document, last_error)
    raise RuntimeError(
        f"Failed to load @{USERNAME}'s posts from Yahoo realtime after {MAX_ATTEMPTS} attempts"
    ) from last_error


def send_mail(posts):
    escaped_username = html.escape(USERNAME)
    message_html = f"<h2>@{escaped_username} 新しい投稿</h2>"

    for p in posts:
        escaped_text = html.escape(p["text"]).replace("\n", "<br>\n")
        escaped_url = html.escape(p["url"], quote=True)
        message_html += f"""
        <hr>
        <p>{escaped_text}</p>
        <a href="{escaped_url}">
        {escaped_url}
        </a>
        """

    resend.Emails.send(
        {
            "from": "onboarding@resend.dev",
            "to": TO_EMAIL,
            "subject": f"@{USERNAME} 新着投稿 {len(posts)}件",
            "html": message_html,
        }
    )


def main():
    last_id = get_last_post_id()
    posts = get_posts()

    newest_id = max((post["id"] for post in posts), key=int)
    if last_id is None:
        save_last_post_id(newest_id)
        logger.info(
            "Initialized state at post ID %s without sending existing posts",
            newest_id,
        )
        return

    new_posts = [
        post for post in posts if int(post["id"]) > int(last_id)
    ]

    if not new_posts:
        logger.info("No new posts")
        return

    new_posts.sort(key=lambda post: int(post["id"]))
    send_mail(new_posts)
    save_last_post_id(newest_id)
    logger.info("Sent %d posts and saved state ID %s", len(new_posts), newest_id)


if __name__ == "__main__":
    main()
