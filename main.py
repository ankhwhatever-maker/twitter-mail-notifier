import os
import resend
from playwright.sync_api import sync_playwright

USERNAME = os.environ["X_USERNAME"]
TO_EMAIL = os.environ["TO_EMAIL"]

resend.api_key = os.environ["RESEND_API_KEY"]

LAST_FILE = "last_post.txt"


def get_last_post():
    if os.path.exists(LAST_FILE):
        with open(LAST_FILE, "r") as f:
            return f.read().strip()
    return ""


def save_last_post(post_url):
    with open(LAST_FILE, "w") as f:
        f.write(post_url)


def get_posts():

    posts = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        page = browser.new_page()

        url = f"https://x.com/{USERNAME}"

        page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=60000
        )

        page.wait_for_timeout(10000)

        articles = page.locator("article").all()

        for article in articles[:10]:
            try:
                text = article.inner_text()

                links = article.locator("a").evaluate_all(
                    "(els)=>els.map(e=>e.href)"
                )

                post_links = [
                    x for x in links
                    if "/status/" in x
                ]

                if post_links:
                    posts.append(
                        {
                            "url": post_links[-1],
                            "text": text
                        }
                    )

            except:
                pass

        browser.close()

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


last = get_last_post()

posts = get_posts()

new_posts = []

for p in posts:
    if p["url"] == last:
        break

    new_posts.append(p)


if new_posts:

    new_posts.reverse()

    send_mail(new_posts)

    save_last_post(
        posts[0]["url"]
    )
