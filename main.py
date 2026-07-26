import os
import resend
from playwright.sync_api import sync_playwright
from datetime import datetime

USERNAME = os.environ["X_USERNAME"]
TO_EMAIL = os.environ["TO_EMAIL"]

resend.api_key = os.environ["RESEND_API_KEY"]


def get_latest_posts():
    posts = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True
        )

        page = browser.new_page()

        url = f"https://x.com/{USERNAME}"

        page.goto(
            url,
            wait_until="networkidle",
            timeout=60000
        )

        page.wait_for_timeout(5000)

        articles = page.locator("article").all()

        for article in articles[:10]:
            try:
                text = article.inner_text()

                if text:
                    posts.append(text)

            except:
                pass

        browser.close()

    return posts


def send_mail(posts):

    body = "<h2>X新着投稿</h2>"

    for post in posts:
        body += f"""
        <hr>
        <p>{post}</p>
        """

    resend.Emails.send(
        {
            "from": "onboarding@resend.dev",
            "to": TO_EMAIL,
            "subject": f"@{USERNAME} 新着投稿",
            "html": body,
        }
    )


posts = get_latest_posts()


if posts:
    send_mail(posts)
