# IRaMuTeQ Corpus Builder

A Streamlit application for preparing textual corpora for **IRaMuTeQ** from three types of input:

1. **MediaCloud** CSV exports
2. **CrowdTangle** CSV exports
3. **Any CSV**, with user-defined text and metadata columns

The application is designed as a practical research utility: it keeps the source-specific processing rules separate while providing a common interface for inspection, preview, validation, duplicate control, and export.

---

## 1. What the application does

### MediaCloud

The **MediaCloud** mode preserves the extraction workflow of the original MediaCloud → IRaMuTeQ application supplied for this project.

In particular, it keeps the original mechanisms for:

- reading MediaCloud exports;
- requiring `media_name`, `publish_date`, and `url`;
- selecting media sources;
- National / Regional Press classification for the Greek corpus;
- custom metadata fields;
- URL cleaning and validation;
- webpage requests and article-text extraction;
- minimum article-length filtering;
- request delays;
- duplicate URL control;
- failure logging;
- publication statistics;
- `rawnb` traceability back to the input CSV;
- background extraction and cancellation;
- corpus and diagnostic downloads.

The original MediaCloud-only application is also still available online:

**https://mediacloud-iramuteq.streamlit.app/**

### CrowdTangle

CrowdTangle exports can have changing column names. The application therefore **does not depend on one fixed header layout**.

It suggests likely columns for:

- post/message text;
- page or group name;
- post date;
- page/group description.

The user can override those suggestions.

The CrowdTangle cleaning follows the supplied Lucie Loubère scripts as the reference workflow, including:

- replacement of `*` with `_` in text and descriptions;
- sanitisation of page/group values;
- optional inclusion of the page/group description;
- extraction of year and year-month from a mapped date;
- duplicate-record control;
- `rawnb` traceability.

The CrowdTangle-specific scripts calculate interaction-related fields, but those fields are **not automatically added to the IRaMuTeQ metadata**, following the supplied scripts.

### Any CSV

The generic CSV mode makes only two structural assumptions:

- exactly **one text column** must be selected;
- at least **one metadata column** must be selected.

Column names are not hard-coded. This allows the application to work with datasets produced by other platforms or with manually prepared CSV files.

---

## 2. IRaMuTeQ format

An IRaMuTeQ corpus is organised as a sequence of documents. Each document begins with a header starting with `****`. Metadata variables are written on the same line using the `*variable_value` form. The textual content follows on the next line(s).

Example:

```text
**** *source_example *year_2025 *country_greece *rawnb_12
This is the text of the first document.

**** *source_example *year_2025 *country_france *rawnb_13
This is the text of the second document.
```

The exact metadata variables depend on the source and the columns selected by the user.

Because `*` has a structural role in IRaMuTeQ headers, text containing literal asterisks is cleaned before export in the CrowdTangle workflow and, by default, in the generic CSV workflow.

Tabs are also treated carefully because they can interfere with IRaMuTeQ metadata structure.

---

## 3. Application workflow

The general workflow is:

**Choose source → upload CSV → map/configure fields → inspect preview → build corpus → download outputs**

For MediaCloud, the original extraction workflow remains the basis of processing. For CrowdTangle and generic CSV files, the application works locally from the uploaded CSV without downloading article webpages.

---

## 4. Running locally

### Requirements

- Python 3.10 or newer is recommended.
- Internet access is required for the MediaCloud extraction mode because that mode retrieves article webpages.
- CrowdTangle and generic CSV conversion can operate from the uploaded file itself.

### Installation

Create and activate a virtual environment if desired:

```bash
python -m venv .venv
```

On macOS/Linux:

```bash
source .venv/bin/activate
```

On Windows:

```powershell
.venv\Scripts\activate
```

Install the dependencies:

```bash
pip install -r requirements.txt
```

### Start Streamlit

```bash
streamlit run app.py
```

Streamlit will display the local address in the terminal, normally something similar to:

```text
http://localhost:8501
```

---

## 5. Deploying on Streamlit Community Cloud

This folder is structured so that the repository can be deployed as a Streamlit application.

Typical deployment steps are:

1. Create a GitHub repository.
2. Put the contents of this folder in the repository root, or keep the folder as the project root.
3. Push the repository to GitHub.
4. Create a new Streamlit Community Cloud application.
5. Select the repository and branch.
6. Set the main file to:

```text
app.py
```

7. Deploy.

The required Python packages are listed in `requirements.txt`.

---

## 6. Input requirements

### MediaCloud CSV

The MediaCloud mode expects these three columns:

| Column | Purpose |
|---|---|
| `media_name` | Media source / publication name |
| `publish_date` | Publication date/time |
| `url` | Article webpage URL |

Other columns can be used as custom metadata.

### CrowdTangle CSV

There is no single mandatory set of CrowdTangle column names. The application lets the user map the available columns.

A typical export may contain fields corresponding to:

- Page Name / Group Name
- Post Created Date
- Message
- Page Description / Description
- interaction-related fields

The exact names can vary, which is why mapping is user-controlled.

### Generic CSV

Select:

- one column containing the text to analyse;
- one or more metadata columns.

The application adds `rawnb` to the generated records so that output documents can be traced to their original CSV row.

---

## 7. Output files

Depending on the selected mode, the application can produce:

- an **IRaMuTeQ `.txt` corpus**;
- a **processing/failure log**;
- MediaCloud-specific publication statistics and diagnostics.

The generated corpus is UTF-8 text and is intended to be opened/imported by IRaMuTeQ.

---

## 8. Reproducibility and traceability

The application keeps the input-row number as `rawnb` in generated records. This is especially useful when a corpus is filtered, deduplicated, or reduced during processing: a saved IRaMuTeQ document can still be associated with its original CSV record.

For MediaCloud, the application also distinguishes between the number of records initially selected and the number of articles successfully saved after URL validation, extraction, duplicate control, and text-length filtering.

---

## 9. Project structure

```text
iramuteq_streamlit/
├── app.py
├── requirements.txt
├── README.md
├── .gitignore
└── .streamlit/
    └── config.toml
```

### `app.py`

Contains the complete Streamlit application, including:

- the original MediaCloud processing workflow;
- CrowdTangle mapping and cleaning;
- generic CSV mapping and cleaning;
- IRaMuTeQ formatting;
- validation and export interfaces.

### `requirements.txt`

Lists the Python packages required to run the application.

### `.streamlit/config.toml`

Contains basic Streamlit server/browser configuration for deployment.

---

## 10. Notes on the MediaCloud mode

The MediaCloud mode is intentionally kept separate from the new CSV-based modes. Selecting **MediaCloud** is not intended to replace or simplify the original extraction logic; it exposes that workflow inside the unified application.

This separation is important because MediaCloud processing is fundamentally different from converting an already-existing CSV: it involves accessing article webpages, extracting article text, handling request failures, and recording extraction diagnostics.

---

## 11. Credits

**IRaMuTeQ Corpus Builder**  
Created by **Panos Tsimpoukis** with the help of ChatGPT.  
LERASS (UT) · PhEPoC-ST (NTUA)

The CrowdTangle cleaning logic is based on the supplied scripts authored by **Lucie Loubère**.

The MediaCloud mode is based on the original MediaCloud → IRaMuTeQ application supplied for this project.
