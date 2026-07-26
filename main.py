import os
import json
import resend
import snscrape.modules.twitter as sntwitter
from datetime import datetime

USERNAME = os.environ["X_USERNAME"]
TO_EMAIL = os.environ["TO_EMAIL"]

resend.api_key = os.environ["RESEND_API_KEY"]

LAST_FILE = "last_post.txt"


def get_last_id():
    if os.path.exists(LAST_FILE):
        with open(LAST_FILE, "r") as f:
            return f.read().strip()
    return "0"


def save_last_id(tweet_id):
    with open(LAST_FILE, "w") as f:
        f.write(str(tweet_id))


def get_new_tweets():
    last_id = int(get_last_id())

    tweets = []

    scraper = sntwitter.TwitterUserScraper(USERNAME)

    for tweet in scraper.get_items():
        if tweet.id <= last_id:
            break

        tweets.append(tweet)

        if len(tweets) >= 10:
            break

    return list(reversed(tweets))


def send_mail(tweets):
    body = ""

    for t in tweets:
        body += f"""
        <p>
        <b>{t.date}</b><br>
        {t.rawContent}<br>
        <a href="{t.url}">{t.url}</a>
        </p>
        <hr>
        """

    resend.Emails.send(
        {
            "from": "onboarding@resend.dev",
            "to": TO_EMAIL,
            "subject": f"@{USERNAME} 新しい投稿 {len(tweets)}件",
            "html": body,
        }
    )


tweets = get_new_tweets()

if tweets:
    send_mail(tweets)
    save_last_id(tweets[-1].id)
