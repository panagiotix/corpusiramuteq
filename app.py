##Created by Panos Tsimpoukis with ChatGPT / September 2026##
#!/usr/bin/env python3

import csv
import re
import time
import unicodedata
import threading
import uuid
from io import StringIO, BytesIO
import io
import streamlit as st


from collections import defaultdict
from datetime import datetime
from urllib.parse import urlsplit, urlunsplit

import requests
import trafilatura
import plotly.graph_objects as go


# Background extraction jobs. A small in-process job registry lets Streamlit
# rerun the UI while the extraction worker continues, making cancellation
# possible without refreshing the page.
EXTRACTION_JOBS = {}
EXTRACTION_JOBS_LOCK = threading.Lock()


# ============================================================
# CONFIGURATION
# ============================================================

INPUT_FILE = "mediacloud_articles.csv"

OUTPUT_FILE = "news_iramuteq.txt"

FAILED_FILE = "failed_articles.txt"

STATS_FILE = "publication_counts_by_year.csv"

DELAY = 1.5

MIN_ARTICLE_LENGTH = 100


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "el-GR,el;q=0.9,en;q=0.8",
}


# ============================================================
# NATIONAL PRESS
# ============================================================

NATIONAL_PRESS = {

    "rizospastis.gr",
    "alphatv.gr",
    "amna.gr",
    "elkosmos.gr",
    "ethnos.gr",
    "kathimerini.gr",
    "imerisia.gr",
    "stoxos.gr",
    "tanea.gr",
    "naftemporiki.gr",
    "athensvoice.gr",
    "ekathimerini.com",
    "enet.gr",
    "tovima.gr",
    "enetenglish.gr",
    "protothema.gr",
    "newsbomb.gr",
    "tokarfi.gr",
    "efsyn.gr",
    "lifo.gr",
    "documentonews.gr",
    "antenna.gr",
    "megatv.com",
    "novasports.gr",
    "star.gr",
    "daypress.gr",
    "gavros.gr",
    "ipop.gr",
    "newsit.gr",
    "polispress.gr",
    "politisonline.com",
    "metrogreece.gr",
    "reporter.gr",
    "athinorama.gr",
    "avgi.gr",
    "espressonews.gr",
    "kerdos.gr",
    "press-time.gr",
    "real.gr",
    "eleftherostypos.gr",
    "dimokratianews.gr",
    "parapolitika.gr",
    "topontiki.gr",
    "sportime.gr",
    "prin.gr",
    "fosonline.gr",
    "freesunday.gr",
    "vradini.gr",
    "championsday.gr",
    "kontranews.gr",
    "dimoprasion.gr",
    "makeleio.gr",
    "iefimerida.gr",
    "sport-fm.gr",
    "ereportaz.gr",
    "paron.gr",
    "agronews.gr",
    "agroekfrasi.gr",
    "axianews.gr",
    "orthodoxostypos.gr",
    "wearesolomon.com",
    "insidestory.gr",
    "thepressproject.gr",
    "themanifoldfiles.org",
    "reportersunited.gr",
    "omniatv.com",
}


# ============================================================
# REGIONAL PRESS
# ============================================================

REGIONAL_PRESS = {

    "makthes.gr",
    "rodiaki.gr",
    "trakyaninsesi.com",
    "alithia.gr",
    "thrakikigi.gr",
    "xronos.gr",
    "agonas.gr",
    "alpha1.gr",
    "athinapoli.gr",
    "aixmi-news.gr",
    "pelop.gr",
    "patrisnews.com",
    "patris.gr",
    "star-fm.gr",
    "novazora.gr",
    "ditiki.gr",
    "prlogos.gr",
    "proinoslogos.gr",
    "enimerosi.com",
    "ioanninatoday.blogspot.com",
    "neoiagones.gr",
    "proinanea.gr",
    "kilkistoday.gr",
    "metrosport.gr",
    "laos-epea.gr",
    "haniotika-nea.gr",
    "cretetv.gr",
    "mesogios.gr",
    "neakriti.gr",
    "dimokratiki.gr",
    "eleftheriaonline.gr",
    "eleftheria.gr",
    "evrytanika.gr",
    "kosmoslarissa.gr",
    "e-thessalia.gr",
    "chiosnews.com",
    "emprosnet.gr",
    "estianews.gr",
    "karfitsa.gr",
}


# ============================================================
# CLEAN SOURCE NAME
# ============================================================

def clean_source_name(media_name):
    """
    Convert media_name into a safe IRaMuTeQ source value.

    Example:

        parapolitika.gr

    becomes:

        parapolitikagr

    The original media_name is not changed in the CSV.
    """

    source = media_name.strip().lower()

    source = re.sub(
        r"[^a-z0-9]",
        "",
        source
    )

    return source


# ============================================================
# NORMALIZE SOURCE FOR CLASSIFICATION
# ============================================================

def normalize_source_for_classification(media_name):
    """
    Normalize media_name for comparison with the
    National Press and Regional Press lists.

    Classification is based ONLY on media_name,
    not on the article URL.
    """

    value = (
        media_name
        or ""
    ).strip().lower()

    value = value.rstrip("/")

    # If MediaCloud supplies a URL instead of a domain,
    # extract the hostname.
    if "://" in value:

        parts = urlsplit(value)

        value = parts.netloc.lower()

    # Remove www.
    if value.startswith("www."):

        value = value[4:]

    return value


# ============================================================
# CLASSIFY SOURCE
# ============================================================

def classify_source(media_name):
    """
    Classify the source using media_name.

    Returns:

        nationalpress
        regionalpress
        unclassified
    """

    source = normalize_source_for_classification(
        media_name
    )

    if source in NATIONAL_PRESS:

        return "nationalpress"

    if source in REGIONAL_PRESS:

        return "regionalpress"

    return "unclassified"


# ============================================================
# EXTRACT YEAR AND MONTH
# ============================================================

def extract_year_month(value):
    """
    Extract year and month from MediaCloud publish_date.

    Supports common formats including:

        2026-06-23
        2026-06-23T12:30:00Z
        2026-06-23T12:30:00+00:00
        2026-06
    """

    if not value:

        return None, None

    value = value.strip()

    # --------------------------------------------------------
    # YYYY-MM-DD
    # --------------------------------------------------------

    match = re.search(
        r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b",
        value
    )

    if match:

        year = match.group(1)

        month = match.group(2).zfill(2)

        day = match.group(3)

        try:

            datetime(
                int(year),
                int(month),
                int(day)
            )

            return year, month

        except ValueError:

            pass

    # --------------------------------------------------------
    # YYYY-MM
    # --------------------------------------------------------

    match = re.search(
        r"\b(20\d{2})-(\d{1,2})\b",
        value
    )

    if match:

        year = match.group(1)

        month = match.group(2).zfill(2)

        try:

            datetime(
                int(year),
                int(month),
                1
            )

            return year, month

        except ValueError:

            pass

    return None, None


# ============================================================
# EXTRACT YEAR
# ============================================================

def extract_year(value):
    """
    Extract only the publication year from publish_date.

    Returns:

        "2026"

    or:

        None
    """

    year, month = extract_year_month(
        value
    )

    return year


# ============================================================
# CLEAN URL
# ============================================================

def clean_url(url):
    """
    Remove query parameters and fragments from a URL.

    Example:

        https://example.gr/article?utm_source=rss

    becomes:

        https://example.gr/article
    """

    cleaned = str(url).strip().strip("\"'").strip()

    parts = urlsplit(
        cleaned
    )

    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            parts.path,
            "",
            ""
        )
    )


# ============================================================
# VALIDATE URL
# ============================================================

def valid_url(url):
    """
    Check whether the value is an HTTP/HTTPS URL.
    """

    if not url:
        return False

    cleaned = str(url).strip().strip("\"'").strip()

    return (
        cleaned.startswith("http://")
        or
        cleaned.startswith("https://")
    )


# ============================================================
# CLEAN ARTICLE TEXT
# ============================================================

def clean_custom_metadata_token(value, fallback="missing"):
    """
    Convert a user-supplied metadata name/value into an IRaMuTeQ-safe token.

    Accents are removed, whitespace/punctuation become underscores, repeated
    underscores are collapsed, and values are normalized to lowercase.
    Empty cells become the explicit category ``missing``.
    """
    value = "" if value is None else str(value).strip().lower()
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value or fallback


def prepare_custom_metadata_fields(columns):
    """
    Create unique IRaMuTeQ metadata field names from CSV column headers.
    """
    used = {}
    prepared = []

    for column in columns:
        base = clean_custom_metadata_token(column, fallback="metadata")
        count = used.get(base, 0) + 1
        used[base] = count
        field = base if count == 1 else f"{base}_{count}"
        prepared.append((column, field))

    return prepared


def clean_text(text):
    """
    Prepare article text for IRaMuTeQ.

    Tabs are removed because tabs are reserved for
    IRaMuTeQ metadata.

    Whitespace and line breaks are normalized.
    """

    if not text:

        return ""

    # --------------------------------------------------------
    # Remove tabs.
    # --------------------------------------------------------

    text = text.replace(
        "\t",
        " "
    )

    # --------------------------------------------------------
    # Normalize carriage returns.
    # --------------------------------------------------------

    text = text.replace(
        "\r",
        "\n"
    )

    # --------------------------------------------------------
    # Normalize spaces.
    # --------------------------------------------------------

    text = re.sub(
        r"[ \t]+",
        " ",
        text
    )

    # --------------------------------------------------------
    # Normalize line breaks.
    # --------------------------------------------------------

    text = re.sub(
        r"\n+",
        " ",
        text
    )

    return text.strip()


# ============================================================
# WRITE FAILURE
# ============================================================

def write_failure(
    failed_file,
    row_number,
    media_name,
    publish_date,
    url,
    reason
):
    """
    Write a detailed failure record.

    row_number is the ORIGINAL MediaCloud CSV row number.
    """

    failed_file.write(
        f"[ROW {row_number}]\n"
    )

    failed_file.write(
        f"source: {media_name}\n"
    )

    failed_file.write(
        f"publish_date: {publish_date}\n"
    )

    failed_file.write(
        f"url: {url}\n"
    )

    failed_file.write(
        f"error: {reason}\n"
    )

    failed_file.write(
        "-" * 70
        + "\n\n"
    )

    failed_file.flush()


# ============================================================
# GENERATE PUBLICATION STATISTICS
# ============================================================

def generate_publication_statistics(rows):
    """
    Generate a CSV containing the number of MediaCloud
    publications per year for each selected media source.

    IMPORTANT:

    This is calculated directly from the selected MediaCloud
    records BEFORE URL deduplication and BEFORE article
    downloading/extraction.

    Therefore, these numbers may differ from the number of
    articles ultimately included in the IRaMuTeQ TXT corpus.
    """

    # ========================================================
    # GET SELECTED SOURCES
    # ========================================================

    sources = sorted(
        {
            (
                row.get(
                    "media_name",
                    ""
                )
                or ""
            ).strip()
            for row in rows
            if (
                row.get(
                    "media_name",
                    ""
                )
                or ""
            ).strip()
        },
        key=str.lower
    )

    # ========================================================
    # COUNT ARTICLES
    # ========================================================

    counts = defaultdict(
        lambda: defaultdict(int)
    )

    years = set()

    invalid_date_rows = 0

    for row in rows:

        media_name = (
            row.get(
                "media_name",
                ""
            )
            or ""
        ).strip()

        publish_date = (
            row.get(
                "publish_date",
                ""
            )
            or ""
        ).strip()

        year = extract_year(
            publish_date
        )

        if not year:

            invalid_date_rows += 1

            continue

        counts[year][media_name] += 1

        years.add(
            year
        )

    # ========================================================
    # WRITE CSV
    # ========================================================

    with open(
        STATS_FILE,
        "w",
        encoding="utf-8-sig",
        newline=""
    ) as f:

        writer = csv.writer(
            f
        )

        # ----------------------------------------------------
        # Header
        # ----------------------------------------------------

        writer.writerow(
            ["year"] + sources
        )

        # ----------------------------------------------------
        # One row per year
        # ----------------------------------------------------

        for year in sorted(
            years,
            key=int
        ):

            row_values = [
                year
            ]

            for source in sources:

                row_values.append(
                    counts[year].get(
                        source,
                        0
                    )
                )

            writer.writerow(
                row_values
            )

    # ========================================================
    # REPORT
    # ========================================================

    print()

    print("=" * 70)

    print("PUBLICATION STATISTICS")

    print("=" * 70)

    print()

    print(
        f"Statistics file: {STATS_FILE}"
    )

    print(
        f"Selected media: {len(sources)}"
    )

    print(
        f"Years found: {len(years)}"
    )

    if invalid_date_rows:

        print(
            f"Rows with unparseable dates: "
            f"{invalid_date_rows}"
        )

    print()

    print(
        "WARNING:"
    )

    print(
        "These statistics are calculated directly from "
        "the selected MediaCloud records."
    )

    print(
        "They may differ from the number of articles "
        "included in the final IRaMuTeQ .txt file."
    )

    print(
        "The .txt file can contain fewer articles because "
        "of duplicate URLs, inaccessible pages, request "
        "errors, extraction failures, or articles that "
        "are too short."
    )

    print()

    return STATS_FILE







# ============================================================
# CROWD TANGLE / GENERIC CSV HELPERS
# ============================================================

def detect_delimiter(decoded):
    sample = "\n".join([x for x in decoded.splitlines() if x.strip()][:12])
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|:").delimiter
    except csv.Error:
        first = next((x for x in decoded.splitlines() if x.strip()), "")
        counts = {d: first.count(d) for d in [",", ";", "\t", "|"]}
        return max(counts, key=counts.get) if max(counts.values(), default=0) else ","


def read_flexible_csv(uploaded):
    raw = uploaded.getvalue()
    decoded = raw.decode("utf-8-sig", errors="replace")
    delimiter = detect_delimiter(decoded)
    first_line = next((line for line in decoded.splitlines() if line.strip()), "")
    quotechars = ["'", '"'] if first_line.lstrip().startswith("'") else ['"', "'"]
    reader = None
    for quotechar in quotechars:
        candidate = csv.DictReader(io.StringIO(decoded), delimiter=delimiter, quotechar=quotechar)
        if candidate.fieldnames:
            candidate.fieldnames = [normalize_csv_header(h) for h in candidate.fieldnames]
            reader = candidate
            break
    if reader is None or not reader.fieldnames:
        raise ValueError("The CSV contains no header.")
    rows = []
    for row_number, row in enumerate(reader, 2):
        rows.append({
            normalize_csv_header(k): ("" if v is None else v)
            for k, v in row.items() if normalize_csv_header(k)
        } | {"_rawnb": row_number})
    return raw, reader.fieldnames, rows, delimiter


def sanitize_iramuteq_value(value, fallback="missing"):
    value = "" if value is None else str(value).strip()
    value = value.replace("\ufeff", "")
    value = value.replace("*", "_")
    value = re.sub(r"[\r\n\t]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value or fallback


def sanitize_iramuteq_field(name, fallback="metadata"):
    value = "" if name is None else str(name).strip().lower()
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value or fallback


def iramuteq_header(metadata_pairs):
    parts = ["****"]
    used = {}
    for name, value in metadata_pairs:
        field = sanitize_iramuteq_field(name)
        count = used.get(field, 0) + 1
        used[field] = count
        if count > 1:
            field = f"{field}_{count}"
        parts.append(f"*{field}_{sanitize_iramuteq_value(value)}")
    return " ".join(parts)


def clean_flexible_text(text, replace_asterisks=True):
    if text is None:
        return ""
    value = str(text).replace("\ufeff", "").replace("\t", " ")
    if replace_asterisks:
        value = value.replace("*", "_")
    value = value.replace("\r", "\n")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n+", " ", value)
    return value.strip()


def join_text_columns(row, columns, cleaner):
    values = []
    for column in columns:
        value = cleaner(row.get(column, ""))
        if value:
            values.append(value)
    return "\n".join(values).strip()


def suggest_ct_column(fieldnames, candidates):
    normalized = {c.lower().replace("_", " "): c for c in fieldnames}
    for candidate in candidates:
        if candidate in normalized:
            return normalized[candidate]
    for c in fieldnames:
        norm = c.lower().replace("_", " ")
        if any(candidate in norm for candidate in candidates):
            return c
    return None


def build_crowdtangle_corpus(rows, text_col, group_col, date_col, desc_col, include_description, extra_cols):
    records = []
    seen = set()
    duplicate_count = 0
    failed = 0
    log = []
    for row in rows:
        text = clean_flexible_text(row.get(text_col, ""), True)
        if include_description and desc_col:
            desc = clean_flexible_text(row.get(desc_col, ""), True)
            if desc:
                text = (text + "\n" + desc).strip() if text else desc
        if not text:
            failed += 1
            log.append(f"rawnb={row.get('_rawnb','')}\tstatus=empty_text")
            continue
        meta = []
        if group_col:
            meta.append(("groupe", row.get(group_col, "")))
        if date_col:
            date_value = str(row.get(date_col, "") or "").strip()
            meta.append(("date", date_value))
            year, month = extract_year_month(date_value)
            if year:
                meta.append(("year", year))
            if year and month:
                meta.append(("ym", f"{year}-{month}"))
        for col in extra_cols:
            meta.append((col, row.get(col, "")))
        meta.append(("rawnb", row.get("_rawnb", "")))
        header = iramuteq_header(meta)
        record = header + "\n" + text
        if record in seen:
            duplicate_count += 1
            log.append(f"rawnb={row.get('_rawnb','')}\tstatus=duplicate")
            continue
        seen.add(record)
        records.append(record)
        log.append(f"rawnb={row.get('_rawnb','')}\tstatus=saved")
    corpus = "\n\n".join(records) + ("\n" if records else "")
    stats = {"input": len(rows), "saved": len(records), "duplicates": duplicate_count, "failed": failed}
    return corpus.encode("utf-8"), "\n".join(log).encode("utf-8"), stats


def build_generic_corpus(rows, text_col, metadata_cols, replace_asterisks=True):
    records = []
    seen = set()
    duplicate_count = 0
    failed = 0
    log = []
    for row in rows:
        text = clean_flexible_text(row.get(text_col, ""), replace_asterisks)
        if not text:
            failed += 1
            log.append(f"rawnb={row.get('_rawnb','')}\tstatus=empty_text")
            continue
        meta = [(col, row.get(col, "")) for col in metadata_cols]
        meta.append(("rawnb", row.get("_rawnb", "")))
        record = iramuteq_header(meta) + "\n" + text
        if record in seen:
            duplicate_count += 1
            log.append(f"rawnb={row.get('_rawnb','')}\tstatus=duplicate")
            continue
        seen.add(record)
        records.append(record)
        log.append(f"rawnb={row.get('_rawnb','')}\tstatus=saved")
    corpus = "\n\n".join(records) + ("\n" if records else "")
    stats = {"input": len(rows), "saved": len(records), "duplicates": duplicate_count, "failed": failed}
    return corpus.encode("utf-8"), "\n".join(log).encode("utf-8"), stats


def show_local_result(label, corpus, log, stats, corpus_name, log_name):
    st.session_state["last_local_result"] = {
        "label": label, "corpus": corpus, "log": log, "stats": stats,
        "corpus_name": corpus_name, "log_name": log_name,
    }
    st.rerun()


def render_flexible_mode(source_mode):
    st.markdown('<div class="section-title">1. Upload input</div>', unsafe_allow_html=True)
    st.markdown('<div class="section-note">Upload the CSV to convert. CrowdTangle headers are detected flexibly; generic CSVs are mapped entirely by you.</div>', unsafe_allow_html=True)
    uploaded = st.file_uploader("CSV input", type=["csv"], label_visibility="collapsed")
    if uploaded is None:
        st.info("Upload a CSV to continue.")
        return
    try:
        raw, fieldnames, rows, delimiter = read_flexible_csv(uploaded)
    except Exception as exc:
        st.error(f"Unable to read the CSV: {type(exc).__name__}: {exc}")
        return
    if not rows:
        st.warning("The CSV contains no records.")
        return
    st.caption(f"Detected delimiter: `{repr(delimiter)}` · {len(fieldnames):,} columns · {len(rows):,} records")

    if source_mode == "CrowdTangle":
        st.markdown('<div class="section-title">2. Map CrowdTangle columns</div>', unsafe_allow_html=True)
        st.markdown("CrowdTangle exports can change their headers. Suggested columns are only defaults: **you control the final mapping**.")
        text_default = suggest_ct_column(fieldnames, ["message", "post message", "post text", "text", "post content"])
        group_default = suggest_ct_column(fieldnames, ["page name", "group name", "page", "group", "account name"])
        date_default = suggest_ct_column(fieldnames, ["post created date", "created date", "created time", "post date", "date", "time"])
        desc_default = suggest_ct_column(fieldnames, ["page description", "description", "group description"])
        text_col = st.selectbox("Post text column *", fieldnames, index=fieldnames.index(text_default) if text_default in fieldnames else 0, key="ct_text_v2")
        optional = ["— none —"] + fieldnames
        def opt_select(label, default, key):
            return st.selectbox(label, optional, index=optional.index(default) if default in optional else 0, key=key)
        group_col = opt_select("Page / group name (recommended)", group_default, "ct_group_v2")
        date_col = opt_select("Post date (recommended)", date_default, "ct_date_v2")
        desc_col = opt_select("Page / group description (optional)", desc_default, "ct_desc_v2")
        reserved = {text_col} | {c for c in [group_col, date_col, desc_col] if c != "— none —"}
        extra_cols = st.multiselect("Additional metadata columns (optional)", [c for c in fieldnames if c not in reserved], key="ct_extra_v2")
        include_description = False
        if desc_col != "— none —":
            include_description = st.checkbox("Append the selected description to each post", value=True, key="ct_include_desc_v2")
        metadata_exists = group_col != "— none —" or date_col != "— none —" or bool(extra_cols)
        if not metadata_exists:
            st.error("CrowdTangle mode requires at least one metadata field. Select a page/group, date, or another metadata column.")
            return
        p = rows[0]
        meta = []
        if group_col != "— none —": meta.append(("groupe", p.get(group_col, "")))
        if date_col != "— none —":
            date_value = str(p.get(date_col, "") or "").strip(); meta.append(("date", date_value))
            year, month = extract_year_month(date_value)
            if year: meta.append(("year", year))
            if year and month: meta.append(("ym", f"{year}-{month}"))
        for c in extra_cols: meta.append((c, p.get(c, "")))
        meta.append(("rawnb", p.get("_rawnb", "")))
        preview_text = clean_flexible_text(p.get(text_col, ""), True)
        if include_description and desc_col != "— none —":
            desc = clean_flexible_text(p.get(desc_col, ""), True)
            if desc: preview_text = (preview_text + "\n" + desc).strip()
        st.markdown('<div class="section-title">3. Preview</div>', unsafe_allow_html=True)
        st.code(iramuteq_header(meta) + "\n" + preview_text, language="text")
        if st.button("Build CrowdTangle corpus", type="primary", use_container_width=True, key="build_ct_v2"):
            corpus, log, stats = build_crowdtangle_corpus(rows, text_col, None if group_col == "— none —" else group_col, None if date_col == "— none —" else date_col, None if desc_col == "— none —" else desc_col, include_description, extra_cols)
            show_local_result("CrowdTangle", corpus, log, stats, "crowdtangle_iramuteq.txt", "crowdtangle_processing_log.txt")

    else:
        st.markdown('<div class="section-title">2. Map CSV columns</div>', unsafe_allow_html=True)
        st.markdown("For a generic CSV, the only structural requirements are **exactly one text column** and **at least one metadata column**.")
        text_col = st.selectbox("Text column *", fieldnames, key="generic_text_v2")
        metadata_cols = st.multiselect("Metadata column(s) * — select at least one", [c for c in fieldnames if c != text_col], key="generic_meta_v2")
        if not metadata_cols:
            st.info("Select at least one metadata column to continue.")
            return
        replace_asterisks = st.checkbox("Replace `*` with `_` in text (recommended for IRaMuTeQ)", value=True, key="generic_asterisks_v2")
        p = rows[0]
        meta = [(c, p.get(c, "")) for c in metadata_cols] + [("rawnb", p.get("_rawnb", ""))]
        preview_text = clean_flexible_text(p.get(text_col, ""), replace_asterisks)
        st.markdown('<div class="section-title">3. Preview</div>', unsafe_allow_html=True)
        st.code(iramuteq_header(meta) + "\n" + preview_text, language="text")
        if st.button("Build generic CSV corpus", type="primary", use_container_width=True, key="build_generic_v2"):
            corpus, log, stats = build_generic_corpus(rows, text_col, metadata_cols, replace_asterisks)
            show_local_result("Generic CSV", corpus, log, stats, "csv_iramuteq.txt", "csv_processing_log.txt")

    if st.session_state.get("last_local_result"):
        result = st.session_state["last_local_result"]
        st.markdown('<div class="section-title">4. Research outputs</div>', unsafe_allow_html=True)
        st.success(f"{result['label']} corpus construction completed.")
        stats = result["stats"]
        a = st.columns(4)
        a[0].metric("Input records", stats["input"])
        a[1].metric("Saved documents", stats["saved"])
        a[2].metric("Duplicates removed", stats["duplicates"])
        a[3].metric("Failed / empty", stats["failed"])
        d1, d2 = st.columns(2)
        with d1:
            st.download_button("Download IRaMuTeQ corpus", result["corpus"], result["corpus_name"], "text/plain", use_container_width=True, key=f"local_corpus_{source_mode}")
        with d2:
            st.download_button("Download processing log", result["log"], result["log_name"], "text/plain", use_container_width=True, key=f"local_log_{source_mode}")
        with st.expander("Processing diagnostics", expanded=False):
            st.json(stats)


def render_landing_and_mode_selector():
    st.markdown('<div class="research-kicker">Open research utility · corpus preparation</div>', unsafe_allow_html=True)
    st.markdown('<h1 class="research-title">IRaMuTeQ Corpus Builder</h1>', unsafe_allow_html=True)
    st.markdown('<div class="research-subtitle">Prepare IRaMuTeQ-ready textual corpora from MediaCloud, CrowdTangle exports, or any CSV.</div>', unsafe_allow_html=True)
    st.markdown('<div class="citation-box"><strong>IRaMuTeQ format.</strong> Each document starts with a `****` header, followed by metadata variables such as `*source_value`, then the text on the following line(s).</div>', unsafe_allow_html=True)
    st.markdown('<div class="section-title">What should an IRaMuTeQ corpus look like?</div>', unsafe_allow_html=True)
    st.markdown("A minimal example is shown below. The first line is the document header; the next line is the text.")
    st.code("**** *source_example *year_2025 *country_greece *rawnb_12\nThis is the text of the first document.\n\n**** *source_example *year_2025 *country_france *rawnb_13\nThis is the text of the second document.", language="text")
    st.caption("The exact variables depend on the selected source and your mappings. The `rawnb` field keeps a trace back to the input row.")
    st.markdown('<div class="section-title">Choose the input source</div>', unsafe_allow_html=True)
    return st.radio("Input source", ["MediaCloud", "CrowdTangle", "Any CSV"], horizontal=True, index=0, key="source_mode_v2")


def render_mediacloud_original():
    import io
    from datetime import datetime

    import matplotlib.pyplot as plt
    # ------------------------------------------------------------
    # Visual identity
    # ------------------------------------------------------------
    st.markdown(
        """
        <style>
        :root {
            --academic-ink: #172033;
            --academic-muted: #667085;
            --academic-line: #d9dee8;
            --academic-paper: #fbfcfe;
        }

        .stApp {
            background: var(--academic-paper);
        }

        .block-container {
            max-width: 1180px;
            padding-top: 2.2rem;
            padding-bottom: 4rem;
        }

        .research-kicker {
            font-size: 0.78rem;
            letter-spacing: 0.14em;
            text-transform: uppercase;
            color: #667085;
            font-weight: 700;
            margin-bottom: 0.5rem;
        }

        .research-title {
            font-family: Georgia, "Times New Roman", serif;
            font-size: clamp(2.2rem, 4vw, 3.65rem);
            line-height: 1.05;
            color: #172033;
            margin: 0;
            font-weight: 600;
        }

        .research-subtitle {
            font-size: 1.08rem;
            line-height: 1.65;
            color: #667085;
            max-width: 850px;
            margin-top: 1rem;
        }

        .section-title {
            font-family: Georgia, "Times New Roman", serif;
            font-size: 1.55rem;
            color: #172033;
            margin: 2.2rem 0 0.35rem 0;
        }

        .section-note {
            color: #667085;
            margin-bottom: 1rem;
        }

        .method-card {
            border: 1px solid #d9dee8;
            border-radius: 12px;
            padding: 1.1rem 1.2rem;
            background: white;
            min-height: 125px;
        }

        .method-number {
            font-size: 0.75rem;
            letter-spacing: 0.08em;
            text-transform: uppercase;
            color: #667085;
            font-weight: 700;
        }

        .method-heading {
            font-family: Georgia, "Times New Roman", serif;
            color: #172033;
            font-size: 1.12rem;
            margin-top: 0.35rem;
        }

        .method-text {
            color: #667085;
            font-size: 0.91rem;
            line-height: 1.45;
        }

        .citation-box {
            border-left: 3px solid #172033;
            background: white;
            padding: 0.85rem 1rem;
            color: #475467;
            font-size: 0.9rem;
            line-height: 1.55;
            margin: 1rem 0 1.5rem 0;
        }

        .footer-line {
            border-top: 1px solid #d9dee8;
            margin-top: 3rem;
            padding-top: 1rem;
            color: #667085;
            font-size: 0.82rem;
        }

        div[data-testid="stMetric"] {
            background: white;
            border: 1px solid #d9dee8;
            padding: 0.85rem;
            border-radius: 10px;
        }

        div[data-testid="stFileUploader"] {
            background: white;
            border: 1px dashed #b9c1cf;
            border-radius: 12px;
            padding: 0.35rem;
        }

        .stButton > button, .stDownloadButton > button {
            border-radius: 8px;
            font-weight: 600;
        }

        [data-testid="stSidebar"] {
            border-right: 1px solid #d9dee8;
        }

        /* High-contrast text for accessibility and reliable rendering across themes. */
        .stApp, .stApp p, .stApp label, .stApp span, .stApp div,
        [data-testid="stSidebar"], [data-testid="stSidebar"] p,
        [data-testid="stSidebar"] label, [data-testid="stSidebar"] span {
            color: #111111;
        }
        .research-kicker, .section-note, .research-subtitle, .method-text,
        .method-number, .footer-line, .stCaption, [data-testid="stCaptionContainer"] {
            color: #444444 !important;
        }
        .research-title, .section-title, .method-heading {
            color: #111111 !important;
        }
        [data-testid="stMetricValue"], [data-testid="stMetricLabel"] {
            color: #111111 !important;
        }
        input, textarea, [data-baseweb="select"] * {
            color: #111111 !important;
        }
        code {
            color: #111111 !important;
        }

        /* Dark controls: keep text white wherever Streamlit renders a dark widget. */
        [data-testid="stSidebar"] {
            background: #111111 !important;
        }
        [data-testid="stSidebar"] *,
        [data-testid="stSidebar"] p,
        [data-testid="stSidebar"] label,
        [data-testid="stSidebar"] span,
        [data-testid="stSidebar"] div,
        [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] * {
            color: #ffffff !important;
        }

        [data-testid="stSidebar"] input,
        [data-testid="stSidebar"] textarea,
        [data-testid="stSidebar"] [data-baseweb="select"],
        [data-testid="stSidebar"] [data-baseweb="input"],
        [data-testid="stSidebar"] [role="spinbutton"] {
            background-color: #111111 !important;
            color: #ffffff !important;
            -webkit-text-fill-color: #ffffff !important;
            border-color: #555555 !important;
        }

        [data-testid="stSidebar"] input::placeholder,
        [data-testid="stSidebar"] textarea::placeholder {
            color: #d0d0d0 !important;
            -webkit-text-fill-color: #d0d0d0 !important;
        }

        .stButton > button,
        .stDownloadButton > button,
        [data-testid="stFormSubmitButton"] > button {
            background-color: #111111 !important;
            color: #ffffff !important;
            -webkit-text-fill-color: #ffffff !important;
            border: 1px solid #111111 !important;
        }

        .stButton > button *,
        .stDownloadButton > button *,
        [data-testid="stFormSubmitButton"] > button * {
            color: #ffffff !important;
            -webkit-text-fill-color: #ffffff !important;
        }

        .stButton > button:hover,
        .stDownloadButton > button:hover,
        [data-testid="stFormSubmitButton"] > button:hover {
            background-color: #2b2b2b !important;
            color: #ffffff !important;
        }

        /* Keep the main source-selection labels readable on the light research canvas. */
        [data-testid="stCheckbox"] label,
        [data-testid="stCheckbox"] label p,
        [data-testid="stCheckbox"] label span {
            color: #111111 !important;
        }

        /* High-contrast uploader: the selected filename must remain readable. */
        [data-testid="stFileUploader"] section,
        [data-testid="stFileUploaderDropzone"],
        [data-testid="stFileUploaderDropzoneInstructions"],
        [data-testid="stFileUploaderFileName"],
        [data-testid="stFileUploaderFileName"] *,
        [data-testid="stFileUploader"] small {
            color: #111111 !important;
            -webkit-text-fill-color: #111111 !important;
        }
        [data-testid="stFileUploaderDropzone"] {
            background: #ffffff !important;
            border-color: #777777 !important;
        }

        /* High-contrast comparison selector: dark control with white text and icons. */
        div[data-testid="stSelectbox"] [data-baseweb="select"],
        div[data-testid="stSelectbox"] [data-baseweb="select"] > div,
        div[data-testid="stSelectbox"] [data-baseweb="select"] * {
            background-color: #111111 !important;
            color: #ffffff !important;
            -webkit-text-fill-color: #ffffff !important;
            border-color: #555555 !important;
        }
        div[data-testid="stSelectbox"] [data-baseweb="select"] svg {
            fill: #ffffff !important;
            color: #ffffff !important;
        }
        /* BaseWeb renders the selected value in a nested single-value element;
           override the global dark-text rule with higher specificity. */
        div[data-testid="stSelectbox"] [data-baseweb="select"] [class*="singleValue"],
        div[data-testid="stSelectbox"] [data-baseweb="select"] [class*="singleValue"] span,
        div[data-testid="stSelectbox"] [data-baseweb="select"] [class*="placeholder"] {
            color: #ffffff !important;
            -webkit-text-fill-color: #ffffff !important;
        }
        div[data-testid="stSelectbox"] [data-baseweb="select"] input {
            color: #ffffff !important;
            -webkit-text-fill-color: #ffffff !important;
            caret-color: #ffffff !important;
        }
        [data-baseweb="popover"] [role="option"],
        [data-baseweb="popover"] [role="option"] * {
            color: #ffffff !important;
            -webkit-text-fill-color: #ffffff !important;
        }
        [data-baseweb="popover"] [role="option"] {
            background-color: #111111 !important;
        }
        [data-baseweb="popover"] [role="option"][aria-selected="true"],
        [data-baseweb="popover"] [role="option"]:hover {
            background-color: #2b2b2b !important;
        }

        /* Dataframe/table toolbar: keep action icons visible on the dark toolbar. */
        [data-testid="stDataFrame"] button,
        [data-testid="stDataFrame"] button *,
        [data-testid="stDataFrame"] [role="button"],
        [data-testid="stDataFrame"] [role="button"] * {
            color: #ffffff !important;
            fill: #ffffff !important;
            stroke: #ffffff !important;
        }
        [data-testid="stDataFrame"] [data-testid="stElementToolbar"] {
            background-color: #111111 !important;
        }

        /* Download buttons: Streamlit may render the label in nested elements
           that inherit the global light-text rule. Force every visible part to white. */
        [data-testid="stDownloadButton"] > button,
        [data-testid="stDownloadButton"] > button *,
        div.stDownloadButton > button,
        div.stDownloadButton > button *,
        .stDownloadButton button p,
        .stDownloadButton button span,
        .stDownloadButton button div,
        .stDownloadButton button svg,
        .stDownloadButton button svg *,
        .stDownloadButton button path {
            color: #ffffff !important;
            -webkit-text-fill-color: #ffffff !important;
            fill: #ffffff !important;
            stroke: #ffffff !important;
        }
        [data-testid="stDownloadButton"] > button,
        div.stDownloadButton > button {
            background-color: #111111 !important;
            border-color: #111111 !important;
        }

        /* Comparison selectbox: force the selected value itself to white.
           This intentionally targets the BaseWeb value node rather than only
           the outer select container. */
        [data-testid="stSelectbox"] [data-baseweb="select"] [class*="singleValue"],
        [data-testid="stSelectbox"] [data-baseweb="select"] [class*="singleValue"] *,
        [data-testid="stSelectbox"] [data-baseweb="select"] [class*="placeholder"],
        [data-testid="stSelectbox"] [data-baseweb="select"] [class*="placeholder"] *,
        [data-testid="stSelectbox"] [data-baseweb="select"] > div,
        [data-testid="stSelectbox"] [data-baseweb="select"] > div * {
            color: #ffffff !important;
            -webkit-text-fill-color: #ffffff !important;
        }
        [data-testid="stSelectbox"] [data-baseweb="select"] svg,
        [data-testid="stSelectbox"] [data-baseweb="select"] svg * {
            color: #ffffff !important;
            fill: #ffffff !important;
            stroke: #ffffff !important;
        }

        /* File uploader: Upload/browse action must use white text and icon. */
        [data-testid="stFileUploader"] button,
        [data-testid="stFileUploader"] button *,
        [data-testid="stFileUploader"] [role="button"],
        [data-testid="stFileUploader"] [role="button"] * {
            background-color: #111111 !important;
            color: #ffffff !important;
            -webkit-text-fill-color: #ffffff !important;
            fill: #ffffff !important;
            stroke: #ffffff !important;
            border-color: #111111 !important;
        }

        /* Metadata field names on dark research cards. */
        .metadata-card {
            background: #111111 !important;
            border: 1px solid #333333 !important;
            border-radius: 10px !important;
            padding: 1.1rem 1.2rem !important;
        }
        .metadata-card, .metadata-card * {
            color: #ffffff !important;
            -webkit-text-fill-color: #ffffff !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    # ------------------------------------------------------------
    # Header
    # ------------------------------------------------------------
    st.markdown('<div class="research-kicker">Open research utility · corpus preparation</div>',
                unsafe_allow_html=True)
    st.markdown('<h1 class="research-title">MediaCloud → IRaMuTeQ</h1>',
                unsafe_allow_html=True)
    st.markdown(
        '<div class="research-subtitle">'
        'A reproducible workflow for transforming MediaCloud article records into '
        'an IRaMuTeQ-ready textual corpus, with source classification, publication '
        'statistics, duplicate control, and transparent extraction diagnostics.'
        '</div>',
        unsafe_allow_html=True,
    )

    st.markdown(
        '<div class="citation-box">'
        '<strong>Workflow.</strong> Upload → inspect → select → extract → validate → export. '
        'The application processes the supplied records and does not require users to '
        'manually manipulate the resulting corpus file.'
        '</div>',
        unsafe_allow_html=True,
    )

    # ------------------------------------------------------------
    # Method overview
    # ------------------------------------------------------------
    steps = [
        ("01", "Upload", "Provide a MediaCloud CSV with media name, publication date, and URL."),
        ("02", "Configure", "Select sources and review extraction parameters before processing."),
        ("03", "Extract", "Download pages, extract article text, clean it, and apply IRaMuTeQ metadata."),
        ("04", "Export", "Download the corpus, publication statistics, and complete failure log."),
    ]
    cols = st.columns(4)
    for col, (num, heading, description) in zip(cols, steps):
        with col:
            st.markdown(
                f'<div class="method-card">'
                f'<div class="method-number">{num}</div>'
                f'<div class="method-heading">{heading}</div>'
                f'<div class="method-text">{description}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

    # ------------------------------------------------------------
    # Sidebar: reproducibility / parameters
    # ------------------------------------------------------------
    with st.sidebar:
        with st.expander("About", expanded=False):
            st.markdown(
                """
                **MediaCloud → IRaMuTeQ**

                A research tool for transforming MediaCloud article records into an IRaMuTeQ-compatible corpus.

                **Created by**  
                Panos Tsimpoukis  
                with the help of ChatGPT  
                LERASS (UT) · PhEPoC-ST (NTUA)
                """
            )

        st.markdown("### Corpus language / source classification")
        classification_mode = st.selectbox(
            "How should sources be classified?",
            options=[
                "Greek corpus — use National / Regional press classification",
                "Other language — do not classify as National / Regional press",
            ],
            index=0,
            help="Choose the second option for French or other non-Greek corpora. The article text is still extracted normally; only the Greek-specific National/Regional press classification is switched off.",
        )
        use_press_classification = classification_mode.startswith("Greek corpus")
        st.caption(
            "For non-Greek corpora, no built-in *type metadata is added. Use Custom metadata below for your own classifications."
            if not use_press_classification else
            "For Greek corpora, the built-in National Press and Regional Press lists are used."
        )

        st.markdown("### Processing settings")
        st.caption(
            "These controls affect how aggressively the application retrieves pages and "
            "how short an extracted text can be before it is excluded."
        )
        delay = st.number_input(
            "Wait between article requests (seconds)",
            min_value=0.0,
            max_value=60.0,
            value=float(DELAY),
            step=0.1,
            help="This is the pause between visits to article webpages. Use 1.5 seconds for normal use. Use 2–5 seconds for very large datasets or when you want to be more conservative toward websites. Lower values are mainly for small tests.",
        )
        min_article_length = st.number_input(
            "Minimum article length (characters)",
            min_value=0,
            max_value=100000,
            value=int(MIN_ARTICLE_LENGTH),
            step=10,
            help="Extracted texts shorter than this threshold are logged as short articles rather than added to the corpus.",
        )

        with st.expander("How should I choose these settings?", expanded=False):
            st.markdown(
                """
                **Wait between article requests**

    This controls how long the app waits before visiting the next article webpage. A longer wait is slower but more considerate to the websites being accessed.

                - **1.5 s (recommended default):** good for ordinary research batches and the closest match to the original script.
                - **2–5 s:** preferable for very large corpora, slower servers, or when you want to be especially conservative toward source websites.
                - **0–1 s:** useful only for small test batches. Faster is not necessarily better and may increase the chance of rate limiting.

                **Minimum article length**

                - **100 characters (recommended default):** keeps the original script's behavior and removes obviously empty/very short extractions.
                - **200–500 characters:** useful when you want to exclude snippets, navigation remnants, or unusually poor extractions more aggressively.
                - **0 characters:** mainly for diagnostic/testing purposes; it may allow low-quality extractions into the corpus.

                There is no universally correct threshold: choose values that match the corpus and document them when reporting your research.
                """
            )

        st.markdown("---")
        st.markdown("### Reproducibility")
        st.caption(
            "Original MediaCloud row numbers are preserved in the IRaMuTeQ metadata. "
            "National/Regional press classification is used only when the Greek-corpus option is selected."
        )

        st.markdown("---")
        st.markdown("### Input specification")
        st.markdown(
            "Your CSV must contain these **three column names**. Each row represents one "
            "MediaCloud article record."
        )
        st.markdown(
            """
            **`media_name`** — the name of the newspaper, website, or other media source as recorded by MediaCloud (for example, `kathimerini.gr`).

            **`publish_date`** — the date/time when the article was published. The application uses it to determine the publication year and year-month.

            **`url`** — the complete web address of the article (for example, `https://example.org/article`). The application visits this webpage and attempts to extract the article text.

            **In short:** one CSV row should correspond to one article record from your MediaCloud export. The file can have any filename (for example, `my_french_corpus.csv`); only the required column names matter.
            """
        )

    # ------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------
    st.markdown('<div class="section-title">1. Corpus input</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="section-note">Upload the CSV exported from MediaCloud. The filename itself does not matter; the application reads the file you upload.</div>',
        unsafe_allow_html=True,
    )

    uploaded = st.file_uploader(
        "MediaCloud CSV",
        type=["csv"],
        label_visibility="collapsed",
        help="Required columns: media_name, publish_date, url",
    )

    if uploaded is None:
        st.info(
            "No corpus loaded yet. Upload a CSV to inspect its sources and begin."
        )
        st.markdown(
            '<div class="footer-line">'
            'Designed for corpus preparation and transparent downstream analysis in IRaMuTeQ.'
            '</div>',
            unsafe_allow_html=True,
        )
        st.stop()

    # ------------------------------------------------------------
    # Read input
    # ------------------------------------------------------------
    def normalize_csv_header(value):
        """Normalize common spreadsheet-export artifacts in CSV headers."""
        value = "" if value is None else str(value)
        value = value.replace("\ufeff", "").strip()
        # Some spreadsheet exports use single quotes as the text qualifier.
        # Remove only a matching pair around the complete header value.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1].strip()
        return value


    def read_csv_robustly(decoded):
        """Read normal CSVs plus common spreadsheet quoting variations.

        Some spreadsheet exports use single quotes as the CSV quote character.
        If we first parse such a file with the default double-quote parser, the
        headers can be normalized successfully but the *cell values keep their
        surrounding single quotes*. That makes URLs such as
        ``'https://example.org/article'`` fail URL validation.

        Detect the quoting style before parsing so both headers and values are
        interpreted consistently.
        """
        required_columns = {"media_name", "publish_date", "url"}

        # Look at the first non-empty line. The uploaded CSV in particular starts
        # fields with single quotes, so use that quote character for the whole file.
        first_line = next((line for line in decoded.splitlines() if line.strip()), "")
        if first_line.lstrip().startswith("'"):
            attempts = [{"quotechar": "'"}, {}]
        elif first_line.lstrip().startswith('"'):
            attempts = [{}, {"quotechar": "'"}]
        else:
            attempts = [{}, {"quotechar": "'"}]

        last_reader = None
        for kwargs in attempts:
            reader = csv.DictReader(io.StringIO(decoded), **kwargs)
            if not reader.fieldnames:
                continue

            normalized_fields = [normalize_csv_header(h) for h in reader.fieldnames]
            reader.fieldnames = normalized_fields
            if required_columns.issubset(set(normalized_fields)):
                return reader
            last_reader = reader

        # If the headers still do not match, return the last parsed reader so the
        # caller can provide a precise missing-column message.
        return last_reader


    try:
        uploaded_bytes = uploaded.getvalue()
        decoded = uploaded_bytes.decode("utf-8-sig")
        reader = read_csv_robustly(decoded)

        if reader is None or not reader.fieldnames:
            st.error("The CSV contains no header.")
            st.stop()

        required_columns = {"media_name", "publish_date", "url"}
        missing = required_columns - set(reader.fieldnames)

        if missing:
            st.error(
                "The uploaded CSV is missing required columns: "
                + ", ".join(sorted(missing))
                + ". Common spreadsheet exports can add quotes around headers; "
                  "the application already handles the most common variants."
            )
            st.stop()

        rows = []
        for row_number, row in enumerate(reader, 2):
            # Normalize keys once more for safety and discard empty trailing columns
            # created by spreadsheet exports.
            normalized_row = {
                normalize_csv_header(key): value
                for key, value in row.items()
                if normalize_csv_header(key)
            }
            normalized_row["_rawnb"] = row_number
            rows.append(normalized_row)

    except Exception as e:
        st.error(f"Unable to read the CSV: {type(e).__name__}: {e}")
        st.stop()

    if not rows:
        st.warning("The CSV contains no article records.")
        st.stop()

    source_counts = {}
    for row in rows:
        media_name = (row.get("media_name", "") or "").strip()
        if media_name:
            source_counts[media_name] = source_counts.get(media_name, 0) + 1

    if not source_counts:
        st.error("No usable `media_name` values were found.")
        st.stop()

    # Dataset overview.
    overview = st.columns(3)
    overview[0].metric("Input records", f"{len(rows):,}")
    overview[1].metric("Unique media names", f"{len(source_counts):,}")
    overview[2].metric("CSV size", f"{len(uploaded_bytes) / 1024:.1f} KB")


    def classify_for_corpus(media_name):
        """Apply the selected corpus-level classification policy."""
        if not use_press_classification:
            return None
        return classify_source(media_name)

    # ------------------------------------------------------------
    # Source selection
    # ------------------------------------------------------------
    st.markdown('<div class="section-title">2. Corpus scope</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="section-note">Choose which media sources should enter the extraction workflow.</div>',
        unsafe_allow_html=True,
    )

    sorted_sources = sorted(source_counts.items(), key=lambda item: item[0].lower())

    # Use widget state for each checkbox. This avoids the common Streamlit issue where
    # a button changes a separate set and the checkbox widgets immediately overwrite it.
    source_fingerprint = "|".join(f"{s}:{c}" for s, c in sorted_sources)
    if st.session_state.get("source_fingerprint") != source_fingerprint:
        st.session_state["source_fingerprint"] = source_fingerprint
        for idx, (source, _) in enumerate(sorted_sources):
            st.session_state[f"source_choice_{idx}"] = True

    def set_all_sources(value):
        for idx, _ in enumerate(sorted_sources):
            st.session_state[f"source_choice_{idx}"] = value


    # Selection controls are deliberately placed before the checkbox widgets so
    # Streamlit can update their session state cleanly on the next rerun.
    control_col1, control_col2, _ = st.columns([1, 1, 2])
    with control_col1:
        st.button(
            "Select all",
            key="select_all_sources",
            on_click=set_all_sources,
            args=(True,),
            use_container_width=True,
        )
    with control_col2:
        st.button(
            "Deselect all",
            key="deselect_all_sources",
            on_click=set_all_sources,
            args=(False,),
            use_container_width=True,
        )

    selected_sources = set()
    source_cols = st.columns(2)
    for i, (source, count) in enumerate(sorted_sources):
        with source_cols[i % 2]:
            checked = st.checkbox(
                f"{source}  ·  {count:,} articles",
                key=f"source_choice_{i}",
            )
            if checked:
                selected_sources.add(source)

    selected_rows = [
        row for row in rows
        if (row.get("media_name", "") or "").strip() in selected_sources
    ]

    s1, s2, s3 = st.columns(3)
    s1.metric("Selected sources", f"{len(selected_sources):,}")
    s2.metric("Selected records", f"{len(selected_rows):,}")
    s3.metric(
        "Share of input",
        f"{(100 * len(selected_rows) / len(rows)):.1f}%" if rows else "0.0%",
    )

    with st.expander("Review source classification", expanded=False):
        preview = []
        for source in sorted(selected_sources, key=str.lower):
            preview.append(
                {
                    "Source": source,
                    "Articles": source_counts[source],
                    "Classification": classify_for_corpus(source),
                }
            )
        if preview:
            st.dataframe(
                preview,
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.caption("No sources selected.")

    if not selected_rows:
        st.warning("Select at least one source to continue.")
        st.stop()

    # ------------------------------------------------------------
    # Custom metadata
    # ------------------------------------------------------------
    st.markdown('<div class="section-title">3. Custom metadata</div>', unsafe_allow_html=True)
    st.markdown(
        "<div class='section-note'>Optionally carry one or more additional CSV columns into each IRaMuTeQ document header. The column header becomes the metadata name, and each row value becomes that article's metadata category.</div>",
        unsafe_allow_html=True,
    )

    required_columns = {"media_name", "publish_date", "url"}
    custom_metadata_options = [
        column for column in reader.fieldnames
        if column not in required_columns and not column.startswith("_")
    ]

    custom_metadata_columns = st.multiselect(
        "Select CSV columns to add as metadata",
        options=custom_metadata_options,
        help="You can select multiple columns. For example, a column named mediatype with values national/regional will produce *mediatype_national and *mediatype_regional in the IRaMuTeQ headers.",
    )

    custom_metadata_fields = prepare_custom_metadata_fields(custom_metadata_columns)

    if custom_metadata_fields:
        st.caption(
            "Selected columns are preserved as separate metadata fields. "
            "Accents are removed and spaces/punctuation are converted to underscores "
            "for IRaMuTeQ-safe names and values; empty cells become `missing`."
        )

        preview_row = selected_rows[0] if selected_rows else rows[0]
        metadata_preview = []
        for original_column, field_name in custom_metadata_fields:
            raw_value = preview_row.get(original_column, "")
            safe_value = clean_custom_metadata_token(raw_value)
            metadata_preview.append(
                {
                    "CSV column": original_column,
                    "IRaMuTeQ metadata": f"*{field_name}_{safe_value}",
                }
            )

        with st.expander("Preview custom metadata", expanded=True):
            st.dataframe(metadata_preview, use_container_width=True, hide_index=True)
    else:
        st.caption(
            "No custom metadata selected. The corpus will use the built-in source/date/rawnb metadata, plus type only when Greek classification is enabled."
        )

    # ------------------------------------------------------------
    # Run
    # ------------------------------------------------------------
    st.markdown('<div class="section-title">4. Corpus construction</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="section-note">'
        'The application will deduplicate URLs, retrieve article pages, extract text, '
        'and construct IRaMuTeQ metadata headers.'
        '</div>',
        unsafe_allow_html=True,
    )

    with st.expander("What will be produced?", expanded=False):
        st.markdown('<div class="metadata-card">', unsafe_allow_html=True)
        st.markdown(
            """
            **IRaMuTeQ corpus**

            Each successfully extracted article receives five core metadata fields:

            - `source` — cleaned name of the publication or media source.
            - `year` — publication year.
            - `yearmonth` — publication year and month, in `YYYY-MM` format.
            - `type` — built-in source category for Greek corpora: national press or regional press. It is omitted entirely for other-language corpora.
            - `rawnb` — original row number of the article in the uploaded CSV, useful for tracing the corpus entry back to the source data.

            **Custom metadata**

            Any additional CSV columns selected above are appended to the same IRaMuTeQ header. The CSV header becomes the metadata name and each cell becomes that article's category. Multiple classifications can be selected at once, such as `mediatype`, `region`, `ownership`, or `political_orientation`.

            **Publication statistics**

            Counts are calculated from the selected MediaCloud records before URL
            deduplication and article extraction.

            **Failure log**

            Records missing metadata, invalid URLs, request failures, extraction
            failures, short articles, and unexpected errors.
            """
        )
        st.markdown('</div>', unsafe_allow_html=True)

    run = st.button(
        "Begin corpus construction",
        type="primary",
        use_container_width=True,
        disabled=bool(st.session_state.get("active_extraction_job_id")),
    )


    def build_statistics_tables(selected_rows, corpus_text):
        """Create initial and saved year x media matrices.

        Initial counts come from the selected CSV before URL deduplication.
        Saved counts are reconstructed from the generated IRaMuTeQ TXT corpus by
        reading each saved article header and using its rawnb to recover the
        original CSV media_name/year. This makes the saved table reflect exactly
        what was actually written to the corpus.
        """
        initial_counts = defaultdict(lambda: defaultdict(int))
        years = set()
        sources = set()
        invalid_date_rows = 0

        row_lookup = {}
        for row in selected_rows:
            media = (row.get("media_name", "") or "").strip()
            raw_number = str(row.get("_rawnb", "") or "").strip()
            year = extract_year((row.get("publish_date", "") or "").strip())
            if raw_number:
                row_lookup[raw_number] = (media, year)
            if not media or not year:
                if media and not year:
                    invalid_date_rows += 1
                continue
            initial_counts[year][media] += 1
            years.add(year)
            sources.add(media)

        # Reconstruct saved counts directly from the generated TXT corpus.
        # Every saved article has a header containing *year_YYYY and *rawnb_N.
        saved_counts = defaultdict(lambda: defaultdict(int))
        header_re = re.compile(r"^\*\*\*\*.*?\*year_(\d{4}).*?\*rawnb_([^\s]+)")
        for line in corpus_text.splitlines():
            if not line.startswith("****"):
                continue
            match = header_re.search(line)
            if not match:
                continue
            header_year, raw_number = match.groups()
            raw_number = raw_number.strip()
            media_year = row_lookup.get(raw_number)
            if media_year:
                media, csv_year = media_year
                if media:
                    # Prefer the original CSV publication year, while falling back
                    # to the header year if necessary.
                    year = csv_year or header_year
                    if year:
                        saved_counts[year][media] += 1
                        years.add(year)
                        sources.add(media)

        def matrix_csv(counts):
            output = io.StringIO()
            writer = csv.writer(output)
            ordered_sources = sorted(sources, key=str.lower)
            writer.writerow(["year"] + ordered_sources)
            for year in sorted(years, key=int):
                writer.writerow([year] + [counts[year].get(source, 0) for source in ordered_sources])
            return output.getvalue().encode("utf-8-sig")

        return matrix_csv(initial_counts), matrix_csv(saved_counts), invalid_date_rows


    def build_interactive_plot_data(initial_rows, saved_rows):
        """Return data keyed by media for the interactive year-by-year comparison."""
        initial_by_media = defaultdict(dict)
        saved_by_media = defaultdict(dict)

        for row in initial_rows:
            year = str(row.get("year", ""))
            if not year:
                continue
            for media, value in row.items():
                if media == "year":
                    continue
                initial_by_media[media][year] = int(value or 0)

        for row in saved_rows:
            year = str(row.get("year", ""))
            if not year:
                continue
            for media, value in row.items():
                if media == "year":
                    continue
                saved_by_media[media][year] = int(value or 0)

        return initial_by_media, saved_by_media


    def build_statistics_plot_png(initial_rows, saved_rows, selected_media=None):
        """Create a fallback downloadable PNG for one selected media."""
        initial_by_media, saved_by_media = build_interactive_plot_data(initial_rows, saved_rows)
        media = selected_media or (sorted(initial_by_media, key=str.lower)[0] if initial_by_media else None)
        if not media:
            return None
        years = sorted(set(initial_by_media.get(media, {})) | set(saved_by_media.get(media, {})), key=int)
        if not years:
            return None

        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(10, 5.5))
        ax.plot(years, [initial_by_media[media].get(y, 0) for y in years], marker="o", linewidth=2, label="Initial articles")
        ax.plot(years, [saved_by_media[media].get(y, 0) for y in years], marker="o", linewidth=2, label="Articles saved")
        ax.set_title(f"Initial vs. saved articles — {media}")
        ax.set_xlabel("Publication year")
        ax.set_ylabel("Number of articles")
        ax.grid(axis="y", alpha=0.25)
        ax.legend()
        fig.tight_layout()
        buffer = BytesIO()
        fig.savefig(buffer, format="png", dpi=180, bbox_inches="tight")
        plt.close(fig)
        return buffer.getvalue()

    def extraction_worker(job_id, selected_rows, custom_metadata_fields, delay, min_article_length, use_press_classification):
        with EXTRACTION_JOBS_LOCK:
            job = EXTRACTION_JOBS[job_id]
            job["status"] = "running"

        runtime_output = "news_iramuteq.txt"
        runtime_failed = "failed_articles.txt"
        runtime_stats = "publication_statistics_initial_vs_saved.csv"

        # Deduplicate URLs, preserving the original CSV row for every unique URL.
        unique_rows = []
        seen = set()
        duplicate_count = 0
        for row in selected_rows:
            raw_url = (row.get("url", "") or "").strip()
            if not valid_url(raw_url):
                unique_rows.append(row)
                continue
            normalized_url = clean_url(raw_url)
            if normalized_url in seen:
                duplicate_count += 1
                continue
            seen.add(normalized_url)
            unique_rows.append(row)

        output_buffer = io.StringIO()
        failed_buffer = io.StringIO()
        saved_counts = defaultdict(lambda: defaultdict(int))

        successful = errors = missing_metadata = request_errors = extraction_errors = 0
        short_articles = unexpected_errors = 0
        national_count = regional_count = unclassified_count = 0
        processed = 0
        cancelled = False

        def update(**kwargs):
            with EXTRACTION_JOBS_LOCK:
                job.update(kwargs)

        def write_failure_web(row_number, media_name, publish_date, url, reason):
            failed_buffer.write(f"[ROW {row_number}]\n")
            failed_buffer.write(f"source: {media_name}\n")
            failed_buffer.write(f"publish_date: {publish_date}\n")
            failed_buffer.write(f"url: {url}\n")
            failed_buffer.write(f"error: {reason}\n")
            failed_buffer.write("-" * 70 + "\n\n")

        session = requests.Session()
        total = len(unique_rows)

        try:
            for number, row in enumerate(unique_rows, 1):
                if job["cancel_event"].is_set():
                    cancelled = True
                    break

                raw_number = row.get("_rawnb")
                media_name = (row.get("media_name", "") or "").strip()
                publish_date = (row.get("publish_date", "") or "").strip()
                raw_url = (row.get("url", "") or "").strip()
                update(processed=number - 1, total=total, current=f"{media_name} · MediaCloud row {raw_number}")

                if not media_name:
                    write_failure_web(raw_number, media_name, publish_date, raw_url, "Missing media_name")
                    errors += 1; missing_metadata += 1
                    continue

                year, month = extract_year_month(publish_date)
                if not year:
                    write_failure_web(raw_number, media_name, publish_date, raw_url, "Could not parse publish_date")
                    errors += 1; missing_metadata += 1
                    continue

                if not valid_url(raw_url):
                    write_failure_web(raw_number, media_name, publish_date, raw_url, "Invalid or missing URL")
                    errors += 1; missing_metadata += 1
                    continue

                url = clean_url(raw_url)
                try:
                    response = session.get(url, headers=HEADERS, timeout=30)
                    response.raise_for_status()
                    article = trafilatura.extract(
                        response.text, url=url, include_comments=False,
                        include_tables=False, include_images=False,
                        include_links=False, favor_precision=True, output_format="txt",
                    )

                    if not article:
                        write_failure_web(raw_number, media_name, publish_date, raw_url, "Trafilatura returned no text")
                        errors += 1; extraction_errors += 1
                    else:
                        article = clean_text(article)
                        if len(article) < min_article_length:
                            write_failure_web(raw_number, media_name, publish_date, raw_url,
                                              f"Extracted article is too short ({len(article)} characters; minimum is {min_article_length})")
                            errors += 1; short_articles += 1
                        else:
                            source = clean_source_name(media_name)
                            press_type = classify_source(media_name) if use_press_classification else None
                            if press_type == "nationalpress": national_count += 1
                            elif press_type == "regionalpress": regional_count += 1
                            elif press_type == "unclassified": unclassified_count += 1

                            custom_tokens = []
                            for original_column, field_name in custom_metadata_fields:
                                custom_tokens.append(f"*{field_name}_{clean_custom_metadata_token(row.get(original_column, ''))}")

                            header_parts = ["****", f"*source_{source}", f"*year_{year}", f"*yearmonth_{year}-{month}"]
                            if press_type is not None:
                                header_parts.append(f"*type_{press_type}")
                            header_parts.extend([f"*rawnb_{raw_number}", *custom_tokens])
                            output_buffer.write(" ".join(header_parts) + "\n")
                            output_buffer.write(article + "\n\n")
                            successful += 1
                            saved_counts[media_name][year] += 1

                            if press_type == "unclassified" and use_press_classification:
                                write_failure_web(raw_number, media_name, publish_date, raw_url,
                                                  "Source could not be classified as National Press or Regional Press")

                except requests.exceptions.RequestException as e:
                    write_failure_web(raw_number, media_name, publish_date, raw_url, f"Request error: {type(e).__name__}: {e}")
                    errors += 1; request_errors += 1
                except Exception as e:
                    write_failure_web(raw_number, media_name, publish_date, raw_url, f"Unexpected error: {type(e).__name__}: {e}")
                    errors += 1; unexpected_errors += 1

                processed = number
                update(processed=processed, successful=successful, errors=errors,
                       national_count=national_count, regional_count=regional_count,
                       unclassified_count=unclassified_count)
                if number < total:
                    for _ in range(max(0, int(delay * 10))):
                        if job["cancel_event"].is_set():
                            cancelled = True
                            break
                        time.sleep(0.1)
                    if cancelled:
                        break

            corpus_text = output_buffer.getvalue()
            initial_stats_bytes, saved_stats_bytes, invalid_date_rows = build_statistics_tables(selected_rows, corpus_text)
            initial_stats_rows = list(csv.DictReader(StringIO(initial_stats_bytes.decode("utf-8-sig"))))
            saved_stats_rows = list(csv.DictReader(StringIO(saved_stats_bytes.decode("utf-8-sig"))))
            plot_bytes = build_statistics_plot_png(initial_stats_rows, saved_stats_rows)

            corpus_bytes = corpus_text.encode("utf-8")
            failed_bytes = failed_buffer.getvalue().encode("utf-8")
            result = {
                "corpus": corpus_bytes, "failed": failed_bytes,
                "initial_stats": initial_stats_bytes, "saved_stats": saved_stats_bytes,
                "plot": plot_bytes, "successful": successful, "errors": errors,
                "duplicate_count": duplicate_count, "national_count": national_count,
                "regional_count": regional_count, "unclassified_count": unclassified_count,
                "rows_processed": processed, "missing_metadata": missing_metadata,
                "request_errors": request_errors, "extraction_errors": extraction_errors,
                "short_articles": short_articles, "unexpected_errors": unexpected_errors,
                "invalid_date_rows": invalid_date_rows, "custom_metadata_columns": [x[0] for x in custom_metadata_fields],
                "cancelled": cancelled,
            }
            update(status="cancelled" if cancelled else "completed", result=result, processed=processed)
        except Exception as e:
            update(status="error", error=f"{type(e).__name__}: {e}")
        finally:
            session.close()


    if run:
        job_id = uuid.uuid4().hex
        with EXTRACTION_JOBS_LOCK:
            EXTRACTION_JOBS[job_id] = {
                "status": "starting", "processed": 0, "total": 0,
                "successful": 0, "errors": 0, "current": "Preparing requests…",
                "cancel_event": threading.Event(),
            }
        st.session_state["active_extraction_job_id"] = job_id
        worker = threading.Thread(
            target=extraction_worker,
            args=(job_id, selected_rows, custom_metadata_fields, delay, min_article_length, use_press_classification),
            daemon=True,
        )
        worker.start()


    @st.fragment(run_every="1s")
    def extraction_monitor():
        job_id = st.session_state.get("active_extraction_job_id") or st.session_state.get("last_extraction_job_id")
        if not job_id:
            return
        with EXTRACTION_JOBS_LOCK:
            job = EXTRACTION_JOBS.get(job_id)
        if not job:
            st.session_state.pop("active_extraction_job_id", None)
            return

        st.markdown("### Extraction progress")
        if job["status"] in {"starting", "running"}:
            total = max(job.get("total", 0), 1)
            done = min(job.get("processed", 0), total)
            st.progress(done / total, text=f"Processed {done:,} / {total:,} · {job.get('current', '')}")
            c1, c2 = st.columns([1, 3])
            with c1:
                if st.button("Cancel extraction", type="secondary", use_container_width=True, key=f"cancel_{job_id}"):
                    job["cancel_event"].set()
                    job["status"] = "cancelling"
            with c2:
                st.caption("Cancellation stops the workflow after the current web request finishes. You can then change the parameters and start again without refreshing the page.")
            return

        if job["status"] == "cancelling":
            st.info("Cancelling extraction… the current request will finish, then processing will stop.")
            return

        if job["status"] == "error":
            st.error(f"Extraction failed: {job.get('error', 'Unknown error')}")
            st.session_state.pop("active_extraction_job_id", None)
            return

        out = job["result"]
        st.progress(1.0, text="Extraction stopped." if out["cancelled"] else "Corpus construction complete.")
        if out["cancelled"]:
            st.warning(f"Extraction cancelled after {out['rows_processed']:,} processed records. The partial corpus and statistics below are available for inspection/download.")
        else:
            st.success("Corpus construction completed.")

        result_cols = st.columns(5)
        result_cols[0].metric("Articles saved", f"{out['successful']:,}")
        result_cols[1].metric("Articles failed", f"{out['errors']:,}")
        result_cols[2].metric("Duplicate URLs", f"{out['duplicate_count']:,}")
        result_cols[3].metric("National press", f"{out['national_count']:,}")
        result_cols[4].metric("Regional press", f"{out['regional_count']:,}")

        st.markdown("### Publication statistics")
        st.caption("Initial = records in the selected CSV. Saved = articles actually present in the generated .txt corpus, reconstructed from each IRaMuTeQ header and its original rawnb row number. Both tables use the same year × media format.")
        table_cols_1 = st.columns([4, 1])
        with table_cols_1[0]:
            st.markdown("#### Initial articles in the selected CSV")
        with table_cols_1[1]:
            st.download_button("⬇ CSV", data=out["initial_stats"], file_name="publication_counts_initial.csv", mime="text/csv", use_container_width=True, key=f"initial_table_csv_{job_id}", help="Download this table as CSV")
        st.dataframe(list(csv.DictReader(StringIO(out["initial_stats"].decode("utf-8-sig")))), use_container_width=True, hide_index=True)

        table_cols_2 = st.columns([4, 1])
        with table_cols_2[0]:
            st.markdown("#### Articles successfully saved")
        with table_cols_2[1]:
            st.download_button("⬇ CSV", data=out["saved_stats"], file_name="publication_counts_saved.csv", mime="text/csv", use_container_width=True, key=f"saved_table_csv_{job_id}", help="Download this table as CSV")
        st.dataframe(list(csv.DictReader(StringIO(out["saved_stats"].decode("utf-8-sig")))), use_container_width=True, hide_index=True)

        initial_rows_for_plot = list(csv.DictReader(StringIO(out["initial_stats"].decode("utf-8-sig"))))
        saved_rows_for_plot = list(csv.DictReader(StringIO(out["saved_stats"].decode("utf-8-sig"))))
        initial_by_media, saved_by_media = build_interactive_plot_data(initial_rows_for_plot, saved_rows_for_plot)
        media_options = sorted(initial_by_media.keys(), key=str.lower)
        if media_options:
            selected_plot_media = st.selectbox("Choose a media source for the comparison", media_options, key=f"plot_media_monitor_{job_id}")
            years = sorted(set(initial_by_media.get(selected_plot_media, {})) | set(saved_by_media.get(selected_plot_media, {})), key=int)
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=years, y=[initial_by_media[selected_plot_media].get(y, 0) for y in years], mode="lines+markers", name="Initial articles"))
            fig.add_trace(go.Scatter(x=years, y=[saved_by_media[selected_plot_media].get(y, 0) for y in years], mode="lines+markers", name="Articles saved"))
            fig.update_layout(title=f"Initial vs. saved articles — {selected_plot_media}", xaxis_title="Year", yaxis_title="Number of articles", hovermode="x unified")
            st.plotly_chart(fig, use_container_width=True, key=f"plot_monitor_{job_id}")

        d1, d2 = st.columns(2)
        with d1:
            st.download_button("Download IRaMuTeQ corpus", data=out["corpus"], file_name="news_iramuteq.txt", mime="text/plain", use_container_width=True, key=f"corpus_{job_id}")
        with d2:
            st.download_button("Download failure log", data=out["failed"], file_name="failed_articles.txt", mime="text/plain", use_container_width=True, key=f"failed_{job_id}")

        # Keep results available after any subsequent full-page rerun/download.

        st.session_state["research_outputs"] = out
        st.session_state["last_extraction_job_id"] = job_id
        st.session_state.pop("active_extraction_job_id", None)

    extraction_monitor()

    # ------------------------------------------------------------
    # Persistent research outputs
    # ------------------------------------------------------------
    # Streamlit reruns the script when a download button is pressed. Keep the generated
    # files in session state so the results section remains available for subsequent downloads.
    if st.session_state.get("research_outputs") and not run:
        out = st.session_state["research_outputs"]
        st.markdown('<div class="section-title">5. Research outputs</div>', unsafe_allow_html=True)
        st.success("Corpus construction is complete. Your generated files remain available for download in this session.")

        r = st.columns(5)
        r[0].metric("Articles saved", f"{out['successful']:,}")
        r[1].metric("Articles failed", f"{out['errors']:,}")
        r[2].metric("Duplicate URLs", f"{out['duplicate_count']:,}")
        r[3].metric("National press", f"{out['national_count']:,}")
        r[4].metric("Regional press", f"{out['regional_count']:,}")

        if out["successful"]:
            st.caption(f"Extraction success rate: **{100 * out['successful'] / out['rows_processed']:.1f}%** ({out['successful']:,} of {out['rows_processed']:,} processed records).")

        st.markdown("### Publication statistics")
        st.caption("Initial counts come from the selected CSV. Saved counts are reconstructed from the generated .txt corpus using each article header and its original MediaCloud row number (rawnb).")
        table_cols_1 = st.columns([4, 1])
        with table_cols_1[0]:
            st.markdown("#### Initial articles in the selected CSV")
        with table_cols_1[1]:
            st.download_button("⬇ CSV", data=out["initial_stats"], file_name="publication_counts_initial.csv", mime="text/csv", use_container_width=True, key="initial_table_csv_persistent", help="Download this table as CSV")
        st.dataframe(list(csv.DictReader(StringIO(out["initial_stats"].decode("utf-8-sig")))), use_container_width=True, hide_index=True)

        table_cols_2 = st.columns([4, 1])
        with table_cols_2[0]:
            st.markdown("#### Articles successfully saved")
        with table_cols_2[1]:
            st.download_button("⬇ CSV", data=out["saved_stats"], file_name="publication_counts_saved.csv", mime="text/csv", use_container_width=True, key="saved_table_csv_persistent", help="Download this table as CSV")
        st.dataframe(list(csv.DictReader(StringIO(out["saved_stats"].decode("utf-8-sig")))), use_container_width=True, hide_index=True)

        initial_rows_for_plot = list(csv.DictReader(StringIO(out["initial_stats"].decode("utf-8-sig"))))
        saved_rows_for_plot = list(csv.DictReader(StringIO(out["saved_stats"].decode("utf-8-sig"))))
        initial_by_media, saved_by_media = build_interactive_plot_data(initial_rows_for_plot, saved_rows_for_plot)
        media_options = sorted(initial_by_media.keys(), key=str.lower)
        if media_options:
            selected_plot_media = st.selectbox("Choose a media source", media_options, key="plot_media_persistent")
            years = sorted(set(initial_by_media.get(selected_plot_media, {})) | set(saved_by_media.get(selected_plot_media, {})), key=int)
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=years, y=[initial_by_media[selected_plot_media].get(y, 0) for y in years], mode="lines+markers", name="Initial articles"))
            fig.add_trace(go.Scatter(x=years, y=[saved_by_media[selected_plot_media].get(y, 0) for y in years], mode="lines+markers", name="Articles saved"))
            fig.update_layout(title=f"Initial vs. saved articles — {selected_plot_media}", xaxis_title="Publication year", yaxis_title="Number of articles", hovermode="x unified")
            st.plotly_chart(fig, use_container_width=True)

        st.markdown("### Download research outputs")
        d1, d2 = st.columns(2)
        with d1:
            st.download_button("Download IRaMuTeQ corpus", data=out["corpus"], file_name="news_iramuteq.txt", mime="text/plain", use_container_width=True, key="download_corpus_persistent")
        with d2:
            st.download_button("Download failure log", data=out["failed"], file_name="failed_articles.txt", mime="text/plain", use_container_width=True, key="download_failed_persistent")

        if out.get("plot"):
            st.image(out["plot"], caption="Initial MediaCloud records vs. articles saved by publication year", use_container_width=True)

        with st.expander("Diagnostics", expanded=False):
            st.dataframe([
                {"Failure category": "Metadata errors", "Count": out["missing_metadata"]},
                {"Failure category": "Request errors", "Count": out["request_errors"]},
                {"Failure category": "Extraction errors", "Count": out["extraction_errors"]},
                {"Failure category": "Short articles", "Count": out["short_articles"]},
                {"Failure category": "Unexpected errors", "Count": out["unexpected_errors"]},
            ], use_container_width=True, hide_index=True)
            if out["invalid_date_rows"]:
                st.warning(f"{out['invalid_date_rows']:,} selected rows had unparseable dates and were omitted from publication statistics.")
            if out["unclassified_count"]:
                st.warning(f"{out['unclassified_count']:,} successfully extracted articles came from sources not present in the built-in National/Regional Press lists.")

        with st.expander("Methodological notes", expanded=False):
            st.markdown(
                """
                **Publication statistics** are calculated directly from the selected MediaCloud records before duplicate URL removal and before article extraction.

                **Corpus counts** can therefore differ from publication statistics because records may be excluded for duplicate URLs, invalid metadata, inaccessible pages, extraction failures, or insufficient text length.

                **IRaMuTeQ metadata** retain the original MediaCloud row number through the `rawnb` field, allowing the resulting corpus to be traced back to the source CSV.
                """
            )

    st.markdown(
        '<div class="footer-line">'
        'MediaCloud → IRaMuTeQ · corpus preparation interface'
        '</div>',
        unsafe_allow_html=True,
    )


# ============================================================
# UNIFIED APP SHELL
# ============================================================

def main():
    st.set_page_config(
        page_title="IRaMuTeQ Corpus Builder",
        page_icon="◈",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    st.markdown("""
    <style>
    :root { --ink:#172033; --muted:#667085; --line:#d9dee8; --paper:#fbfcfe; }
    .stApp { background:var(--paper); }
    .block-container { max-width:1180px; padding-top:2.2rem; padding-bottom:4rem; }
    .research-kicker { font-size:.78rem; letter-spacing:.14em; text-transform:uppercase; color:#667085; font-weight:700; margin-bottom:.5rem; }
    .research-title { font-family:Georgia,"Times New Roman",serif; font-size:clamp(2.2rem,4vw,3.65rem); line-height:1.05; color:#111!important; margin:0; font-weight:600; }
    .research-subtitle { font-size:1.08rem; line-height:1.65; color:#444!important; max-width:900px; margin-top:1rem; }
    .section-title { font-family:Georgia,"Times New Roman",serif; font-size:1.55rem; color:#111!important; margin:2.2rem 0 .35rem; }
    .section-note { color:#444!important; margin-bottom:1rem; }
    .method-card { border:1px solid var(--line); border-radius:12px; padding:1.1rem 1.2rem; background:#fff; min-height:125px; }
    .method-number { font-size:.75rem; letter-spacing:.08em; text-transform:uppercase; color:#667085; font-weight:700; }
    .method-heading { font-family:Georgia,"Times New Roman",serif; color:#111!important; font-size:1.12rem; margin-top:.35rem; }
    .method-text { color:#444!important; font-size:.91rem; line-height:1.45; }
    .citation-box { border-left:3px solid #172033; background:#fff; padding:.85rem 1rem; color:#475467; font-size:.9rem; line-height:1.55; margin:1rem 0 1.5rem; }
    .footer-line { border-top:1px solid var(--line); margin-top:3rem; padding-top:1rem; color:#667085; font-size:.82rem; }
    [data-testid="stSidebar"] { background:#111!important; }
    [data-testid="stSidebar"] * { color:#fff!important; }
    /* All actionable buttons use a dark background with white text/icons. */
    .stButton > button, .stDownloadButton > button, [data-testid="stFormSubmitButton"] > button,
    [data-testid="stFileUploader"] button, [data-testid="stFileUploader"] [role="button"] {
        background-color:#111!important; color:#fff!important; -webkit-text-fill-color:#fff!important; border:1px solid #111!important; border-radius:8px; font-weight:600;
    }
    .stButton > button *, .stDownloadButton > button *, [data-testid="stFormSubmitButton"] > button *,
    [data-testid="stFileUploader"] button *, [data-testid="stFileUploader"] [role="button"] * {
        color:#fff!important; -webkit-text-fill-color:#fff!important; fill:#fff!important; stroke:#fff!important;
    }
    .stButton > button:hover, .stDownloadButton > button:hover { background-color:#2b2b2b!important; color:#fff!important; }
    /* Code blocks: white text on a dark background so the **** header is always visible. */
    [data-testid="stCodeBlock"] pre, [data-testid="stCodeBlock"] code {
        background:#111!important; color:#fff!important; -webkit-text-fill-color:#fff!important;
    }
    [data-testid="stCodeBlock"] pre { border:1px solid #333!important; border-radius:8px!important; }
    [data-testid="stCodeBlock"] code * { color:#fff!important; -webkit-text-fill-color:#fff!important; }
    [data-testid="stCheckbox"] label, [data-testid="stCheckbox"] label p, [data-testid="stCheckbox"] label span { color:#111!important; }
    [data-testid="stFileUploader"] section, [data-testid="stFileUploaderDropzone"], [data-testid="stFileUploaderDropzoneInstructions"], [data-testid="stFileUploaderFileName"], [data-testid="stFileUploaderFileName"] * { color:#111!important; -webkit-text-fill-color:#111!important; }
    [data-testid="stFileUploaderDropzone"] { background:#fff!important; border-color:#777!important; }
    div[data-testid="stSelectbox"] [data-baseweb="select"], div[data-testid="stSelectbox"] [data-baseweb="select"] > div,
    div[data-testid="stSelectbox"] [data-baseweb="select"] * { background-color:#111!important; color:#fff!important; -webkit-text-fill-color:#fff!important; border-color:#555!important; }
    div[data-testid="stSelectbox"] [data-baseweb="select"] svg { fill:#fff!important; color:#fff!important; }
    [data-baseweb="popover"] [role="option"], [data-baseweb="popover"] [role="option"] * { color:#fff!important; background:#111!important; }
    [data-baseweb="popover"] [role="option"]:hover { background:#2b2b2b!important; }
    .metadata-card, .metadata-card * { color:#fff!important; }
    </style>
    """, unsafe_allow_html=True)

    source_mode = render_landing_and_mode_selector()
    with st.sidebar:
        st.markdown("### Corpus builder")
        st.caption("Select a source above. The MediaCloud mode uses the original supplied application workflow; the other modes use flexible CSV mapping.")
        st.markdown("### Existing MediaCloud app")
        st.markdown("[MediaCloud → IRaMuTeQ](https://mediacloud-iramuteq.streamlit.app/)")

    if source_mode == "MediaCloud":
        render_mediacloud_original()
    else:
        render_flexible_mode(source_mode)

    # Final accessibility overrides are deliberately placed after the original
    # MediaCloud stylesheet, because the original app also defines broad text
    # rules. These ensure buttons and code examples remain readable.
    st.markdown("""
    <style>
    .stButton > button, .stDownloadButton > button, [data-testid="stFormSubmitButton"] > button,
    .stButton > button *, .stDownloadButton > button *, [data-testid="stFormSubmitButton"] > button * {
        color:#fff!important; -webkit-text-fill-color:#fff!important;
    }
    [data-testid="stCodeBlock"] pre, [data-testid="stCodeBlock"] code,
    [data-testid="stCodeBlock"] code * {
        background:#111!important; color:#fff!important; -webkit-text-fill-color:#fff!important;
    }
    </style>
    """, unsafe_allow_html=True)

    st.markdown('<div class="footer-line">IRaMuTeQ Corpus Builder · MediaCloud · CrowdTangle · generic CSV</div>', unsafe_allow_html=True)


if __name__ == "__main__":
    main()
