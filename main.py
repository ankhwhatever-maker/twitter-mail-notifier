import os
import requests
from datetime import datetime
from resend import Resend

RESEND_API_KEY = os.environ["RESEND_API_KEY"]

resend = Resend(RESEND_API_KEY)

def send_mail(message):
    resend.emails.send(
        {
            "from": "onboarding@resend.dev",
            "to": "ankh.yuta2@gmail.com",
            "subject": "X通知テスト",
            "html": f"""
            <h2>X通知</h2>
            <p>{message}</p>
            <p>{datetime.now()}</p>
            """,
        }
    )

send_mail(
    "GitHub Actionsからメール送信テスト成功！"
)
