import html
import logging
import os
import re
from pathlib import Path
from urllib.parse import urlparse

import resend
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

TO_EMAIL = os.environ["TO_EMAIL"]

resend.api_key = os.environ["RESEND_API_KEY"]

raw_usernames = os.environ.get("X_USERNAMES", os.environ.get("X_USERNAME", ""))
USERNAMES = list(
    dict.fromkeys(
        username.strip().lstrip("@")
        for username in raw_usernames.split(",")
        if username.strip()
    )
)

STATE_DIR = Path("state")
LEGACY_LAST_FILE = Path("last_post.txt")
MAX_ATTEMPTS = 3
RETRY_DELAY_MS = 3000
MAX_POSTS = 100
MAX_SCROLLS = 25
SCROLL_WAIT_MS = 1500
MAX_STAGNANT_SCROLLS = 3

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

if not USERNAMES:
    raise ValueError("X_USERNAMES must contain at least one X username")

for username in USERNAMES:
    if not re.fullmatch(r"[A-Za-z0-9_]{1,15}", username):
        raise ValueError(f"Invalid X username: {username!r}")


def state_file(username):
    return STATE_DIR / f"{username.lower()}.txt"


def get_last_post_id(username):
    path = state_file(username)
    using_legacy_state = False

    if path.exists():
        value = path.read_text().strip()
    elif username == USERNAMES[0] and LEGACY_LAST_FILE.exists():
        value = LEGACY_LAST_FILE.read_text().strip()
        using_legacy_state = bool(value)
    else:
        return None

    if not value:
        return None
    if value.isdigit():
        if using_legacy_state:
            logger.info("Migrating legacy state for @%s", username)
        return value

    parsed = parse_post_url(value, username)
    if parsed:
        logger.info("Migrating URL state for @%s to a post ID", username)
        return parsed["id"]

    raise RuntimeError(f"Invalid state in {path}: {value!r}")


def save_last_post_id(username, post_id):
    STATE_DIR.mkdir(exist_ok=True)
    state_file(username).write_text(post_id)


def parse_post_url(url, username):
    parsed = urlparse(url)
    if parsed.hostname not in {"x.com", "www.x.com", "twitter.com", "www.twitter.com"}:
        return None

    post_path = re.compile(
        rf"^/{re.escape(username)}/status/(?P<id>\d+)(?:/.*)?$",
        re.IGNORECASE,
    )
    match = post_path.fullmatch(parsed.path)
    if not match:
        return None

    post_id = match.group("id")
    return {
        "id": post_id,
        "url": f"https://x.com/{username}/status/{post_id}",
    }


def extract_visible_posts(page, username):
    posts = []
    seen_ids = set()

    for article in page.locator("article").all():
        text = article.inner_text()
        links = article.locator("a").evaluate_all("(els)=>els.map(e=>e.href)")
        post_links = [parse_post_url(link, username) for link in links]
        post_links = [post for post in post_links if post is not None]

        if post_links:
            post = post_links[0]
            if post["id"] in seen_ids:
                continue
            seen_ids.add(post["id"])
            posts.append({**post, "text": text})

    return posts


def collect_posts(page, username, last_id):
    collected = {}
    stagnant_scrolls = 0

    for scroll_count in range(MAX_SCROLLS + 1):
        previous_count = len(collected)
        for post in extract_visible_posts(page, username):
            collected[post["id"]] = post

        if not collected:
            raise RuntimeError("No matching posts were found in the loaded articles")

        if last_id is None or last_id in collected:
            return list(collected.values())

        if len(collected) >= MAX_POSTS:
            raise RuntimeError(
                f"Previous post ID {last_id} was not found within {MAX_POSTS} posts; "
                "refusing to advance state"
            )

        if len(collected) == previous_count:
            stagnant_scrolls += 1
        else:
            stagnant_scrolls = 0

        if stagnant_scrolls >= MAX_STAGNANT_SCROLLS or scroll_count == MAX_SCROLLS:
            break

        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(SCROLL_WAIT_MS)

    posts = list(collected.values())
    has_newer_posts = any(int(post["id"]) > int(last_id) for post in posts)
    if has_newer_posts:
        raise RuntimeError(
            f"Previous post ID {last_id} was not found after scrolling; "
            "refusing to send an incomplete notification or advance state"
        )

    return posts


def get_posts(username, last_id):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        url = f"https://x.com/{username}"
        last_error = None

        for attempt in range(1, MAX_ATTEMPTS + 1):
            page = browser.new_page()
            try:
                logger.info("Loading @%s (attempt %d/%d)", username, attempt, MAX_ATTEMPTS)
                page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=60000,
                )
                page.locator("article").first.wait_for(state="visible", timeout=30000)
                posts = collect_posts(page, username, last_id)

                logger.info("Extracted %d posts for @%s", len(posts), username)
                return posts
            except (PlaywrightError, RuntimeError) as exc:
                last_error = exc
                logger.warning("Attempt %d/%d failed: %s", attempt, MAX_ATTEMPTS, exc)
                if attempt < MAX_ATTEMPTS:
                    page.wait_for_timeout(RETRY_DELAY_MS * attempt)
            finally:
                page.close()

        browser.close()

    raise RuntimeError(
        f"Failed to load @{username}'s posts after {MAX_ATTEMPTS} attempts"
    ) from last_error


def send_mail(username, posts):
    escaped_username = html.escape(username)
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
            "subject": f"@{username} 新着投稿 {len(posts)}件",
            "html": message_html,
        }
    )


def process_username(username):
    last_id = get_last_post_id(username)
    posts = get_posts(username, last_id)

    newest_id = max((post["id"] for post in posts), key=int)
    if last_id is None:
        save_last_post_id(username, newest_id)
        logger.info(
            "Initialized @%s at post ID %s without sending existing posts",
            username,
            newest_id,
        )
        return

    new_posts = [
        post for post in posts if int(post["id"]) > int(last_id)
    ]

    if not new_posts:
        logger.info("No new posts for @%s", username)
        save_last_post_id(username, last_id)
        return

    new_posts.sort(key=lambda post: int(post["id"]))
    send_mail(username, new_posts)
    save_last_post_id(username, newest_id)
    logger.info(
        "Sent %d posts for @%s and saved state ID %s",
        len(new_posts),
        username,
        newest_id,
    )


def main():
    failures = []

    for username in USERNAMES:
        try:
            process_username(username)
        except Exception as exc:
            logger.exception("Failed to process @%s", username)
            failures.append((username, exc))

    if failures:
        failed_usernames = ", ".join(f"@{username}" for username, _ in failures)
        raise RuntimeError(f"Failed to process: {failed_usernames}")


if __name__ == "__main__":
    main()
