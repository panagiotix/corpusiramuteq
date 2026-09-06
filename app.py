#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unified IRaMuTeQ corpus builder.

Modes:
- CrowdTangle -> IRaMuTeQ (flexible column mapping + CrowdTangle-specific cleaning)
- Any CSV -> IRaMuTeQ (one text column + at least one metadata column)
"""

import csv
import io
import re
import time
import unicodedata
import threading
import uuid
from datetime import datetime
from io import StringIO, BytesIO

import streamlit as st


# ============================================================
# CONFIGURATION
# ============================================================
DELAY = 1.5
MIN_ARTICLE_LENGTH = 100
EXTRACTION_JOBS = {}
EXTRACTION_JOBS_LOCK = threading.Lock()

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "el-GR,el;q=0.9,en;q=0.8",
}

def normalize_csv_header(value):
    value = "" if value is None else str(value)
    value = value.replace("\ufeff", "").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1].strip()
    return value


def detect_delimiter(decoded):
    sample = "\n".join([x for x in decoded.splitlines() if x.strip()][:10])
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|:").delimiter
    except csv.Error:
        first = next((x for x in decoded.splitlines() if x.strip()), "")
        counts = {d: first.count(d) for d in [",", ";", "\t", "|"]}
        return max(counts, key=counts.get) if max(counts.values(), default=0) else ","


def read_csv_robustly(decoded):
    delimiter = detect_delimiter(decoded)
    first_line = next((line for line in decoded.splitlines() if line.strip()), "")
    quote_attempts = ["'", '"'] if first_line.lstrip().startswith("'") else ['"', "'"]
    last_reader = None
    for quotechar in quote_attempts:
        reader = csv.DictReader(io.StringIO(decoded), delimiter=delimiter, quotechar=quotechar)
        if not reader.fieldnames:
            continue
        reader.fieldnames = [normalize_csv_header(h) for h in reader.fieldnames]
        if any(reader.fieldnames):
            return reader, delimiter
        last_reader = reader
    return last_reader, delimiter


def read_uploaded_csv(uploaded):
    raw = uploaded.getvalue()
    decoded = raw.decode("utf-8-sig", errors="replace")
    reader, delimiter = read_csv_robustly(decoded)
    if reader is None or not reader.fieldnames:
        raise ValueError("The CSV contains no header.")
    rows = []
    for row_number, row in enumerate(reader, 2):
        normalized = {
            normalize_csv_header(k): ("" if v is None else v)
            for k, v in row.items() if normalize_csv_header(k)
        }
        normalized["_rawnb"] = row_number
        rows.append(normalized)
    return raw, reader.fieldnames, rows, delimiter


def clean_custom_metadata_token(value, fallback="missing"):
    value = "" if value is None else str(value).strip().lower()
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value or fallback


def prepare_custom_metadata_fields(columns):
    used = {}
    prepared = []
    for column in columns:
        base = clean_custom_metadata_token(column, fallback="metadata")
        count = used.get(base, 0) + 1
        used[base] = count
        field = base if count == 1 else f"{base}_{count}"
        prepared.append((column, field))
    return prepared


def clean_text(text, replace_asterisks=True):
    if text is None:
        return ""
    text = str(text).replace("\ufeff", "")
    # Tabs are reserved for IRaMuTeQ metadata.
    text = text.replace("\t", " ")
    # Asterisks are structural in IRaMuTeQ and therefore cannot be left in
    # CrowdTangle text. This is also the central cleaning rule in the supplied
    # CrowdTangle scripts.
    if replace_asterisks:
        text = text.replace("*", "_")
    text = text.replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n+", " ", text)
    return text.strip()


def clean_crowdtangle_text(text):
    """Apply the cleaning rule demonstrated in the supplied scripts."""
    return clean_text(text)


def extract_year_month(value):
    if not value:
        return None, None
    value = str(value).strip()
    # ISO-like dates first.
    m = re.search(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b", value)
    if m:
        year, month, day = m.group(1), m.group(2).zfill(2), m.group(3)
        try:
            datetime(int(year), int(month), int(day))
            return year, month
        except ValueError:
            pass
    m = re.search(r"\b(20\d{2})-(\d{1,2})\b", value)
    if m:
        year, month = m.group(1), m.group(2).zfill(2)
        try:
            datetime(int(year), int(month), 1)
            return year, month
        except ValueError:
            pass
    # Common slash/dot dates.
    for pattern in (r"\b(20\d{2})[/.](\d{1,2})[/.](\d{1,2})\b",
                    r"\b(\d{1,2})[/.](\d{1,2})[/.](20\d{2})\b"):
        m = re.search(pattern, value)
        if m:
            if m.group(1).startswith("20"):
                year, month, day = m.group(1), m.group(2).zfill(2), m.group(3)
            else:
                year, month, day = m.group(3), m.group(2).zfill(2), m.group(1)
            try:
                datetime(int(year), int(month), int(day))
                return year, month
            except ValueError:
                pass
    return None, None


def safe_metadata_field(name):
    return clean_custom_metadata_token(name, fallback="metadata")


def build_header(metadata_pairs):
    """metadata_pairs is a list of (field_name, value)."""
    parts = ["****"]
    for field, value in metadata_pairs:
        parts.append(f"*{safe_metadata_field(field)}_{clean_custom_metadata_token(value)}")
    return " ".join(parts)


def row_text_from_columns(row, columns, cleaner=clean_text, joiner="\n"):
    chunks = []
    for col in columns:
        value = cleaner(row.get(col, ""))
        if value:
            chunks.append(value)
    return joiner.join(chunks).strip()





def build_generic_corpus(rows, text_col, metadata_cols, cleaner):
    output = io.StringIO()
    log = io.StringIO()
    saved = failed = duplicates = empty_text = 0
    seen = set()
    for row in rows:
        rawnb = row.get("_rawnb", "")
        text = cleaner(row.get(text_col, ""))
        if not text:
            failed += 1; empty_text += 1
            log.write(f"[ROW {rawnb}]\nerror: Empty text in selected column '{text_col}'\n{'-'*70}\n\n")
            continue
        meta = [(c, row.get(c, "")) for c in metadata_cols]
        meta.append(("rawnb", rawnb))
        record = build_header(meta) + "\n" + text
        if record in seen:
            duplicates += 1
            log.write(f"[ROW {rawnb}]\nerror: Duplicate corpus record\n{'-'*70}\n\n")
            continue
        seen.add(record)
        output.write(record + "\n\n")
        saved += 1
    stats = {"input": len(rows), "saved": saved, "failed": failed, "duplicates": duplicates, "empty_text": empty_text}
    return output.getvalue().encode("utf-8"), log.getvalue().encode("utf-8"), stats


def build_crowdtangle_corpus(rows, text_col, group_col, date_col, desc_col, include_description, extra_cols):
    output = io.StringIO()
    log = io.StringIO()
    saved = failed = duplicates = empty_text = 0
    seen = set()
    for row in rows:
        rawnb = row.get("_rawnb", "")
        text = clean_crowdtangle_text(row.get(text_col, ""))
        if include_description and desc_col != "— none —":
            desc = clean_crowdtangle_text(row.get(desc_col, ""))
            if desc:
                text = (text + "\n" + desc).strip() if text else desc
        if not text:
            failed += 1; empty_text += 1
            log.write(f"[ROW {rawnb}]\nerror: Empty CrowdTangle text\n{'-'*70}\n\n")
            continue
        meta = []
        if group_col != "— none —":
            meta.append(("groupe", row.get(group_col, "")))
        if date_col != "— none —":
            date = str(row.get(date_col, "") or "").strip()
            meta.append(("date", date))
            year, month = extract_year_month(date)
            if year:
                meta.append(("year", year))
            if year and month:
                meta.append(("ym", f"{year}-{month}"))
        for c in extra_cols:
            meta.append((c, row.get(c, "")))
        meta.append(("rawnb", rawnb))
        record = build_header(meta) + "\n" + text
        if record in seen:
            duplicates += 1
            log.write(f"[ROW {rawnb}]\nerror: Duplicate CrowdTangle record\n{'-'*70}\n\n")
            continue
        seen.add(record)
        output.write(record + "\n\n")
        saved += 1
    stats = {"input": len(rows), "saved": saved, "failed": failed, "duplicates": duplicates, "empty_text": empty_text}
    return output.getvalue().encode("utf-8"), log.getvalue().encode("utf-8"), stats


def show_local_result(label, corpus, log, stats, corpus_name, log_name):
    st.session_state["last_local_result"] = {"label": label, "corpus": corpus, "log": log, "stats": stats, "corpus_name": corpus_name, "log_name": log_name}
    st.rerun()

# ============================================================
# UI
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
    .stApp p,.stApp label,.stApp span { color:#111; }
    /* Keep IRaMuTeQ examples/previews fully legible. Streamlit's syntax
       highlighting creates nested spans, so the global span rule above must
       not be allowed to turn parts of the corpus black. */
    [data-testid="stCode"] pre,
    [data-testid="stCode"] code,
    [data-testid="stCode"] code span {
        color:#fff !important;
        -webkit-text-fill-color:#fff !important;
        background:#191c24 !important;
    }
    [data-testid="stCode"] pre { overflow-x:auto !important; }
    [data-testid="stSidebar"] { background:#111!important; }
    [data-testid="stSidebar"] * { color:#fff!important; }
    .stButton>button,.stDownloadButton>button { border-radius:8px; font-weight:600; color:#fff !important; -webkit-text-fill-color:#fff !important; background:#111827 !important; border-color:#111827 !important; }
    .stButton>button:hover,.stDownloadButton>button:hover { color:#fff !important; -webkit-text-fill-color:#fff !important; background:#1f2937 !important; }
    .stButton>button *, .stDownloadButton>button * { color:#fff !important; -webkit-text-fill-color:#fff !important; fill:#fff !important; }
    [data-testid="stFileUploader"] button, [data-testid="stFileUploader"] button *, [data-testid="stFileUploader"] [role="button"], [data-testid="stFileUploader"] [role="button"] * { color:#fff !important; -webkit-text-fill-color:#fff !important; fill:#fff !important; background:#111827 !important; }
    [data-testid="stFileUploader"] small { color:#444 !important; }
    [data-testid="stRadio"] label, [data-testid="stRadio"] label p, [data-testid="stRadio"] label span { color:#111 !important; -webkit-text-fill-color:#111 !important; }
    .source-choice { border:1px solid var(--line); border-radius:12px; padding:1rem 1.1rem; background:#fff; }
    .source-choice-title { font-weight:700; color:#111; margin-bottom:.25rem; }
    .source-choice-text { color:#555; font-size:.92rem; line-height:1.45; }
    </style>
    """, unsafe_allow_html=True)

    st.markdown('<div class="research-kicker">Open research utility · corpus preparation</div>', unsafe_allow_html=True)
    st.markdown('<h1 class="research-title">IRaMuTeQ Corpus Builder</h1>', unsafe_allow_html=True)
    st.markdown(
        '<div class="research-subtitle">Prepare textual corpora for IRaMuTeQ from CrowdTangle exports or from any CSV file, with explicit control over the text and metadata fields.</div>',
        unsafe_allow_html=True,
    )

    st.markdown('<div class="citation-box"><strong>What is an IRaMuTeQ corpus?</strong> Each document begins with a line starting with <code>****</code>. Metadata variables are written as <code>*variable_value</code>, followed by the text of the document on the next line. The application cleans structural characters such as <code>*</code> and tabs before export.</div>', unsafe_allow_html=True)

    st.markdown('<div class="section-title">IRaMuTeQ text format</div>', unsafe_allow_html=True)
    st.code("""**** *source_example *year_2025 *country_greece *rawnb_12
This is the text of the first document.

**** *source_example *year_2025 *country_france *rawnb_13
This is the text of the second document.""", language=None)
    st.caption("The exact metadata variables depend on the source and the columns you select.")

    st.markdown('<div class="section-title">Choose your input source</div>', unsafe_allow_html=True)
    source_mode = st.radio(
        "Input source",
        ["CrowdTangle", "Any CSV"],
        horizontal=True,
        key="input_source_mode",
        label_visibility="collapsed",
    )

    st.markdown('<a href="https://mediacloud-iramuteq.streamlit.app/" target="_blank">MediaCloud → IRaMuTeQ app</a>', unsafe_allow_html=True)

    st.markdown('<div class="section-title">1. Corpus input</div>', unsafe_allow_html=True)
    if source_mode == "CrowdTangle":
        st.markdown("Upload a CrowdTangle CSV export. Column names may vary between exports; the application will suggest mappings that you can change.")
        uploader_help = "CrowdTangle exports with comma, semicolon, tab, or pipe delimiters are supported."
    else:
        st.markdown("Upload any CSV. You will choose exactly one text column and at least one metadata column.")
        uploader_help = "CSV files with comma, semicolon, tab, or pipe delimiters are supported."

    uploaded = st.file_uploader(
        "Upload CSV",
        type=["csv"],
        help=uploader_help,
        key="corpus_csv_uploader",
    )

    if uploaded is None:
        st.info("Upload a CSV to continue.")
        st.markdown('<div class="footer-line">IRaMuTeQ Corpus Builder · CrowdTangle · generic CSV</div>', unsafe_allow_html=True)
        return

    try:
        _, fieldnames, rows, detected_delimiter = read_uploaded_csv(uploaded)
    except Exception as e:
        st.error(f"Unable to read the CSV: {type(e).__name__}: {e}")
        return

    if not rows:
        st.warning("The CSV contains no records.")
        return

    st.caption(f"Detected delimiter: `{repr(detected_delimiter)}` · {len(fieldnames):,} columns · {len(rows):,} records")

    if source_mode == "CrowdTangle":
        st.markdown('<div class="section-title">2. Map CrowdTangle columns</div>', unsafe_allow_html=True)
        st.markdown("CrowdTangle exports can change their headers. The application therefore suggests mappings, but **you control the final mapping**.")

        def suggest_column(candidates):
            lowered = {c.lower().replace("_", " "): c for c in fieldnames}
            for cand in candidates:
                if cand in lowered:
                    return lowered[cand]
            for c in fieldnames:
                norm = c.lower().replace("_", " ")
                if any(cand in norm for cand in candidates):
                    return c
            return None

        text_default = suggest_column(["message", "post message", "post text", "text", "post content"])
        group_default = suggest_column(["page name", "group name", "page", "group", "account name"])
        date_default = suggest_column(["post created date", "created date", "created time", "post date", "date", "time"])
        desc_default = suggest_column(["page description", "description", "group description"])

        def select_with_optional(label, default, key):
            options = ["— none —"] + fieldnames
            index = options.index(default) if default in options else 0
            return st.selectbox(label, options, index=index, key=key)

        text_col = st.selectbox("Post text column *", fieldnames, index=fieldnames.index(text_default) if text_default in fieldnames else 0, key="ct_text")
        group_col = select_with_optional("Page / group name (recommended)", group_default, "ct_group")
        date_col = select_with_optional("Post date (recommended)", date_default, "ct_date")
        desc_col = select_with_optional("Page / group description (optional)", desc_default, "ct_desc")

        reserved = {text_col}
        for c in [group_col, date_col, desc_col]:
            if c != "— none —": reserved.add(c)
        extra_options = [c for c in fieldnames if c not in reserved]
        extra_cols = st.multiselect("Additional metadata columns (optional)", extra_options, key="ct_extra")

        st.markdown('<div class="section-title">3. CrowdTangle cleaning</div>', unsafe_allow_html=True)
        st.markdown("The supplied CrowdTangle scripts are used as the cleaning reference: asterisks in text are replaced with underscores; page/group names are sanitized; dates can generate year and year-month; duplicate entries are removed.")
        include_description = desc_col != "— none —"
        if include_description:
            st.checkbox("Append the selected description to each post", value=True, key="ct_include_desc")
        else:
            st.session_state["ct_include_desc"] = False

        metadata_candidates = []
        if group_col != "— none —": metadata_candidates.append(("groupe", group_col))
        if date_col != "— none —": metadata_candidates.append(("date", date_col))
        for c in extra_cols:
            metadata_candidates.append((c, c))
        if not metadata_candidates:
            st.error("CrowdTangle mode requires at least one metadata field. Select a page/group, date, or another metadata column.")
            return

        st.markdown('<div class="section-title">4. Preview</div>', unsafe_allow_html=True)
        p = rows[0]
        text = clean_crowdtangle_text(p.get(text_col, ""))
        if st.session_state.get("ct_include_desc") and desc_col != "— none —":
            desc = clean_crowdtangle_text(p.get(desc_col, ""))
            if desc:
                text = (text + "\n" + desc).strip()
        meta = []
        if group_col != "— none —": meta.append(("groupe", p.get(group_col, "")))
        if date_col != "— none —":
            date = str(p.get(date_col, "") or "").strip()
            year, month = extract_year_month(date)
            meta.append(("date", date))
            if year: meta.append(("year", year))
            if year and month: meta.append(("ym", f"{year}-{month}"))
        for c in extra_cols: meta.append((c, p.get(c, "")))
        meta.append(("rawnb", p.get("_rawnb", "")))
        st.code(build_header(meta) + "\n" + text, language=None)

        if st.button("Build CrowdTangle corpus", type="primary", use_container_width=True):
            corpus, log, stats = build_crowdtangle_corpus(
                rows, text_col, group_col, date_col, desc_col,
                bool(st.session_state.get("ct_include_desc")), extra_cols
            )
            show_local_result("CrowdTangle", corpus, log, stats, "crowdtangle_iramuteq.txt", "crowdtangle_processing_log.txt")

    else:
        st.markdown('<div class="section-title">2. Map CSV columns</div>', unsafe_allow_html=True)
        st.markdown("For a generic CSV, the only structural assumptions are **one text column** and **at least one metadata column**. You decide which columns serve those roles.")
        text_col = st.selectbox("Text column *", fieldnames, key="generic_text")
        metadata_options = [c for c in fieldnames if c != text_col]
        metadata_cols = st.multiselect("Metadata column(s) * — select at least one", metadata_options, key="generic_meta")
        if not metadata_cols:
            st.info("Select at least one metadata column to continue.")
            return

        st.markdown('<div class="section-title">3. Text cleaning</div>', unsafe_allow_html=True)
        clean_asterisks = st.checkbox("Replace `*` with `_` in text (recommended for IRaMuTeQ)", value=True)
        generic_cleaner = lambda x: clean_text(x, replace_asterisks=clean_asterisks)

        st.markdown('<div class="section-title">4. Preview</div>', unsafe_allow_html=True)
        p = rows[0]
        preview_text = generic_cleaner(p.get(text_col, ""))
        preview_meta = [(c, p.get(c, "")) for c in metadata_cols]
        preview_meta.append(("rawnb", p.get("_rawnb", "")))
        st.code(build_header(preview_meta) + "\n" + preview_text, language=None)

        if st.button("Build generic CSV corpus", type="primary", use_container_width=True):
            corpus, log, stats = build_generic_corpus(rows, text_col, metadata_cols, generic_cleaner)
            show_local_result("Generic CSV", corpus, log, stats, "csv_iramuteq.txt", "csv_processing_log.txt")

    if st.session_state.get("last_local_result"):
        result = st.session_state["last_local_result"]
        st.markdown('<div class="section-title">5. Research outputs</div>', unsafe_allow_html=True)
        st.success(f"{result['label']} corpus construction completed.")
        r = result["stats"]
        a = st.columns(4)
        a[0].metric("Input records", r["input"])
        a[1].metric("Saved documents", r["saved"])
        a[2].metric("Duplicates removed", r["duplicates"])
        a[3].metric("Failed / empty", r["failed"])
        d1, d2 = st.columns(2)
        with d1:
            st.download_button("Download IRaMuTeQ corpus", result["corpus"], result["corpus_name"], "text/plain", use_container_width=True, key="local_corpus_download")
        with d2:
            st.download_button("Download processing log", result["log"], result["log_name"], "text/plain", use_container_width=True, key="local_log_download")
        with st.expander("Processing diagnostics", expanded=False):
            st.json(r)

    st.markdown('<div class="footer-line">IRaMuTeQ Corpus Builder · CrowdTangle · generic CSV</div>', unsafe_allow_html=True)


if __name__ == "__main__":
    main()
