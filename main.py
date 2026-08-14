import logging
import os
import re
from urllib.parse import urlparse

import resend
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

USERNAME = os.environ["X_USERNAME"].lstrip("@")
TO_EMAIL = os.environ["TO_EMAIL"]

resend.api_key = os.environ["RESEND_API_KEY"]

LAST_FILE = "last_post.txt"

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


def get_last_post():
    if os.path.exists(LAST_FILE):
        with open(LAST_FILE, "r") as f:
            return f.read().strip()
    return ""


def save_last_post(post_url):
    with open(LAST_FILE, "w") as f:
        f.write(post_url)


def canonical_post_url(url):
    parsed = urlparse(url)
    if parsed.hostname not in {"x.com", "www.x.com", "twitter.com", "www.twitter.com"}:
        return None

    match = POST_PATH.fullmatch(parsed.path)
    if not match:
        return None

    return f"https://x.com/{USERNAME}/status/{match.group('id')}"


def get_posts():

    posts = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        page = browser.new_page()

        url = f"https://x.com/{USERNAME}"

        try:
            page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=60000,
            )
            page.locator("article").first.wait_for(state="visible", timeout=30000)
        except PlaywrightTimeoutError as exc:
            raise RuntimeError(f"Timed out while loading @{USERNAME}'s posts") from exc

        articles = page.locator("article").all()
        seen_urls = set()

        for article in articles[:10]:
            try:
                text = article.inner_text()

                links = article.locator("a").evaluate_all(
                    "(els)=>els.map(e=>e.href)"
                )

                post_links = [canonical_post_url(link) for link in links]
                post_links = [link for link in post_links if link is not None]

                if post_links:
                    post_url = post_links[0]
                    if post_url in seen_urls:
                        continue
                    seen_urls.add(post_url)
                    posts.append(
                        {
                            "url": post_url,
                            "text": text,
                        }
                    )

            except Exception:
                logger.exception("Failed to parse an article; continuing with the others")

        browser.close()

    if not posts:
        raise RuntimeError(
            f"No posts for @{USERNAME} could be extracted; X may have changed its page"
        )

    logger.info("Extracted %d posts for @%s", len(posts), USERNAME)
    return posts


def send_mail(posts):

    html = f"<h2>@{USERNAME} 新しい投稿</h2>"

    for p in posts:
        html += f"""
        <hr>
        <p>{p['text']}</p>
        <a href="{p['url']}">
        {p['url']}
        </a>
        """

    resend.Emails.send(
        {
            "from": "onboarding@resend.dev",
            "to": TO_EMAIL,
            "subject": f"@{USERNAME} 新着投稿 {len(posts)}件",
            "html": html,
        }
    )


def main():
    last = get_last_post()
    posts = get_posts()
    new_posts = []

    for post in posts:
        if post["url"] == last:
            break
        new_posts.append(post)

    if not new_posts:
        logger.info("No new posts")
        return

    new_posts.reverse()
    send_mail(new_posts)
    save_last_post(posts[0]["url"])
    logger.info("Sent %d posts and saved state %s", len(new_posts), posts[0]["url"])


if __name__ == "__main__":
    main()
