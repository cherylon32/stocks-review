# Stocks Review

A free news radar for an investor who doesn't want to read the news all day.

- **Reads the news every 30 minutes**: Google News searches in English and Chinese, CNBC, TechCrunch, 36氪 and the Nasdaq US IPO calendar.
- **Keeps only what matters to you**: tech, AI, fintech and economy news, worldwide. US companies in English, Chinese companies in Chinese.
- **Notifies you only when big news breaks**: a push notification on your phone (or Telegram or email). Quiet days stay quiet.
- **Dashboard for your phone**: every scored story, an IPO watch page, and a "Before you act" checklist on each big story.

**Cost: $0.** Everything runs on GitHub Actions and GitHub Pages, which are free for public repositories. The news sources and notification services are free too.

Dashboard: **https://cherylon32.github.io/stocks-review/** (it works after step 1 below).

---

## One-time setup (about 10 minutes)

### 1. Turn on the website

On GitHub, open this repository's **Settings → Pages**. Under **Build and deployment → Source**, choose **GitHub Actions**.

### 2. Get alerts on your phone (choose one or more)

All of them use **Settings → Secrets and variables → Actions → New repository secret**. Secrets stay private even though the repository is public.

**ntfy (easiest, recommended)**
1. Install the free **ntfy** app on your phone (search "ntfy" in the App Store or Google Play, or see <https://docs.ntfy.sh/subscribe/phone/>).
2. Tap **+** and subscribe to a topic name nobody could guess, e.g. `stocks-review-k7f2q9xw`. Anyone who knows the name could read your alerts, so make it random.
3. Add a secret named `NTFY_TOPIC` whose value is that topic name.

**Telegram**
1. In Telegram, message **@BotFather**, send `/newbot`, and copy the token it gives you into a secret named `TELEGRAM_BOT_TOKEN`.
2. Send any message to your new bot, then open `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser and find `"chat":{"id":123456789`. Put that number in a secret named `TELEGRAM_CHAT_ID`.

**Email (Gmail)**
1. Turn on 2-Step Verification for your Google account, then create an **App password** at <https://myaccount.google.com/apppasswords>.
2. Add the secret `SMTP_USER` (your Gmail address) and the secret `SMTP_PASSWORD` (the 16-character app password). Optionally add `EMAIL_TO` to send to a different address.

### 3. Test it

Open the **Actions** tab, then **Update news → Run workflow**, tick **Send a test notification**, and click **Run workflow**. A test message should reach your phone within about a minute.

The first update only records what's already in the news (so you aren't flooded on day one) and sends a "Stocks Review is running" message. Alerts start from the second update.

---

## Everyday use

- **You don't need to open anything.** When a story scores high enough, it pops up on your phone. Tap it to read the article.
- **Change your watchlist or alert sensitivity** in [`config.yml`](config.yml). You can edit it on GitHub from your phone. Raise `big_news_score` for fewer alerts, or lower it for more.
- **Track a company the bot doesn't know yet**: add it to the watchlist, or add it with its Chinese name and ticker to [`companies.yml`](companies.yml).
- The dashboard's **Guide** tab explains how stories are scored and suggests how to turn news into better decisions.

## How it works

```
every 30 min (GitHub Actions)
  ├─ fetch: Google News (EN + 中文 searches), RSS feeds, Nasdaq IPO calendar
  ├─ filter: keep tech / AI / fintech / economy news; drop clickbait
  ├─ group: same story from different outlets = one story
  ├─ score: event type + price move + company + outlet count + source  (see Guide tab)
  ├─ alert: score ≥ 8 (IPO ≥ 6), at most 4 per update / 10 per day / 2 per company per day
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
