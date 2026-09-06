# IRaMuTeQ Corpus Builder

A Streamlit application for preparing textual corpora for **IRaMuTeQ** from three kinds of sources:

1. **MediaCloud** CSV exports — using the original MediaCloud extraction mechanism supplied for this project.
2. **CrowdTangle** CSV exports — with flexible column mapping and cleaning based on the supplied CrowdTangle scripts.
3. **Any CSV** — with one user-selected text column and at least one user-selected metadata column.

The application is designed for reproducible corpus preparation: the input data are inspected first, the mapping and cleaning rules are visible, and the generated corpus can be downloaded together with processing diagnostics.

---

## 1. What is an IRaMuTeQ corpus?

An IRaMuTeQ corpus is a plain UTF-8 text file in which each document begins with a header line starting with `****`. Metadata variables are written after the four asterisks using the form `*variable_value`.

For example:

```text
**** *source_example *year_2025 *country_greece *rawnb_12
This is the text of the first document.

**** *source_example *year_2025 *country_france *rawnb_13
This is the text of the second document.
```

The metadata variables depend on the source and the mapping selected by the user.

### Important formatting rule

The asterisk `*` has a structural meaning in IRaMuTeQ. Consequently, text containing literal asterisks is cleaned before export in the CrowdTangle workflow and can optionally be cleaned in the generic CSV workflow.

Tabs are also treated as reserved separators and are replaced with spaces in corpus text.

---

## 2. Application workflow

The application uses a common interface while keeping the processing logic specific to each source.

### MediaCloud

The MediaCloud mode retains the extraction mechanism from the original MediaCloud → IRaMuTeQ application supplied with this project.

The workflow is:

1. Upload the MediaCloud CSV.
2. Check the required fields.
3. Select the media sources to process.
4. Optionally select additional CSV columns as metadata.
5. Review the IRaMuTeQ preview.
6. Retrieve the article pages and extract article text.
7. Remove duplicate URLs.
8. Apply the MediaCloud metadata and source classification rules.
9. Validate article length and extraction success.
10. Export the IRaMuTeQ corpus, failure log, and publication statistics.

The original MediaCloud extraction settings are retained, including:

- request delay, default **1.5 seconds**;
- minimum extracted article length, default **100 characters**;
- MediaCloud fields `media_name`, `publish_date`, and `url`;
- National / Regional press classification for Greek corpora;
- original CSV row traceability through `rawnb`;
- URL deduplication;
- extraction diagnostics and failure categories;
- cancellation during long extraction jobs;
- initial-versus-saved publication statistics.

For non-Greek corpora, the built-in Greek National / Regional classification can be disabled.

### CrowdTangle

CrowdTangle exports can have different column names depending on the export and workflow. The application therefore **suggests** likely columns but does not require one exact CrowdTangle header schema.

The user maps:

- post/message text;
- page or group name;
- post date;
- page/group description;
- optional additional metadata columns.

The cleaning follows the supplied CrowdTangle scripts, including:

- replacement of `*` in post text and descriptions with `_`;
- sanitisation of page/group names for IRaMuTeQ metadata;
- derivation of `year` and `ym` from a mapped date when possible;
- duplicate detection based on the generated record;
- preservation of the original CSV row through `rawnb`.

The supplied scripts also calculate interaction-related variables such as interactions, likes, and likes-at-posting. These are **not automatically inserted into the IRaMuTeQ header**, following the supplied scripts and their warning that such variables can cause treatment problems. They can instead be selected as optional metadata when appropriate.

### Any CSV

The generic CSV mode deliberately makes very few assumptions about the dataset.

The user must select:

- **exactly one text column**;
- **at least one metadata column**.

All selected metadata columns are represented in the IRaMuTeQ header. The application also adds `rawnb`, which records the original CSV row number and makes it possible to trace corpus documents back to their input record.

---

## 3. Input requirements

### MediaCloud

The MediaCloud CSV must contain:

```text
media_name
publish_date
url
```

Each row represents one MediaCloud article record.

### CrowdTangle

There is no single mandatory header spelling. The application detects likely fields and lets the user correct the mapping.

Typical fields include equivalents of:

```text
Page Name
Post Created Date
Message
Description / Page Description
Total Interactions
Likes
Likes at Posting
```

The actual column names may vary.

### Generic CSV

Any CSV can be used provided it contains:

- one column containing the text to analyse;
- at least one other column that can serve as metadata.

The application supports common CSV delimiters, including comma, semicolon, tab, and pipe-separated files.

---

## 4. Outputs

Depending on the selected source, the application can produce:

### IRaMuTeQ corpus

A UTF-8 `.txt` file containing the cleaned corpus and IRaMuTeQ headers.

### Processing / failure log

A text file documenting records that could not be included, such as:

- missing metadata;
- invalid URLs;
- request errors;
- article extraction failures;
- articles below the minimum length;
- duplicate records.

### MediaCloud publication statistics

The MediaCloud workflow also produces statistics comparing records in the selected input with articles successfully saved to the generated corpus.

---

## 5. Installation

Python 3.10+ is recommended.

Create a virtual environment:

```bash
python -m venv .venv
```

Activate it on macOS/Linux:

```bash
source .venv/bin/activate
```

On Windows:

```powershell
.venv\Scripts\activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Start the application:

```bash
streamlit run app.py
```

The application will normally be available at:

```text
http://localhost:8501
```

---

## 6. Streamlit Cloud deployment

The repository/folder should contain at least:

```text
iramuteq_streamlit/
├── app.py
├── requirements.txt
├── README.md
├── .gitignore
└── .streamlit/
    └── config.toml
```

For Streamlit Community Cloud:

1. Create or use a GitHub repository.
2. Upload the contents of this folder.
3. Create a new Streamlit app.
4. Select the repository and branch.
5. Set the main file to `app.py`.
6. Deploy.

The dependencies are installed from `requirements.txt`.

---

## 7. Dependencies

The application uses:

- **Streamlit** — web interface;
- **Requests** — MediaCloud article retrieval;
- **Trafilatura** — article-text extraction;
- **Plotly** — interactive visualisation support;
- **Matplotlib** — MediaCloud publication-statistics output;
- **Pandas / NumPy** — available for data-processing extensions.

See `requirements.txt` for the deployment environment.

---

## 8. Reproducibility and traceability

The application keeps the original row number of an input record as `rawnb`.

For example:

```text
**** *source_kathimerini_gr *year_2025 *yearmonth_2025_04 *type_nationalpress *rawnb_127
Article text...
```

This makes it possible to connect a document in the IRaMuTeQ corpus back to the corresponding row in the source CSV.

When reporting corpus construction, users should record at least:

- source type;
- input filename/version;
- date of processing;
- selected metadata mappings;
- MediaCloud request delay, if applicable;
- minimum article length, if applicable;
- any source-specific exclusions or cleaning choices.

---

## 9. Existing MediaCloud application

The original MediaCloud-only application remains available here:

https://mediacloud-iramuteq.streamlit.app/

The unified application keeps the MediaCloud extraction mechanism from the supplied original application while adding the CrowdTangle and generic CSV workflows.

---

## 10. Credits

**IRaMuTeQ Corpus Builder**

Created by **Panos Tsimpoukis**, with the help of ChatGPT.

Affiliations:

- LERASS (Université de Toulouse)
- PhEPoC-ST (NTUA)

The CrowdTangle cleaning logic is based on scripts supplied by **Lucie Loubère**.

The MediaCloud workflow is based on the original MediaCloud → IRaMuTeQ application supplied for this project.

---

## 11. Notes and limitations

This application prepares and cleans corpus data; it does not perform the IRaMuTeQ statistical analyses themselves.

For web-based MediaCloud extraction, article availability and website structure can change over time. Failed requests and extraction failures are therefore expected for some datasets and should be reviewed in the failure log.

For research use, retain the original input CSV alongside the generated corpus and processing log so that the transformation remains auditable.
