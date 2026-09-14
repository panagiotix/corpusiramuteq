# IRaMuTeQ Corpus Construction

A Docker app to build text corpora formatted for **IRaMuTeQ** from three
different input sources:

- **CrowdTangle** — a CrowdTangle CSV export, with flexible column mapping
  and CrowdTangle-specific text cleaning.
- **Any CSV** — any CSV file, where you pick one text column and at least
  one metadata column yourself.
- **MediaCloud** — a MediaCloud CSV export, where the article text is
  fetched from each URL and cleaned automatically, with National/Regional
  press classification and publication statistics.

You choose the input source once on the landing page, and the app takes you
straight into the matching pipeline.

## Running it

This project runs as a **Docker container** — see
**[DOCKER.md](DOCKER.md)** for the full step-by-step guide (Windows, Mac,
and Linux, written for people with no Docker experience).

Quick version, once [Docker](https://www.docker.com/products/docker-desktop/)
is installed and running:

```bash
git clone https://github.com/panagiotix/corpusiramuteq.git
cd corpusiramuteq
docker build -t iramuteq-toolkit .
docker run -d --name iramuteq-toolkit -p 8501:8501 --restart unless-stopped iramuteq-toolkit
```

Then open **http://localhost:8501** in your browser.

## Usage

1. On the landing page, choose an input source: **CrowdTangle**, **Any CSV**,
   or **MediaCloud**.
2. Upload the corresponding CSV file.
3. Follow the on-screen steps for that source (column mapping, metadata
   selection, cleaning options, etc.) and preview the resulting IRaMuTeQ
   header/text before building the corpus.
4. Build the corpus and download:
   - the IRaMuTeQ corpus (`.txt`)
   - a processing/failure log
   - (MediaCloud only) publication statistics tables and an interactive
     initial-vs-saved chart

You can switch input source at any time from the "← Change input source"
button in the sidebar.

## IRaMuTeQ text format

Each document in the exported corpus begins with a line starting with
`****`. Metadata variables are written as `*variable_value`, followed by
the document text on the next line, for example:

```
**** *source_example *year_2025 *country_greece *rawnb_12
This is the text of the first document.

**** *source_example *year_2025 *country_france *rawnb_13
This is the text of the second document.
```

The exact metadata variables produced depend on the input source and the
columns you select.

## Notes on the MediaCloud pipeline

- Article extraction runs in a background thread so the interface stays
  responsive; progress is shown live and can be cancelled at any time.
- Sources are classified as National or Regional press using built-in
  lists; unclassified sources can still be included in the corpus. When
  classification is on, sources are grouped by National/Regional/
  Unclassified in the selection step to make choosing them easier.
- Publication statistics compare initial CSV records to the articles
  actually saved in the corpus, both broken down by year and media.

## Project structure

```
app.py               Main application (all three pipelines)
requirements.txt     Python dependencies (used inside the Docker build)
Dockerfile           Docker image definition
.dockerignore        Files excluded from the Docker build
DOCKER.md            Step-by-step Docker guide (Windows/Mac/Linux)
README.md            This file
```

## Credits

Created by **Panos Tsimpoukis**, LERASS (UT) · PhEPoC-ST (NTUA)
