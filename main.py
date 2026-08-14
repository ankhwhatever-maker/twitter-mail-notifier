import html
import logging
import os
import re
from urllib.parse import urlparse

import resend
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

USERNAME = os.environ["X_USERNAME"].lstrip("@")
TO_EMAIL = os.environ["TO_EMAIL"]

resend.api_key = os.environ["RESEND_API_KEY"]

LAST_FILE = "last_post.txt"
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

if not re.fullmatch(r"[A-Za-z0-9_]{1,15}", USERNAME):
    raise ValueError("X_USERNAME must be a valid X username")

POST_PATH = re.compile(
    rf"^/{re.escape(USERNAME)}/status/(?P<id>\d+)(?:/.*)?$",
    re.IGNORECASE,
)


def get_last_post_id():
    if os.path.exists(LAST_FILE):
        with open(LAST_FILE, "r") as f:
            value = f.read().strip()

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
    with open(LAST_FILE, "w") as f:
        f.write(post_id)


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


def extract_visible_posts(page):
    posts = []
    seen_ids = set()

    for article in page.locator("article").all():
        text = article.inner_text()
        links = article.locator("a").evaluate_all("(els)=>els.map(e=>e.href)")
        post_links = [parse_post_url(link) for link in links]
        post_links = [post for post in post_links if post is not None]

        if post_links:
            post = post_links[0]
            if post["id"] in seen_ids:
                continue
            seen_ids.add(post["id"])
            posts.append({**post, "text": text})

    return posts


def collect_posts(page, last_id):
    collected = {}
    stagnant_scrolls = 0

    for scroll_count in range(MAX_SCROLLS + 1):
        previous_count = len(collected)
        for post in extract_visible_posts(page):
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


def get_posts(last_id):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        url = f"https://x.com/{USERNAME}"
        last_error = None

        for attempt in range(1, MAX_ATTEMPTS + 1):
            page = browser.new_page()
            try:
                logger.info("Loading @%s (attempt %d/%d)", USERNAME, attempt, MAX_ATTEMPTS)
                page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=60000,
                )
                page.locator("article").first.wait_for(state="visible", timeout=30000)
                posts = collect_posts(page, last_id)

                logger.info("Extracted %d posts for @%s", len(posts), USERNAME)
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
        f"Failed to load @{USERNAME}'s posts after {MAX_ATTEMPTS} attempts"
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
    posts = get_posts(last_id)

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
        save_last_post_id(last_id)
        return

    new_posts.sort(key=lambda post: int(post["id"]))
    send_mail(new_posts)
    save_last_post_id(newest_id)
    logger.info("Sent %d posts and saved state ID %s", len(new_posts), newest_id)


if __name__ == "__main__":
    main()
