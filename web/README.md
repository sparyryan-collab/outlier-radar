# Outlier Radar on the web (always on, free, custom domain)

This puts the dashboard on the internet at an address you choose, updating by itself while your PC is off.

How it works: a static website on **GitHub Pages**, refreshed several times a day by a **GitHub Actions** job that asks YouTube's **official Data API** for fresh numbers (every 15 minutes if you add the optional server worker). There is no server to keep running and nothing sleeps. The PC app stays as it is; this is a second, hosted edition of the same dashboard.

## Cost and billing details

| Part | Cost | Payment details needed? |
|---|---|---|
| GitHub account, Pages hosting, scheduled jobs (public repository) | free | No. A free GitHub account has no card on it, so it cannot be charged. GitHub says standard runners are free and unlimited for public repositories. |
| YouTube Data API key | free to use | Google's own pages say only that you need a Google account; they do not mention billing. Guides I found say you can create the key without a billing account, but one mentions a free-trial prompt, and I could not test this. **If Google insists on a card before it lets you create the key, stop there** and use the no-key option below. |
| Custom domain | **about 10-15 USD a year** | **Yes, at a domain registrar** (not GitHub or Google). Some registrars take PayPal as well as cards; check before you buy. |

If you want to pay nothing and give no payment details at all: skip the custom domain and use the free address `https://<your-github-name>.github.io/outlier-radar/` (name the repository `<your-github-name>.github.io` to get the shorter `https://<your-github-name>.github.io/`). You can add a domain later without changing anything else.

Running out of YouTube quota never bills you. YouTube just refuses further calls until the next day; extra quota can only be requested through a review form, not bought. Do not attach a Google Cloud billing account; nothing here needs one.

## What you get, and what is different from the PC app

| | PC app | Hosted site |
|---|---|---|
| Always on, your PC can be off | no | **yes** |
| Anyone can open it by link | no | **yes** (public, read-only) |
| Custom domain with HTTPS | no | **yes** |
| Data source | yt-dlp scraping, no quota | official YouTube Data API: free, with Google's default of about **100 searches and 10,000 other units a day** (it can change) |
| How fresh | every 20 min, most channels hourly | GitHub starts the scheduled job only a few times a day, so each run re-checks as many channels as the day's remaining quota allows: hot channels every few hours, the rest about daily. Each channel shows when it was last checked. The optional server worker starts the job every 15 minutes |
| Live search for any topic | yes, for you | yes, with an API key (see "Live search" below) |
| Real upload times in the Live feed | no (first-seen time) | **yes** |
| Cost | free | free (plus your domain, about 10-15 USD a year) |

Be aware: because of the API quota, the hosted site refreshes each channel less often than the PC app. It keeps the best 1,500 channels. To give it a big boost at any time, let the PC app scan for a while, then run `python web/scanner/export_extra.py` and push: the website queues those channels and fetches every number again through the official API. Nothing the PC app measured with yt-dlp is published.

## Setup (about 15 minutes)

You need: a free GitHub account, a free Google account, and (for a custom domain) a domain name.

### 1. Get a YouTube API key (free)
1. Go to https://console.cloud.google.com/ and create a project (any name).
2. APIs & Services, Library, search "YouTube Data API v3", Enable.
3. APIs & Services, Credentials, Create credentials, API key. Copy it.
4. Optional but sensible: edit the key and restrict it to "YouTube Data API v3".

Keep this key private. It goes into GitHub as a secret in step 3 and is never put in the website.

If Google asks you to set up billing or start a free trial with a card before you can go on, do not. See "Cost and billing details" above for what to do instead.

### 2. Put the project on GitHub
In this folder run `python web/scanner/make_repo_folder.py`. It creates a folder `outlier-radar-site` with exactly the files GitHub needs. Open a terminal in that folder, then:

```
git init -b main
git add .
git commit -m "Outlier Radar site"
gh auth login
gh repo create outlier-radar --public --source . --push
gh api -X POST repos/{owner}/{repo}/pages -f build_type=workflow
```

`gh auth login` opens your browser once to sign in. The repository has to be public for free GitHub Pages, so the code, your pick notes and the data are public too (it is all public YouTube information). The push itself starts one automatic run before Pages is switched on, so expect that first run to fail; step 3 runs it again properly.

### 3. Add the key and start it
```
gh secret set YT_API_KEY
gh workflow run "Refresh site" -f bootstrap=true
```
`gh secret set` asks you to paste the key (you will not see it as you paste). `bootstrap=true` is only for this very first run: there is no previous data yet, so the scanner starts from the seed's list of channels and fetches all their numbers through the API. After a few minutes your site is live at `https://<your-github-name>.github.io/outlier-radar/`. From then on it refreshes by itself several times a day.

If a run shows a red cross under the repository's Actions tab, open it: the log says what failed (the most common cause is a wrong or not yet enabled API key). The scanner never starts over from nothing by itself: if it cannot read its saved state or the live site's data, it refuses to publish and the live site keeps its last good version.

## Custom domain (for example radar.yourdomain.com)

1. **Own a domain.** If you do not, buy one from any registrar (Cloudflare Registrar, Namecheap, Porkbun, GoDaddy and so on; usually 10-15 USD a year for a .com). Nobody can buy it for you from here.
2. **Pick the address.** A subdomain such as `radar.yourdomain.com` is the simplest. A bare domain such as `yourdomain.com` also works.
3. **Add DNS records at your registrar.**
   - For a **subdomain**: one `CNAME` record, name `radar`, value `<your-github-name>.github.io` (no repository name in it).
   - For a **bare domain** (apex): four `A` records for `@` pointing to `185.199.108.153`, `185.199.109.153`, `185.199.110.153` and `185.199.111.153`. Optionally four `AAAA` records: `2606:50c0:8000::153`, `2606:50c0:8001::153`, `2606:50c0:8002::153`, `2606:50c0:8003::153`.
   - Remove other records for the same name (stray A/AAAA/CNAME records stop the HTTPS certificate from being issued). If you have a `CAA` record it must allow `letsencrypt.org`.
4. **Tell GitHub.** In the repository: Settings, Pages, Custom domain, type the domain, Save. Wait for the green "DNS check successful" tick. Then switch on **Enforce HTTPS** (it can take from a few minutes up to a day to become available).
   The same thing from the terminal: `gh api -X PUT repos/{owner}/{repo}/pages -f cname=radar.yourdomain.com`
5. **Point the scanner at the new address** so it keeps reading its own last results: `gh variable set SITE_URL --body https://radar.yourdomain.com`

The `CNAME` file is not used with this kind of deployment; the domain lives in Settings, Pages.

### A free subdomain instead of a paid domain (is-a.dev)

Services that hand out free `.tk`/`.ml` domains (Freenom) are gone; what still exists are free *subdomains*, which you do not own. One that fits this setup is **is-a.dev**, run by volunteers. From its own docs: it costs nothing, needs only a GitHub account (no card, no personal details beyond your GitHub username), and gives you `yourname.is-a.dev`. The conditions: the site must be "a personal website ... or a non-commercial project that's software development related", it must be reasonably complete, and a commercial site can be rejected. Review has no fixed timeline. I could not read their terms of service, so I cannot tell you exactly when a subdomain can be taken away.

How to apply (after your site is already live on its github.io address):
1. Fork https://github.com/is-a-dev/register on GitHub.
2. In your fork, inside the `domains` folder, create `yourname.json` (lowercase letters, numbers and single dashes) containing:
   ```json
   {
       "owner": { "username": "your-github-name" },
       "records": { "CNAME": "your-github-name.github.io" }
   }
   ```
3. Open a pull request, keep their template, and add the link to your live site and a screenshot.
4. Only after it is merged: in your repository go to Settings, Pages, Custom domain, enter `yourname.is-a.dev`, Save, then tick Enforce HTTPS when it appears. Then run `gh variable set SITE_URL --body https://yourname.is-a.dev`.

If it is declined, nothing breaks: keep using the free github.io address.

## Live search on the site (set up once, works on every device)

Typing in the search box always filters the channels already on the site. Searching **all of YouTube** uses quota, so it needs a key somewhere. The site never asks visitors for one by itself. There are two ways to switch it on; the first is safer.

**A. Through the server worker (recommended).** Visitors' searches go to your Cloudflare worker, which holds the key and enforces limits per person: 5 searches a day for visitors who aren't signed in, 15 for signed-in ones, and 90 for everyone together, so nobody can use up your quota. Set it up as described in "The server worker" below (the `YT_KEY` row), then `gh variable set API_URL --body https://viral-radar.<you>.workers.dev` and run "Refresh site". With `API_URL` set, no key is put in the page at all.

**B. A key in the page.** Create a second key that only works on your site's address and give it to the site as `BROWSER_KEY`. Website restrictions stop other websites' browsers from using it, but not scripts, which can claim to be your site. So **create this key in its own Google Cloud project**, never the scanner's: then the worst anyone can do is use up the visitors' search allowance, not stop the scanner.

1. Open https://console.cloud.google.com/, create a **new** project (for example "viral-radar-visitors"), and enable YouTube Data API v3 in it.
2. Credentials, Create credentials, API key. Copy it.
3. Click the new key to edit it:
   - Application restrictions: **Websites**. Add `https://sparyryan-collab.github.io/*`, and later your custom domain too (for example `https://viralradar.foo.ng/*`).
   - API restrictions: **Restrict key**, then tick only **YouTube Data API v3**. Save.
4. In a terminal in the repository folder: `gh variable set BROWSER_KEY` (paste the key when asked), then `gh workflow run "Refresh site"`.

How the quota is protected: Google gives each project about 100 searches a day (a bucket of their own) plus 10,000 units for everything else. The scanner uses up to 45 of its project's searches and `trends.py` up to 34 more, which is why visitors' searches must come from another project or through the worker. Each device also counts 15 searches a day, and a device's own searches reopen free for a week. Searches are shared too: with the worker, any topic someone searched in the last 6 hours is answered from the worker's cache without spending any of the site's YouTube searches, and visitors can't write to that cache. Without the worker, a topic a signed-in visitor searched in the last 24 hours opens free for everyone through Firestore. When searches run out, the search bar says when they come back (midnight Pacific time) and everything else keeps working.

Anyone who wants more can still add their own key from the How to use page; it stays in their browser.

## Google sign-in and sync (free, about 10 minutes, once)

Signing in with Google keeps your saved channels, Playbook, upload log and My channel the same on every device. It uses Google's Firebase on the free "Spark" plan: no card, and no server for you to run. Until it is set up, the site simply shows no sign-in button.

1. Open https://console.firebase.google.com/ with your Google account and click **Create a project** (or add Firebase to the Google Cloud project you already use for the API key). You can turn Google Analytics off.
2. **Build → Authentication → Get started → Sign-in method → Google → Enable**, choose your email as the support email, **Save**.
3. Still in Authentication: **Settings → Authorized domains → Add domain**: `sparyryan-collab.github.io`, and later your custom domain (for example `viralradar.foo.ng`).
4. **Build → Firestore Database → Create database**, pick a location near you (for example `asia-southeast1`), start in **production mode**, create.
5. In Firestore, open **Rules**, replace everything with the contents of `web/firestore.rules` from this repository, and click **Publish**. Check it took: the Rules tab should show `allow create: if request.auth != null && valid();` under `searches`. If the console still shows `allow read, write: if true` (test mode), everyone can read and change everything.
   What each part allows:
   - `users`: each signed-in person reads and writes only their own synced settings.
   - `searches`: anyone can read shared search results; only a signed-in person can save one, in the expected shape, and it can't be replaced until it is 20 hours old. (A signed-in person could still save made-up results, so the page stops using this collection once the server worker handles searches.)
   - `pro`: people can read only their own subscription status. Nobody can write it from the page; only the server worker can.
   - `push`: each person manages only their own alert subscription.
   - `track`: anyone can see which channels visitors asked the site to follow; a signed-in person can add a channel once.
   - Everything else (`billing`, `rate`, `ailimit`, `meta`) is closed to the page; the server worker writes those with its service account.
6. **Project settings (the gear) → General → Your apps → the `</>` (Web) button**, give it any name, skip Hosting, **Register app**. It shows a `firebaseConfig` with `apiKey: "AIza..."` and `projectId: "..."`.
7. In a terminal: `gh variable set FIREBASE_PROJECT_ID --body "<projectId>"`, then `gh variable set FIREBASE_API_KEY` (paste the apiKey when asked), then `gh workflow run "Refresh site"`.

The Firebase apiKey is meant to be in the web page (it only identifies the project); the rules in step 5 are what protect the data.

## Trends

The Trends page combines three sources:

- **Rising on Shorts**: worked out from every tracked Short. A phrase or hashtag counts as rising when at least 4 new Shorts (5 for a single word written like a name) from at least 3 channels used it in the last 3 days, far more than the 18 days before predict, with real views behind it. Small counts are pulled toward "no change", so 3 Shorts never look like a boom, and a card with fewer than 8 Shorts or 4 channels says "weak signal". Each channel only contributes its newest 24 Shorts, so a channel that posts several a day has its short history scaled up to 18 days (or is left out) instead of making every word it uses look new. Contractions and the common words of every language the scanner covers are ignored; languages written without spaces (Thai, Japanese, Chinese, Korean) count only by hashtag. Games and cartoons/characters get their own boards from curated name lists.
- **YouTube trending charts**: `web/scanner/trends.py` (run by the workflow after each scan) reads YouTube's `mostPopular` chart for Thailand and the USA in 12 categories. Each chart costs 1 unit, about 24 units every 3 hours, from the scanner's key. It keeps each video's previous rank, so the page can show what climbed and what's new.
- **News & curiosity**: Wikipedia's most-read articles yesterday (English and Thai) from the open Wikimedia pageviews API, compared with the day before.

The page and `trends.py` use the same word lists (a JSON block at the top of `index.html`) and the same rules, so a trend alert never names a trend the page doesn't show as hot. `node web/scanner/parity.mjs` checks that they agree. Everything lands in `trends.json` next to `data.json`. If a run fails, the previous feeds are kept.

## Viral Radar Pro (subscriptions)

Pro features (full trend lists, the games/cartoons/YouTube/news boards, trend alerts, the Tools page and CSV export) carry a PRO tag. By default they are free for everyone ("free while in beta"). The site works with Lemon Squeezy, Paddle and Stripe, alone or together.

Which provider? Lemon Squeezy and Paddle are "merchants of record": they sell on your behalf and handle sales tax and VAT in every country, which is the least work from Thailand. Stripe is cheaper per payment, but you handle tax yourself. One provider is enough.

1. **Create a monthly product** with your provider and copy its checkout link, with the visitor's account id added (the site replaces `{uid}` and `{email}`):
   - Lemon Squeezy: `https://YOURSTORE.lemonsqueezy.com/buy/VARIANT_ID?checkout[custom][uid]={uid}&checkout[email]={email}`
   - Stripe Payment Link: `https://buy.stripe.com/XXXX?client_reference_id={uid}&prefilled_email={email}`
   - Paddle: open the checkout with `customData: {uid}` (Paddle.js) or a hosted checkout that passes custom data.
2. **Deploy the payment worker** (`web/pay/worker.js`, a free Cloudflare Worker). It checks each webhook's signature and records Pro in Firestore `pro/<uid>` with an end date.
   - Firebase console, Project settings, Service accounts, **Generate new private key** (a JSON file; keep it secret).
   - Cloudflare dashboard, Workers & Pages, Create, Worker; paste `worker.js`, Deploy. Under Settings, Variables, add secrets: `FIREBASE_SA` (the whole JSON file) and the signing secret for each provider you use: `LEMON_SECRET`, `STRIPE_SECRET`, `PADDLE_SECRET`.
   - In the provider's webhook settings, point at the worker: `https://<worker>.workers.dev/lemonsqueezy` (subscription events), `/stripe` (checkout.session.completed, customer.subscription.updated, customer.subscription.deleted) or `/paddle` (subscription events).
   - `node web/pay/selftest.mjs` checks the worker with fake signed webhooks from all three providers.
3. **Make sure the Firestore rules include `pro`** (the full rules are in step 5 of "Google sign-in and sync" above).
   Nobody can write `pro` documents from the page; only the worker can. To give someone Pro by hand (a tester or a friend), create `pro/<their uid>` in the Firebase console with a timestamp field `until`.
4. **Set repository variables**: `PRO_PRICE` (for example `$9.99`), `CHECKOUT_URL` (from step 1), `MANAGE_URL` (optional: Stripe's customer-portal link; Lemon Squeezy sends its own portal link through the worker), `CONTACT_EMAIL` (shown on the terms, privacy and refund pages) and finally `PRO_GATE` = `1` to switch the locks on. Then run "Refresh site".

The site has terms of service, privacy and refund pages (footer links). They are a sensible starting point, not legal advice: read them, set `CONTACT_EMAIL`, and adjust the refund promise (7 days, first payment) if you want a different one.

**What is really protected.** Locks in the page alone protect nothing, because anyone can download the public data files. So once Pro is paid (`PRO_GATE` on) and the server worker's premium data is set up (the `UPLOAD_TOKEN` row below), the workflow uploads the full `trends.json` to the worker and publishes a trimmed public copy: YouTube's charts, the news lists, clip creators and their best moments, and creators who invite clippers then reach only Pro members, through `GET /api/trends`, which checks their sign-in and subscription. The AI writer and push alerts are also checked on the server. What stays readable by anyone who downloads `data.json` is everything the page works out from the public channel data: rising words, the games and cartoons boards, and the creator tools. Those need the channel data that free features use, so treat their locks as a convenience, or move them to the server later.

Before charging, read the YouTube API Services Terms of Service and Developer Policies (your app must add value of its own beyond YouTube's data, and must say it isn't made by YouTube; the footer does), and expect to apply for a higher YouTube quota once many people search.

## The server worker (fresh data every 15 minutes, trend alerts, the AI writer)

One free Cloudflare Worker, `web/pay/worker.js`, does everything the static site can't. Each part switches on only when its settings are added, so you can start with one. `node web/pay/selftest.mjs` checks all of it with fake services.

**Create it (about 10 minutes):**
1. Sign up at https://dash.cloudflare.com (free, no card). **Workers & Pages, Create, Create Worker**, name it `viral-radar`, **Deploy**, then **Edit code**: paste the whole of `web/pay/worker.js` and **Deploy** again. (Or from a terminal: `cd web/pay && npx wrangler deploy`.)
2. **Settings, Triggers, Cron Triggers, Add**: `*/15 * * * *` (every 15 minutes).
3. **Settings, Variables and secrets**: add the ones for the parts you want (mark keys as *Secret*):

| Part | Variables |
|---|---|
| Fresh data every 15 minutes (GitHub's own timer manages only a few runs a day) | `GITHUB_TOKEN`: on GitHub, Settings, Developer settings, Fine-grained tokens, Generate: only this repository, permission **Actions: Read and write**. `GITHUB_REPO`: `sparyryan-collab/outlier-radar` |
| Payments | `FIREBASE_SA` (the service account JSON) plus each provider's signing secret, see "Viral Radar Pro" |
| Trend alerts (phone and desktop notifications, even with the app closed) | Run `node web/pay/vapid.mjs` once. Put `VAPID_PUBLIC_KEY` in the worker **and** as a GitHub repository variable; `VAPID_PRIVATE_JWK` in the worker only (secret). Also `VAPID_SUBJECT` (`mailto:` your email), `SITE_URL` (the site's address) and `FIREBASE_SA` |
| AI idea writer | `ANTHROPIC_API_KEY` (from https://console.anthropic.com; usage is billed by Anthropic), `FIREBASE_SA`, `ALLOWED_ORIGIN` (the site's origin, for example `https://sparyryan-collab.github.io`), and the GitHub repository variable `AI_URL` = the worker's address (`https://viral-radar.<you>.workers.dev`). Optional: `AI_MODEL` (default `claude-opus-5-5`), `AI_DAILY` (requests per person per day, default 20), `AI_GLOBAL_DAILY` (all people together, default 300), `PRO_GATE` = `1` once Pro is paid. Counting happens before each request, so parallel requests can't slip past the limits |
| Visitors' YouTube search | `YT_KEY` (a YouTube Data API key, ideally in its own Google Cloud project; it never leaves the worker), `FIREBASE_SA`, `ALLOWED_ORIGIN`, then the GitHub repository variable `API_URL` = the worker's address. Optional limits: `SEARCH_ANON_DAILY` (5), `SEARCH_USER_DAILY` (15), `SEARCH_GLOBAL_DAILY` (90), `CALLS_HOURLY` (400 other calls per person per hour). Only the methods and parameters the page uses are passed through |
| Premium data for Pro members | A KV namespace bound to the worker as `DATA` (Workers & Pages, KV, Create; then the worker's Settings, Bindings, KV namespace, variable name `DATA`), `UPLOAD_TOKEN` (any long random string) in the worker **and** as the GitHub secret `UPLOAD_TOKEN` (`gh secret set UPLOAD_TOKEN`), `FIREBASE_SA`, and `API_URL` as above. See "What is really protected" |

After adding GitHub variables, run "Refresh site" so the page picks them up.

**Costs to know about:** Cloudflare's free plan covers this comfortably at the start, and the worker's counters use a few Firestore writes per search. The AI writer is the only part with a running cost: each request (5 ideas) is a small Claude API call, and `AI_DAILY` caps how many each person can make. Keep it a Pro feature once Pro is paid.

## Tracked channels

On the Saved page anyone can paste a channel link or @handle. A channel already in the data is simply saved. For a new one (signed-in visitors only), the page asks Firestore's `track` list to follow it: the scanner reads that list every run, scans those channels like the rest, and never drops them for ranking low. Up to 400 requested channels are followed (`MAX_TRACKED`); a channel needs at least 6 Shorts to get numbers. The scanner reads the list with `FIREBASE_PROJECT_ID` and `FIREBASE_API_KEY`, the same variables sign-in uses.

## Trend alerts

On the Trends page people track trends, then turn on alerts. While Viral Radar is open (or installed and running), the page itself notifies them when a tracked trend takes off. With the server worker's alert settings in place, alerts also arrive when the app is closed: the scanner publishes the list of hot trends in `trends.json`, and every 15 minutes the worker sends a web push to each subscriber whose tracked trend just took off (at most once every 3 days per trend).

## Affiliates

Lemon Squeezy has a built-in affiliate program: creators who recommend Viral Radar earn a share of each subscription. Turn it on in Lemon Squeezy (Affiliates), then set the repository variables `LEMON_STORE` (your store's slug; the site then loads Lemon's affiliate tracking script so `?aff=` links are credited) and `AFFILIATE_URL` (your affiliate sign-up page; it appears in the footer as "Earn with us").

## More YouTube quota

`web/QUOTA_REQUEST.md` has answers ready for YouTube's quota extension form. Apply before many people search or Pro goes paid; reviews take a few weeks.

## Install it as an app

On Android and desktop Chrome or Edge, the site offers "📲 Install the app" in the menu. On iPhone or iPad: Share, then "Add to Home Screen". The installed app opens full screen and still shows the last data when offline.

## How the data stays safe between runs

- Each run starts from the newer of two copies of the scanner's state: a private copy the workflow saves in GitHub's Actions cache (with every channel's history, the quota counters and the candidates), and the live site's `data.json`. The three newest saved copies are kept.
- If neither can be read, the scanner and `trends.py` refuse to run, nothing is written, and `web/scanner/check_site.py` stops the deploy, so the live site keeps its last good version. Only the very first run of a brand-new site uses `bootstrap=true`.
- Every number on the site comes from the official API. Old records imported from the PC app are kept private until the API has refreshed them, and dropped if it can't. A channel the API can't refresh for 30 days is dropped too, and profile pictures are fetched again within 25 days and deleted when a channel leaves the list.
- On every push the workflow first runs the self-tests (`web/scanner/selftest.py`, `web/scanner/parity.mjs`, `web/pay/selftest.mjs`); a failing test stops the deploy.

## Changing things

- **Niches to scan:** edit `web/queries.json` (each entry has `q`, and optionally `lang` and `region`, for example `th` and `TH`). Push the change and the next run picks it up.
- **Your notes on top channels:** edit `outlier-radar/picks.json` (English and Thai fields, plus `date`), push. Keep live numbers out of the notes: the page shows the current ones right beside them. Write format and opinion, and mark opinion as such ("Our read: …").
- **How fast it spends quota:** environment values in the workflow, defaults in `web/scanner/scan.py`: `DAILY_BUDGET` (8,000 units a day), `RUN_BUDGET` (the most one run may spend, 2,400), `PROBES_PER_RUN` (a cap on channels per run), `SEARCH_DAILY_CAP`, `FEATURED_PER_RUN`, `KEEP_CHANNELS`. Each run divides what is left of the day's budget by the number of runs still expected today, learned from how often GitHub actually starts them. `trends.py` keeps to its own 1,200 units a day (`TRENDS_DAILY_UNITS`).
- **Add the PC app's finds:** `python web/scanner/export_extra.py` writes `web/seed/extra.json`; push it and the next run queues those channels for the API to check.
- **Check the logic without any key or network:** `python web/scanner/selftest.py` (it runs against a fake YouTube API), `node web/scanner/parity.mjs` (the page and the scanner agree) and `node web/pay/selftest.mjs` (the server worker).

## Things to know

- GitHub starts scheduled jobs late and often skips them: in practice a few runs a day rather than every 15 minutes. Each run therefore does a bigger share of the day's work, and the server worker's 15-minute trigger is the way to get frequent runs. GitHub also switches scheduled jobs off after 60 days without repository activity; the workflow ends each run by asking GitHub to keep it enabled. If you ever see the schedule stopped, open Actions and click Enable (or push any small change).
- Google's limits reset at midnight Pacific time. Searches have their own bucket of 100 calls a day: the scanner uses at most 45 and `trends.py` at most 34, so visitors' searches must come from another project or through the worker. If the search bucket runs out, the scanner keeps re-checking channels and only stops searching. Google's published numbers are described as subject to change, so if searches start failing early, lower `SEARCH_DAILY_CAP`.
- The profile pictures are stored on the site itself (downloaded slowly, refreshed within 25 days), because hot-linking Google's image server gets rate limited. The downloader names itself honestly in its User-Agent, tries a picture that failed again only a day later, and the workflow saves the picture folder to the Actions cache only on runs that changed it (the two newest copies are kept).
- What counts as a Short: the API has no "is a Short" field, and Shorts can be up to 3 minutes long. Any upload of 3 minutes or less counts, and the scanner also records each video's player shape. Once vertical Shorts have shown that the shape is reliable (at least 80% of videos of 60 seconds or less come back vertical), horizontal short videos are left out automatically; `data.json` says whether that has happened (`orient.trusted`). Subscriber counts are rounded by YouTube.
- Scores rank patterns of demand. They do not predict results. Study the formats, then make something original.
