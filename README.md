# IRaMuTeQ Corpus Construction Toolkit

A small web app that turns raw exports into a text corpus ready to import
into [IRaMuTeQ](http://www.iramuteq.org/). No coding needed — everything
happens in your browser.

## What it does

Choose one of three input sources, upload your file, and download a
ready-to-use corpus:

- **Meta Content Library** — upload a Facebook or Instagram export CSV.
  The app maps the columns and cleans the text automatically.
- **Any CSV** — upload any CSV file. You pick which column is the text
  and which column(s) are metadata, and the app builds the corpus.
- **MediaCloud** — upload a MediaCloud export CSV. The app fetches and
  cleans each article from its URL, classifies outlets as National or
  Regional press, and gives you publication statistics alongside the
  corpus.

An **Extractions manager** tab keeps a history of past runs — you can
reopen, re-download, or delete them at any time, and a MediaCloud
extraction can be paused and resumed even if you close the tab.

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

## Credits

Created by Panos Tsimpoukis, LERASS (UT) · PhEPoC-ST (NTUA).
