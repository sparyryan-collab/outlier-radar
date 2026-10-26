# Outlier Radar on the web (always on, free, custom domain)

This puts the dashboard on the internet at an address you choose, updating by itself while your PC is off.

How it works: a static website on **GitHub Pages**, refreshed about every 15 minutes by a **GitHub Actions** job that asks YouTube's **official Data API** for fresh numbers. There is no server to keep running and nothing sleeps. The PC app stays as it is; this is a second, hosted edition of the same dashboard.

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
| How fresh | every 20 min, most channels hourly | about every 15 min (GitHub sometimes skips scheduled runs at busy times); each run re-checks up to 40 channels, looks up related channels of 3 top creators, and runs one discovery search (up to 45 a day, spread out) |
| Live search for any topic | yes, for you | yes, with an API key (see "Live search" below) |
| Real upload times in the Live feed | no (first-seen time) | **yes** |
| Cost | free | free (plus your domain, about 10-15 USD a year) |

Be aware: because of the API quota, the hosted site refreshes each channel less often than the PC app. It keeps the best 1,500 channels. To give it a big boost at any time, let the PC app scan for a while, then run `python web/scanner/export_extra.py` and push: the website merges those channels once and keeps them fresh from then on.

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
gh workflow run "Refresh site"
```
`gh secret set` asks you to paste the key (you will not see it as you paste). After about two minutes your site is live at `https://<your-github-name>.github.io/outlier-radar/`. From then on it refreshes by itself, about every 15 minutes.

If the first run shows a red cross under the repository's Actions tab, open it: the log says what failed (the most common cause is a wrong or not yet enabled API key). The site still publishes the starter data meanwhile.

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

Typing in the search box always filters the channels already on the site. Searching **all of YouTube** needs an API key, because every search uses quota. The site never asks visitors for one by itself.

**One-time setup, so it works on every phone and computer with no sign-in:** create a second key that only works on your site's address and give it to the site as `BROWSER_KEY`. A key locked to your address is designed to sit in a web page. It is useless on any other site, and it never touches your scanner secret.

1. Open https://console.cloud.google.com/apis/credentials (the same Google project as before is fine).
2. Create credentials, then API key. Copy it.
3. Click the new key to edit it:
   - Application restrictions: **Websites**. Add `https://sparyryan-collab.github.io/*`, and later your custom domain too (for example `https://viralradar.foo.ng/*`).
   - API restrictions: **Restrict key**, then tick only **YouTube Data API v3**. Save.
4. In a terminal in the repository folder: `gh variable set BROWSER_KEY` (paste the key when asked), then `gh workflow run "Refresh site"`.

After that run, everyone gets a "Search all of YouTube" button and Enter searches YouTube directly.

How the quota is protected: each device gets 15 searches a day with the site's key; a device's own searches reopen free for a week, and any topic someone searched in the last 24 hours opens free for everyone (shared through Firestore, see the rules below). When the project's roughly 100 daily searches run out, the search bar says when they come back (midnight Pacific time) and everything else keeps working. Keep this key in a different Google Cloud project from the scanner's key, so visitors can never use up the scanner's quota.

Anyone who wants more can still add their own key from the How to use page; it stays in their browser.

## Google sign-in and sync (free, about 10 minutes, once)

Signing in with Google keeps your saved channels, Playbook, upload log and My channel the same on every device. It uses Google's Firebase on the free "Spark" plan: no card, and no server for you to run. Until it is set up, the site simply shows no sign-in button.

1. Open https://console.firebase.google.com/ with your Google account and click **Create a project** (or add Firebase to the Google Cloud project you already use for the API key). You can turn Google Analytics off.
2. **Build → Authentication → Get started → Sign-in method → Google → Enable**, choose your email as the support email, **Save**.
3. Still in Authentication: **Settings → Authorized domains → Add domain**: `sparyryan-collab.github.io`, and later your custom domain (for example `viralradar.foo.ng`).
4. **Build → Firestore Database → Create database**, pick a location near you (for example `asia-southeast1`), start in **production mode**, create.
5. In Firestore, open **Rules**, replace everything with the rules below, and click **Publish**:
   ```
   rules_version = '2';
   service cloud.firestore {
     match /databases/{database}/documents {
       match /users/{uid} {
         allow read, write: if request.auth != null && request.auth.uid == uid;
       }
       match /searches/{id} {
         function valid() {
           let d = request.resource.data;
           return id.matches('q[0-9a-f]{40}')
             && d.keys().hasOnly(['q', 'c', 'n', 't'])
             && d.q is string && d.q.size() > 0 && d.q.size() <= 120
             && d.c is string && d.c.size() <= 800000
             && d.n is int && d.n >= 1 && d.n <= 15
             && d.t == request.time;
         }
         allow get: if true;
         allow create: if valid();
         allow update: if valid() && resource.data.t < request.time - duration.value(20, 'h');
       }
     }
   }
   ```
   This means each signed-in person can read and write only their own data, and anyone can read shared search results. A shared result can only be written in the expected shape, and can't be replaced until it is 20 hours old. The page also re-checks every shared result before showing it.
6. **Project settings (the gear) → General → Your apps → the `</>` (Web) button**, give it any name, skip Hosting, **Register app**. It shows a `firebaseConfig` with `apiKey: "AIza..."` and `projectId: "..."`.
7. In a terminal: `gh variable set FIREBASE_PROJECT_ID --body "<projectId>"`, then `gh variable set FIREBASE_API_KEY` (paste the apiKey when asked), then `gh workflow run "Refresh site"`.

The Firebase apiKey is meant to be in the web page (it only identifies the project); the rules in step 5 are what protect the data.

## Trends

The Trends page combines three sources:

- **Rising on Shorts**: worked out in the page from every tracked Short. A word, phrase or hashtag counts as rising when far more new Shorts used it in the last 3 days than in the 18 days before, from several channels, with real views behind it. Games and cartoons/characters get their own boards from curated name lists.
- **YouTube trending charts**: `web/scanner/trends.py` (run by the workflow after each scan) reads YouTube's `mostPopular` chart for Thailand and the USA in 12 categories. Each chart costs 1 unit, about 24 units every 3 hours, from the scanner's key. It keeps each video's previous rank, so the page can show what climbed and what's new.
- **News & curiosity**: Wikipedia's most-read articles yesterday (English and Thai) from the open Wikimedia pageviews API, compared with the day before.

Everything lands in `trends.json` next to `data.json`. If a run fails, the previous feeds are kept.

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
3. **Add this Firestore rule** (inside `match /databases/{database}/documents { ... }`) and publish:
   ```
   match /pro/{uid} {
     allow read: if request.auth != null && request.auth.uid == uid;
   }
   ```
   Nobody can write `pro` documents from the page; only the worker can. To give someone Pro by hand (a tester or a friend), create `pro/<their uid>` in the Firebase console with a timestamp field `until`.
4. **Set repository variables**: `PRO_PRICE` (for example `$9.99`), `CHECKOUT_URL` (from step 1), `MANAGE_URL` (optional: Stripe's customer-portal link; Lemon Squeezy sends its own portal link through the worker), `CONTACT_EMAIL` (shown on the terms, privacy and refund pages) and finally `PRO_GATE` = `1` to switch the locks on. Then run "Refresh site".

The site has terms of service, privacy and refund pages (footer links). They are a sensible starting point, not legal advice: read them, set `CONTACT_EMAIL`, and adjust the refund promise (7 days, first payment) if you want a different one.

Note: the locks run in the browser and the data files are public, so they stop casual use rather than determined copying. Before charging, read the YouTube API Services Terms of Service and Developer Policies (your app must add value of its own beyond YouTube's data, and must say it isn't made by YouTube; the footer does), and expect to apply for a higher YouTube quota once many people search.

## Install it as an app

On Android and desktop Chrome or Edge, the site offers "📲 Install the app" in the menu. On iPhone or iPad: Share, then "Add to Home Screen". The installed app opens full screen and still shows the last data when offline.

## Changing things

- **Niches to scan:** edit `web/queries.json` (each entry has `q`, and optionally `lang` and `region`, for example `th` and `TH`). Push the change and the next run picks it up.
- **Your notes on top channels:** edit `outlier-radar/picks.json` (English and Thai fields), push.
- **How fast it spends quota:** environment values in the workflow, defaults in `web/scanner/scan.py` (`PROBES_PER_RUN`, `SEARCH_DAILY_CAP`, `DAILY_BUDGET`, `FEATURED_PER_RUN`, `KEEP_CHANNELS`).
- **Add the PC app's finds:** `python web/scanner/export_extra.py` writes `web/seed/extra.json` (channels the site does not have yet) and copies their profile pictures; push it and the next run merges them.
- **Check the scanner logic without any key or network:** `python web/scanner/selftest.py` (it runs against a fake YouTube API).

## Things to know

- Scheduled GitHub jobs can start a few minutes late, and GitHub switches scheduled jobs off after 60 days without repository activity. The workflow ends each run by asking GitHub to keep it enabled, which is meant to prevent that. I could not confirm that here, so if you ever see the schedule stopped, open Actions and click Enable (or push any small change).
- Google's limits reset at midnight Pacific time. The scanner keeps itself well inside them (searches have their own bucket of 100 calls a day; the scanner uses at most 45 of them and 8,000 of the 10,000 daily units, leaving the rest for visitors); if YouTube refuses a call it stops and the site keeps showing the last good data. Google's published numbers are described as subject to change, so if searches start failing early, lower `SEARCH_DAILY_CAP`.
- The profile pictures are stored on the site itself (downloaded slowly and cached), because hot-linking Google's image server gets rate limited.
- Channel numbers come from the official API, so a few things differ from the PC app: the API cannot tell a Short from a short regular video, so any upload of 3 minutes or less counts; subscriber counts are rounded by YouTube.
- Scores rank patterns of demand. They do not predict results. Study the formats, then make something original.
- The workflow file and the page were written and tested here against a fake YouTube API and in a browser, but not run on GitHub itself or with a real API key, so the first run is the real test.
