# twitter-mail-notifier

Yahoo!リアルタイム検索から特定のXアカウントの公開投稿を取得し、
新着投稿をResendでメール通知します。GitHub Actionsから1日1回実行されます。

## GitHub Actions Secrets

- `X_USERNAME`: 監視対象のXユーザー名
- `RESEND_API_KEY`: Resend APIキー
- `TO_EMAIL`: 通知先メールアドレス
