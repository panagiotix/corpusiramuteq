# IRaMuTeQ Corpus Construction Toolkit

A small web app that turns raw exports into a text corpus ready to import
into [IRaMuTeQ](http://www.iramuteq.org/). No coding needed — everything
happens in your browser.

## What it does

Choose one of four input sources and download a ready-to-use corpus:

- **Meta Content Library** — upload a Facebook or Instagram export CSV.
  The app maps the columns and cleans the text automatically.
- **Any CSV** — upload any CSV file. You pick which column is the text
  and which column(s) are metadata, and the app builds the corpus.
- **MediaCloud** — upload a MediaCloud export CSV. The app fetches and
  cleans each article from its URL, classifies outlets as National or
  Regional press, and gives you publication statistics alongside the
  corpus.
- **TikTok (Research API)** — enter your own TikTok Research API
  credentials (client key and secret of an approved research project),
  search videos by keyword and hashtag (combined with AND / OR), account,
  region and date, and build a corpus from their descriptions and/or speech
  transcripts. Metadata:
  `*date_2026-09-14 *year_2026 *month_202609 *region_fr *texttype_description`,
  plus optional account, engagement (views/likes/comments/shares in thirds of the
  corpus: low/medium/high) and video ID. Long date ranges are split into
  30-day windows; if the daily API quota is reached the extraction pauses
  and can be resumed later from where it stopped. Your credentials are
  kept only in your browser session and in memory — never saved to disk.
  The raw data is also downloadable as CSV.

An **Extractions manager** tab keeps a history of past runs — you can
reopen, re-download, or delete them at any time, and MediaCloud and TikTok
extractions can be paused and resumed even if you close the tab.

## Installing and running the app

The app runs in Docker, so it works the same way on Windows, Mac, and
Linux. See **[DOCKER.md](DOCKER.md)** for a full step-by-step guide,
written for people with no Docker experience — it covers installing
Docker, downloading the project, building and running the app, updating
it later, and troubleshooting.

Once it's running, open your browser to:

```
http://localhost:8501
```

## Versions

The current version is shown in the app's sidebar and on the landing page.
What changed in each version is listed in **[CHANGELOG.md](CHANGELOG.md)**,
and each version is published as a
[GitHub release](https://github.com/panagiotix/corpusiramuteq/releases).
To update, download the latest release and rebuild the app as described in
[DOCKER.md](DOCKER.md).

## Credits

Created by Panos Tsimpoukis, LERASS (UT) · PhEPoC-ST (NTUA).

To cite the tool, use the **Copy citation** button on the landing page.
