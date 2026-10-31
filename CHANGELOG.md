# Changelog

All notable changes to the IRaMuTeQ corpus construction toolkit.
Versions follow `MAJOR.MINOR.PATCH`: **PATCH** = bug fixes, **MINOR** = new
features, **MAJOR** = changes that break existing habits or file formats.
The running version is shown in the app's sidebar and in the credit line of
the landing page, and every extraction records the version that produced it.

## 1.2.0 — 2026-10-31

### Added
- **Update notice** on the landing page (bottom-right): when a newer feature
  version is published on GitHub (1.2 → 1.3, 1 → 2), the app shows what's
  new, the update steps from DOCKER.md with a **Copy commands** button, and a
  link to the release notes. Fix-only releases (1.2.1) don't trigger it.
  The notice can be dismissed; it comes back for the next version.
  The check runs at most every 6 hours and stays silent when offline.

## 1.1.0 — 2026-10-31

### Added
- **New input source: TikTok Research API.** Each user enters their own
  client key and secret (kept in memory only, never saved). Search by
  keywords and hashtags, combined with AND / OR, restricted by accounts,
  regions and dates, with excluded keywords. A plain-language reading of
  the query is shown before launch.
- TikTok corpus built from video descriptions, speech transcripts, or both.
  Metadata: `*date_`, `*year_`, `*month_` (`*month_202609`), `*region_`,
  `*texttype_` (description / transcript / both), and optionally `*user_`,
  `*videoid_` and engagement variables (`*views_`, `*likes_`, `*comments_`,
  `*shares_`: low / medium / high = thirds of the corpus; the cut-off values
  are written in the summary log).
- Long TikTok extractions: 30-day windows, automatic token renewal, retries,
  pause when the daily quota is reached and resume from the last saved page,
  recovery after an app restart. Raw data downloadable as CSV.
- Clickable credit with a **Copy citation** button on the landing page.
- Version number shown in the sidebar, in the credit line, in the copied
  citation, in the summary logs and for each run in the Extractions manager.

### Changed
- The TikTok and MediaCloud pages share the same visual style.
- After launching an extraction, the user is taken to the Extractions manager.

## 1.0.0 — initial version (not tagged)

- Input sources: Meta Content Library (Facebook / Instagram exports),
  any CSV, MediaCloud (article text fetched from URLs, National / Regional
  press classification, publication statistics).
- Extractions manager: history of runs, re-download, rename, delete;
  MediaCloud pause / resume.
