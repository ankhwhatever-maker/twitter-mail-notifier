import os
import resend
from datetime import datetime

resend.api_key = os.environ["RESEND_API_KEY"]

def send_mail(message):
    resend.Emails.send(
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
