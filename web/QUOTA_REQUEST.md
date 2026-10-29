# Asking YouTube for more API quota (and permission for Viral Radar's own metrics)

Every Google Cloud project gets 10,000 YouTube Data API units a day, and about 100 searches. Apply for more, and for the analytics permission below, through YouTube's **Audit and Quota Extension Form**:
https://support.google.com/youtube/contact/yt_api_form

The review checks that the app follows the YouTube API Services Terms of Service and Developer Policies. It usually takes a few weeks, and Google may ask follow-up questions by email. Apply separately for each Google Cloud project whose key needs more: the site's search key (`YT_KEY` in the server worker, or `BROWSER_KEY` if the worker is not set up) and the scanner's key (`YT_API_KEY`) should live in different projects.

## The one choice that matters most: "Analytics & Reporting"

YouTube's Developer Policies (section III.E.4, checked on 29 October 2026) forbid using API data "to create new or derived data or metrics", and give "a score that factors in likes, total views, or any other API Data" as an example. Viral Radar's scores, "× usual" multipliers, rankings, tags and trend lifts are exactly that.

Since 1 June 2026 there is a way to do this legitimately: section III.L of the policies and the page "Additional policies for derived metrics and data storage" (https://developers.google.com/youtube/terms/derived-metrics-policy) allow analytics apps to create these metrics **after accepting an amendment through this form**. In the form, go to **Section 5: Use Cases, API Integration, and Feature Implementation** and choose **"Analytics & Reporting"** as the use case. Do this even if you don't need more quota yet: until it is accepted, the site's own metrics are outside the policies.

How Viral Radar's metrics fit the allowed categories on that page:

| Viral Radar shows | Allowed category |
|---|---|
| Channel score (0–100) and its parts, "× usual" for each Short, "views compared with subscribers", typical views (median), hit rate, momentum, views per hour | Custom Channel Scores and Ratios |
| "Top picks, ranked", the channel list sorted by score, the weekly chart, comparisons of up to 3 channels, growth charts over time | Gamification and Leaderboards |
| Topics (animals, games, facts…), tags such as "Rising", "Consistent engine", "One-hit wonder", rising words and hashtags, games and cartoons boards | Content Categorization and Tagging |
| Clip moments found from viewers' timestamped comments | Viewer Sentiment Analysis (comment analysis) |

The page also requires that these metrics are kept apart from YouTube's own numbers. Viral Radar says so on every page (footer), on the Today page and on the Trends page: "Scores, multipliers, trends and tags are Viral Radar's own calculations from YouTube's public numbers. YouTube doesn't publish them." YouTube's own counts (views, likes, comments, subscribers) are always shown unchanged.

Once accepted, statistics (views, likes, subscriber and comment counts) and the derived metrics may be kept for up to 36 months. Set the repository variable `STATS_DAYS` to the number of days you want (at most 1095); until then the scanner keeps them for 30 days. Titles, channel names, descriptions and comment text stay on the 30-day refresh either way.

Two points a reviewer may ask about, with suggested answers:
- **Data aggregation (III.E.2):** trend lists count words in the titles of many channels' Shorts. Describe them as content tagging and leaderboards for creators (the categories above), not as insight into YouTube's usage or business.
- **Search results (III.C):** the search page re-orders the channels found by YouTube search by Viral Radar's score. Titles, thumbnails and numbers from YouTube are shown unchanged and nothing from other sources is mixed in; the ordering is one of the derived metrics above.

Below are answers you can adapt. Replace anything in [brackets].

## About the app

- **App name:** Viral Radar
- **Website:** https://sparyryan-collab.github.io/outlier-radar/ [or your own domain once it is live]
- **Terms of service:** [site address]#terms
- **Privacy policy:** [site address]#privacy
- **Use case:** Analytics & Reporting (creator tools)
- **What it does:** Viral Radar helps small YouTube Shorts creators learn what works. It follows public channels and Shorts, scores each channel and compares each Short's views with its own channel's usual views, and shows creators which ideas, topics and formats are growing fast, with links that play the original videos on YouTube.
- **Who uses it:** independent creators, mostly in Thailand and the USA, on the web and as an installed web app.
- **Monetisation:** free while in beta; an optional Pro subscription is planned for the analysis tools (full trend lists, alerts, niche finder, title checker, exports). YouTube's own data is shown to everyone; Pro pays for the analysis built on top.

## How the API is used

| Part | Endpoints | Units per day (now) | Units per day (requested) |
|---|---|---|---|
| Scanner (re-checks tracked channels in small batches through the day) | channels.list, playlistItems.list, videos.list, channelSections.list | at most 8,000 | [30,000] |
| Trending charts and trend research (Thailand and USA, 12 categories, every 3 hours) | videos.list (chart=mostPopular), search.list, commentThreads.list | at most 1,200 | about 1,200 |
| Visitor searches (when someone searches a topic) | search.list, videos.list, channels.list, playlistItems.list | at most 90 searches | [1,000 searches] |
| "My channel" and "Add a channel" (visitor pastes a channel) | channels.list, playlistItems.list, videos.list | small | small |

How the site keeps usage low: visitor searches go through the site's own server, which allows 5 searches a day per visitor (15 when signed in) and 90 a day for the whole site; the server caches answers (search results for 6 hours, other lookups for 15 minutes); finished searches are shared with every visitor for 24 hours, so a popular topic costs one search a day, not one per person; channels are re-checked on a schedule rather than on every page view; and the scanner spreads a fixed daily budget across its runs and stops for the day when it is used.

## Compliance points reviewers look for

- Videos play only in YouTube's own embedded player, unchanged, in privacy-enhanced mode (youtube-nocookie.com), and every video and channel links back to YouTube. Videos start only when the visitor chooses to play one.
- **Made for Kids:** the Made for Kids status of every video the site embeds is looked up through the API (videos.list, part `status`) and shown in the player. The site has no analytics, advertising or tracking code, so nothing tracks playback of any video, made for kids or not.
- The site never downloads or stores video or audio files. Everything it shows comes from the YouTube Data API: public numbers (views, likes, comment counts, subscriber counts), titles, video IDs, upload dates, lengths, the embedded player's shape and the Made for Kids flag. How long things are kept:
  - Channel and video data is refreshed on the regular scans. A channel that has not been refreshed through the API for 30 days is deleted, and channels that drop off the list are forgotten within 30 days.
  - Statistics history (subscriber counts and typical views over time) is kept for 30 days, or up to 36 months once the Analytics & Reporting amendment is accepted (`STATS_DAYS`).
  - Channel profile pictures are copied so pages load quickly. Each copy is downloaded again at least every 25 days and deleted when its channel leaves the list. Video thumbnails load straight from YouTube and are never copied.
  - Trend research keeps chart history for 14 days, researched creators for 7 days and creators who invite clippers for 28 days.
  - Shared search results expire after 24 hours. The server's cache keeps an answer for at most 6 hours.
- Viral Radar's own metrics are labelled as its own calculations (see above); YouTube's counts are shown unchanged.
- The page says Viral Radar is independent and not made by or affiliated with YouTube or Google (footer and terms).
- The privacy policy says the site uses YouTube API Services, links to Google's Privacy Policy, explains what is stored on the device and in Firestore, the embedded player, alerts, the AI writer and daily limits, and how to ask for deletion (within 7 days; deleting Viral Radar's data doesn't change anything on YouTube). The terms link to YouTube's Terms of Service and say that using the site means agreeing to them.
- The app does not ask for access to anyone's YouTube account, so it holds no Authorized Data.
- Contact for privacy questions: the contact link on the privacy page ([your contact email], or the GitHub issues page until one is set).

## What to attach

- Screenshots of the Today page, a channel page, a video playing in the embedded player, the search results page and the Trends page.
- A short screen recording of someone searching a topic and opening a result (optional, but it speeds up reviews).
