# IRaMuTeQ Corpus Builder

A Streamlit application for preparing **IRaMuTeQ-ready textual corpora** from:

- **CrowdTangle CSV exports**, with flexible column mapping and cleaning based on the supplied CrowdTangle scripts.
- **Any CSV file**, with one user-selected text column and at least one user-selected metadata column.

The application starts with a general IRaMuTeQ-format explanation. The user then chooses the source type and follows the corresponding workflow.

For MediaCloud processing, use the existing dedicated application:

**https://mediacloud-iramuteq.streamlit.app/**

---

## 1. IRaMuTeQ corpus format

IRaMuTeQ corpora are plain-text files in which each document starts with a header beginning with `****`. Metadata variables follow the form `*variable_value`. The text of the document follows on the next line.

Example:

```text
**** *source_example *year_2025 *country_greece *rawnb_12
This is the text of the first document.

**** *source_example *year_2025 *country_france *rawnb_13
This is the text of the second document.
```

The exact metadata variables depend on the source and the columns selected by the user.

### Important formatting rules

- `*` is structural in IRaMuTeQ. In the CrowdTangle workflow, literal asterisks in the text and description are replaced with `_`. In the generic CSV workflow, replacement of `*` can be enabled or disabled by the user.
- Tabs are reserved for IRaMuTeQ metadata and are therefore replaced with spaces in corpus text.
- The application keeps the original CSV row number as `rawnb`, allowing corpus documents to be traced back to the source dataset.

---

## 2. CrowdTangle workflow

CrowdTangle exports do not necessarily use exactly the same column names. The application therefore **detects likely columns and suggests a mapping**, while allowing the user to change every selection.

### Typical mappings

The application looks for likely equivalents of:

| Purpose | Typical CrowdTangle field |
|---|---|
| Post text | `Message` |
| Page/group | `Page Name` / `Group Name` |
| Date | `Post Created Date` |
| Description | `Description` / `Page Description` |
| Optional metadata | Other CSV columns |

The exact spelling does not need to match.

### Cleaning rules

The cleaning follows the supplied CrowdTangle scripts:

1. Post text is cleaned for IRaMuTeQ formatting.
2. Literal `*` characters are replaced with `_`.
3. Tabs and unnecessary whitespace are normalised.
4. Page/group names are converted into safe metadata values.
5. A mapped date can generate `year` and `ym` metadata.
6. An optional page/group description can be appended to the post text.
7. Duplicate generated records are removed.
8. `rawnb` preserves the original CSV row number.

### Interaction variables

The supplied CrowdTangle scripts calculate variables such as total interactions, likes, and likes at posting. These variables are **not automatically added to the IRaMuTeQ header**, following the supplied scripts and their warning that interaction variables may cause problems during treatment. If needed, they can be selected explicitly as additional metadata.

---

## 3. Generic CSV workflow

The generic CSV workflow intentionally makes as few assumptions as possible about the dataset.

The user selects:

- **exactly one text column**;
- **at least one metadata column**.

All selected metadata columns are included in the IRaMuTeQ header. The application additionally adds `rawnb` for traceability.

This makes the workflow suitable for CSV files produced by other platforms, surveys, archives, exports, or manually prepared datasets.

---

## 4. CSV reading

The application supports common delimiter formats, including:

- comma `,`
- semicolon `;`
- tab
- pipe `|`
- colon `:` as a fallback

UTF-8 files and UTF-8 files with a BOM are supported. CSV headers are normalised to remove BOM characters and unnecessary surrounding quotation marks.

---

## 5. Output

After processing, the application provides:

### IRaMuTeQ corpus

A UTF-8 `.txt` file containing the generated IRaMuTeQ documents.

### Processing log

A text log recording records that could not be included, including empty text and duplicate records.

### Processing diagnostics

The interface reports:

- number of input records;
- number of saved documents;
- number of duplicates removed;
- number of failed or empty records.

---

## 6. Installation

Python 3.10 or newer is recommended.

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

Install the dependencies:

```bash
pip install -r requirements.txt
```

Run the application:

```bash
streamlit run app.py
```

The application is normally available at `http://localhost:8501`.

---

## 7. Streamlit Community Cloud

The project can be deployed directly from a GitHub repository. The repository should contain:

```text
iramuteq_streamlit/
├── app.py
├── requirements.txt
├── README.md
├── .gitignore
└── .streamlit/
    └── config.toml
```

On Streamlit Community Cloud:

1. Push the project to GitHub.
2. Create a new Streamlit app.
3. Select the repository and branch.
4. Set the main file to `app.py`.
5. Deploy.

Streamlit installs the Python packages listed in `requirements.txt`.

---

## 8. Design principles

The application follows four principles:

**Source-specific cleaning.** CrowdTangle cleaning follows the supplied scripts rather than imposing a generic social-media schema.

**User-controlled mapping.** CSV column names can vary; users can inspect and change the suggested mapping.

**IRaMuTeQ safety.** Structural characters are cleaned before export, and the generated corpus can be previewed before construction.

**Traceability.** The `rawnb` field links generated documents back to their original CSV row.

---

## 9. Project structure

```text
iramuteq_streamlit/
├── app.py                 # Streamlit application
├── requirements.txt       # Python dependencies
├── README.md              # Project documentation
├── .gitignore             # Git exclusions
└── .streamlit/
    └── config.toml        # Streamlit configuration
```

---

## 10. Credits and methodological reference

The CrowdTangle cleaning logic is based on the CrowdTangle → IRaMuTeQ scripts supplied for this project, authored by **Lucie Loubère**. The scripts are treated as the methodological reference for the CrowdTangle-specific cleaning and metadata choices implemented here.
