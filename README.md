# Stocks Review

A free news radar for an investor who doesn't want to read the news all day.

- **Reads the news every 15 minutes**: Google News searches in English and Chinese, CNBC, TechCrunch and the Nasdaq US IPO calendar.
- **Keeps only what matters to you**: tech, AI, fintech and economy news, worldwide, with extra searches for Malaysia (Bursa tech, e-wallets, digital banks, data centres, IPOs). US companies in English, Chinese companies in Chinese, Malaysian companies in both.
- **Notifies you only when big news breaks**: a push notification on your phone (or Telegram or email). Quiet days stay quiet.
- **Dashboard for your phone**: every scored story, an IPO watch page, and a "Before you act" checklist on each big story.

**Cost: $0.** Everything runs on free services. See [Will this ever cost money?](#will-this-ever-cost-money) below.

Dashboard: **https://cherylon32.github.io/stocks-review/** (it works after step 1 below).

---

## One-time setup (about 10 minutes)

### 1. Turn on the website

On GitHub, open this repository's **Settings → Pages**. Under **Build and deployment → Source**, choose **GitHub Actions**.

### 2. Get alerts (choose one or more)

All of them use **Settings → Secrets and variables → Actions → New repository secret**. Secrets stay private even though the repository is public. Alerts go to every channel you set up.

**Email (Gmail)**: each alert email has the company and headline in the subject (e.g. `🔥 NVIDIA: Nvidia shares sink as board launches probe… [Stocks Review]`), is marked high priority, and has a "Read the article" button.
1. Turn on **2-Step Verification** for your Google account (Google Account → Security).
2. Create an **App password** at <https://myaccount.google.com/apppasswords>. Name it "Stocks Review" and copy the 16-letter password. This is not your normal Gmail password, and you can delete it any time.
3. Add two secrets: `SMTP_USER` = your Gmail address, and `SMTP_PASSWORD` = the 16-letter app password.
4. Optional: add `EMAIL_TO` if alerts should go to a different address than the Gmail that sends them.
5. To make sure alerts pop up on your phone, turn on notifications in the Gmail app. In Gmail on the web, you can also create a filter for emails whose subject contains `[Stocks Review]` with **Never send it to Spam** and **Always mark it as important**.

**ntfy (phone push without email)**
1. Install the free **ntfy** app on your phone (search "ntfy" in the App Store or Google Play, or see <https://docs.ntfy.sh/subscribe/phone/>).
2. Tap **+** and subscribe to a topic name nobody could guess, e.g. `stocks-review-k7f2q9xw`. Anyone who knows the name could read your alerts, so make it random.
3. Add a secret named `NTFY_TOPIC` whose value is that topic name.

**Telegram**
1. In Telegram, message **@BotFather**, send `/newbot`, and copy the token it gives you into a secret named `TELEGRAM_BOT_TOKEN`.
2. Send any message to your new bot, then open `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser and find `"chat":{"id":123456789`. Put that number in a secret named `TELEGRAM_CHAT_ID`.

### 3. Test it

Open the **Actions** tab, then **Update news → Run workflow**, tick **Send a test notification**, and click **Run workflow**. A test email (or push) should arrive within about a minute. If the run's log says `Sending via email failed`, the app password or Gmail address is wrong.

The first update only records what's already in the news (so you aren't flooded on day one) and sends a "Stocks Review is running" message. Alerts start from the second update.

---

## Everyday use

- **You don't need to open anything.** When a story scores high enough, an email (or push) arrives with the headline in the subject. Tap **Read the article**.
- **Change your watchlist or alert sensitivity** in [`config.yml`](config.yml). You can edit it on GitHub from your phone. Raise `big_news_score` for fewer alerts, or lower it for more.
- **Track a company the bot doesn't know yet**: add it to the watchlist, or add it with its Chinese name and ticker to [`companies.yml`](companies.yml).
- The dashboard's **Guide** tab explains how stories are scored and suggests how to turn news into better decisions.

## Will this ever cost money?

No, as long as the two rules at the end of this section hold. Every piece is free:

| Part | Service | Why it's free |
|---|---|---|
| Runs every 15 minutes | GitHub Actions | Free and unlimited for **public** repositories on GitHub's standard runners |
| Dashboard website | GitHub Pages | Free for **public** repositories (personal use is far below its 100 GB/month bandwidth guideline) |
| News | Google News RSS, CNBC, TechCrunch, Nasdaq IPO calendar | Public feeds, no account or key |
| Email alerts | Your Gmail (app password) | Free; at most about 10 alerts a day, far below Gmail's ~500/day sending limit |
| Phone push (optional) | ntfy.sh / Telegram | Free, no account needed |

No AI service or paid API is used, so there are no per-use charges. To make sure you are never billed:

1. **Keep the repository public.** A private repository only gets 2,000 free Actions minutes a month (this bot would use about 3,000), and Pages for private repositories needs a paid plan.
2. **Don't add a payment method to GitHub** (Settings → Billing and plans). Without one, GitHub cannot charge you; at worst it would pause the workflow.

## How it works

```
every 15 min (GitHub Actions)
  ├─ fetch: Google News (EN + 中文 searches), RSS feeds, Nasdaq IPO calendar
  ├─ filter: keep tech / AI / fintech / economy news; drop clickbait
  ├─ group: same story from different outlets = one story
  ├─ score: event type + price move + company + outlet count + source  (see Guide tab)
  ├─ alert: score ≥ 9 (IPO ≥ 7), at most 4 per update / 10 per day / 2 per company per day
  └─ publish: dashboard on GitHub Pages; history kept on the news-data branch (14 days)
```

The code lives in `newsbot/`: `sources.py` fetches, `analyze.py` scores and picks alerts, `notify.py` sends them. The dashboard is `site/` (plain HTML, CSS and JavaScript).

## Limitations

- It reads **headlines**, not full articles (many are paywalled). The scoring uses keyword rules, so it can occasionally rank a story too high or too low. Every story links to the source.
- GitHub can start scheduled runs a few minutes late (occasionally longer at busy times), so alerts are near-real-time, not instant.
- Google News RSS is free but unofficial; if a source fails, the others keep working (see the Guide tab → Sources status).
- The IPO calendar covers **US** listings. Hong Kong, China and other IPOs come from the news.
- GitHub pauses scheduled workflows in public repositories after 60 days without any commits. If GitHub emails you about it, open the Actions tab and click **Enable workflow**.
- **This is a reading aid, not financial advice.**

## Development

```bash
pip install -r requirements-dev.txt
```

```bash
python -m pytest -q
```

```bash
python -m newsbot --data-dir state/data --no-notify
```

The last command does a real fetch and writes `state/data/news.json`. To view the dashboard locally, copy `site/` and that `data/` folder into one directory and serve it with `python -m http.server`.
