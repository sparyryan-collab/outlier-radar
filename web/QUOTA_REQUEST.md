# Asking YouTube for more API quota

Every Google Cloud project gets 10,000 YouTube Data API units a day, and about 100 searches. That is plenty while Viral Radar is small. Once many people search, or Pro is paid, apply for more through YouTube's **Audit and Quota Extension Form**:
https://support.google.com/youtube/contact/yt_api_form

The review checks that the app follows the YouTube API Services Terms of Service and Developer Policies. It usually takes a few weeks, and Google may ask follow-up questions by email. Apply separately for each Google Cloud project whose key needs more: the site's search key (`YT_KEY` in the server worker, or `BROWSER_KEY` if the worker is not set up) and the scanner's key (`YT_API_KEY`) should live in different projects.

Below are answers you can adapt. Replace anything in [brackets].

## About the app

- **App name:** Viral Radar
- **Website:** https://sparyryan-collab.github.io/outlier-radar/ [or your own domain once it is live]
- **Terms of service:** [site address]#terms
- **Privacy policy:** [site address]#privacy
- **What it does:** Viral Radar helps small YouTube Shorts creators learn what works. It follows public channels and Shorts, compares each Short's views with its own channel's usual views, and shows creators which ideas, topics and formats are growing fast, with links that play the original videos on YouTube.
- **Who uses it:** independent creators, mostly in Thailand and the USA, on the web and as an installed web app.
- **Monetisation:** free to use; an optional Pro subscription adds trend tools (trend lists, alerts, niche finder, title checker, exports). YouTube data itself is shown to everyone; Pro pays for the analysis built on top.

## How the API is used

| Part | Endpoints | Units per day (now) | Units per day (requested) |
|---|---|---|---|
| Scanner (re-checks tracked channels in small batches through the day) | channels.list, playlistItems.list, videos.list, channelSections.list | at most 8,000 | [30,000] |
| Trending charts and trend research (Thailand and USA, 12 categories, every 3 hours) | videos.list (chart=mostPopular), search.list | at most 1,200 | about 1,200 |
| Visitor searches (when someone searches a topic) | search.list, videos.list, channels.list, playlistItems.list | at most 90 searches | [1,000 searches] |
| "My channel" and "Add a channel" (visitor pastes a channel) | channels.list, playlistItems.list, videos.list | small | small |

How the site keeps usage low: visitor searches go through the site's own server, which allows 5 searches a day per visitor (15 when signed in) and 90 a day for the whole site; the server caches answers (search results for 6 hours, other lookups for 15 minutes); finished searches are shared with every visitor for 24 hours, so a popular topic costs one search a day, not one per person; channels are re-checked on a schedule rather than on every page view; and the scanner spreads a fixed daily budget across its runs and stops for the day when it is used.

## Compliance points reviewers look for

- Videos play only in YouTube's own embedded player, unchanged, and every video and channel links back to YouTube.
- The site never downloads or stores video or audio files. Everything it shows comes from the YouTube Data API: public numbers (views, likes, comment counts, subscriber counts), titles, video IDs, upload dates, lengths and the embedded player's shape. How long things are kept:
  - Channel and video data is refreshed on the regular scans. A channel that has not been refreshed through the API for 30 days is deleted, and channels that drop off the list are removed.
  - Channel profile pictures are copied so pages load quickly. Each copy is downloaded again at least every 25 days and deleted when its channel leaves the list. Video thumbnails load straight from YouTube and are never copied.
  - Shared search results expire after 24 hours. The server's cache keeps an answer for at most 6 hours.
  - [Check these against the policy's data-storage section before sending.]
- The page says Viral Radar is independent and not made by or affiliated with YouTube or Google (footer and terms).
- Numbers shown are YouTube's own counts, or simple calculations on them (for example "views compared with this channel's usual views").
- Users are told in the privacy policy that searches go through the YouTube API Services, with a link to Google's Privacy Policy, and the terms link to YouTube's Terms of Service.
- The app does not ask for access to anyone's YouTube account.

## What to attach

- Screenshots of the home page, a channel page, a video page with the embedded player, the search results page and the Trends page.
- A short screen recording of someone searching a topic and opening a result (optional, but it speeds up reviews).
