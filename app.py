#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unified IRaMuTeQ corpus builder.

Modes:
- MediaCloud -> IRaMuTeQ (preserves the original extraction workflow)
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
from collections import defaultdict
from datetime import datetime
from io import StringIO, BytesIO
from urllib.parse import urlsplit, urlunsplit

import requests
import trafilatura
import plotly.graph_objects as go
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

# ============================================================
# SOURCE CLASSIFICATION (kept from the MediaCloud application)
# ============================================================
NATIONAL_PRESS = {
    "rizospastis.gr", "alphatv.gr", "amna.gr", "elkosmos.gr", "ethnos.gr",
    "kathimerini.gr", "imerisia.gr", "stoxos.gr", "tanea.gr", "naftemporiki.gr",
    "athensvoice.gr", "ekathimerini.com", "enet.gr", "tovima.gr", "enetenglish.gr",
    "protothema.gr", "newsbomb.gr", "tokarfi.gr", "efsyn.gr", "lifo.gr",
    "documentonews.gr", "antenna.gr", "megatv.com", "novasports.gr", "star.gr",
    "daypress.gr", "gavros.gr", "ipop.gr", "newsit.gr", "polispress.gr",
    "politisonline.com", "metrogreece.gr", "reporter.gr", "athinorama.gr", "avgi.gr",
    "espressonews.gr", "kerdos.gr", "press-time.gr", "real.gr", "eleftherostypos.gr",
    "dimokratianews.gr", "parapolitika.gr", "topontiki.gr", "sportime.gr", "prin.gr",
    "fosonline.gr", "freesunday.gr", "vradini.gr", "championsday.gr", "kontranews.gr",
    "dimoprasion.gr", "makeleio.gr", "iefimerida.gr", "sport-fm.gr", "ereportaz.gr",
    "paron.gr", "agronews.gr", "agroekfrasi.gr", "axianews.gr", "orthodoxostypos.gr",
    "wearesolomon.com", "insidestory.gr", "thepressproject.gr", "themanifoldfiles.org",
    "reportersunited.gr", "omniatv.com",
}

REGIONAL_PRESS = {
    "makthes.gr", "rodiaki.gr", "trakyaninsesi.com", "alithia.gr", "thrakikigi.gr",
    "xronos.gr", "agonas.gr", "alpha1.gr", "athinapoli.gr", "aixmi-news.gr",
    "pelop.gr", "patrisnews.com", "patris.gr", "star-fm.gr", "novazora.gr", "ditiki.gr",
    "prlogos.gr", "proinoslogos.gr", "enimerosi.com", "ioanninatoday.blogspot.com",
    "neoiagones.gr", "proinanea.gr", "kilkistoday.gr", "metrosport.gr", "laos-epea.gr",
    "haniotika-nea.gr", "cretetv.gr", "mesogios.gr", "neakriti.gr", "dimokratiki.gr",
    "eleftheriaonline.gr", "eleftheria.gr", "evrytanika.gr", "kosmoslarissa.gr",
    "e-thessalia.gr", "chiosnews.com", "emprosnet.gr", "estianews.gr", "karfitsa.gr",
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


def extract_year(value):
    return extract_year_month(value)[0]


def clean_source_name(media_name):
    source = (media_name or "").strip().lower()
    return re.sub(r"[^a-z0-9]", "", source)


def normalize_source_for_classification(media_name):
    value = (media_name or "").strip().lower().rstrip("/")
    if "://" in value:
        value = urlsplit(value).netloc.lower()
    if value.startswith("www."):
        value = value[4:]
    return value


def classify_source(media_name):
    source = normalize_source_for_classification(media_name)
    if source in NATIONAL_PRESS:
        return "nationalpress"
    if source in REGIONAL_PRESS:
        return "regionalpress"
    return "unclassified"


def clean_url(url):
    cleaned = str(url).strip().strip('"\'').strip()
    parts = urlsplit(cleaned)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def valid_url(url):
    if not url:
        return False
    cleaned = str(url).strip().strip('"\'').strip()
    return cleaned.startswith("http://") or cleaned.startswith("https://")


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


def mediacloud_worker(job_id, selected_rows, custom_metadata_fields, delay, min_article_length, use_press_classification):
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



@st.fragment(run_every="1s")

def render_media_job():
    job_id = st.session_state.get("active_job")
    if not job_id:
        return
    with EXTRACTION_JOBS_LOCK:
        job = EXTRACTION_JOBS.get(job_id)
    if not job:
        return

    st.markdown('<div class="section-title">5. MediaCloud processing</div>', unsafe_allow_html=True)
    if job["status"] in {"running", "starting", "cancelling"}:
        total = max(job.get("total", 0), 1)
        done = min(job.get("processed", 0), total)
        st.progress(done / total, text=f"Processed {done:,} / {total:,} · {job.get('current', '')}")
        c1, c2 = st.columns([1, 3])
        with c1:
            if job["status"] != "cancelling" and st.button("Cancel extraction", key=f"cancel_{job_id}", type="secondary", use_container_width=True):
                job["cancel_event"].set()
                job["status"] = "cancelling"
        with c2:
            st.caption("Cancellation stops after the current web request finishes.")
        time.sleep(1)
        st.rerun()

    if job["status"] == "error":
        st.error(f"Extraction failed: {job.get('error', 'Unknown error')}")
        st.session_state.pop("active_job", None)
        return

    out = job["result"]
    st.progress(1.0, text="Extraction stopped." if out["cancelled"] else "Corpus construction complete.")
    if out["cancelled"]:
        st.warning(f"Extraction cancelled after {out['rows_processed']:,} processed records. Partial results are available.")
    else:
        st.success("Corpus construction completed.")

    result_cols = st.columns(5)
    result_cols[0].metric("Articles saved", f"{out['successful']:,}")
    result_cols[1].metric("Articles failed", f"{out['errors']:,}")
    result_cols[2].metric("Duplicate URLs", f"{out['duplicate_count']:,}")
    result_cols[3].metric("National press", f"{out['national_count']:,}")
    result_cols[4].metric("Regional press", f"{out['regional_count']:,}")

    st.markdown("### Publication statistics")
    st.caption("Initial = records in the selected CSV. Saved = articles actually present in the generated corpus, reconstructed using the original rawnb row number.")
    st.dataframe(list(csv.DictReader(StringIO(out["initial_stats"].decode("utf-8-sig")))), use_container_width=True, hide_index=True)
    st.dataframe(list(csv.DictReader(StringIO(out["saved_stats"].decode("utf-8-sig")))), use_container_width=True, hide_index=True)
    if out.get("plot"):
        st.image(out["plot"], caption="Initial MediaCloud records vs. articles saved by publication year", use_container_width=True)

    d1, d2 = st.columns(2)
    with d1:
        st.download_button("Download IRaMuTeQ corpus", out["corpus"], "news_iramuteq.txt", "text/plain", use_container_width=True, key=f"mc_corpus_{job_id}")
    with d2:
        st.download_button("Download failure log", out["failed"], "failed_articles.txt", "text/plain", use_container_width=True, key=f"mc_failed_{job_id}")
    st.session_state["last_media_result"] = out
    st.session_state.pop("active_job", None)


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
    .metadata-card { background:#111!important; border:1px solid #333; border-radius:10px; padding:1rem 1.2rem; }
    .metadata-card, .metadata-card * { color:#fff!important; }
    .stApp p,.stApp label,.stApp span { color:#111; }
    [data-testid="stSidebar"] { background:#111!important; }
    [data-testid="stSidebar"] * { color:#fff!important; }
    .stButton>button,.stDownloadButton>button { border-radius:8px; font-weight:600; color:#fff !important; -webkit-text-fill-color:#fff !important; background:#111827 !important; border-color:#111827 !important; }
    .stButton>button:hover,.stDownloadButton>button:hover { color:#fff !important; -webkit-text-fill-color:#fff !important; background:#1f2937 !important; }
    .stButton>button *, .stDownloadButton>button * { color:#fff !important; -webkit-text-fill-color:#fff !important; fill:#fff !important; }
    [data-testid="stSidebar"] [data-testid="stRadio"] label, [data-testid="stSidebar"] [data-testid="stRadio"] label p, [data-testid="stSidebar"] [data-testid="stRadio"] label span { color:#fff !important; -webkit-text-fill-color:#fff !important; }
    [data-testid="stSidebar"] [data-testid="stRadio"] [role="radiogroup"] label { color:#fff !important; }
    [data-testid="stFileUploader"] button, [data-testid="stFileUploader"] button *, [data-testid="stFileUploader"] [role="button"], [data-testid="stFileUploader"] [role="button"] * { color:#fff !important; -webkit-text-fill-color:#fff !important; fill:#fff !important; background:#111827 !important; }
    [data-testid="stFileUploader"] small { color:#444 !important; }
    </style>
    """, unsafe_allow_html=True)

    # Source selection lives in the sidebar so the MediaCloud mode can retain the
    # original application's focused "1. Corpus input" workflow without the
    # generic introduction above it.
    with st.sidebar:
        source_mode = st.radio(
            "Input source",
            ["MediaCloud", "CrowdTangle", "Any CSV"],
            index=0,
            key="input_source_mode",
            help="MediaCloud uses the original MediaCloud extraction workflow. CrowdTangle and Any CSV use the new flexible mapping workflows.",
        )

    st.markdown('<div class="research-kicker">Open research utility · corpus preparation</div>', unsafe_allow_html=True)
    st.markdown('<h1 class="research-title">IRaMuTeQ Corpus Builder</h1>', unsafe_allow_html=True)
    st.caption("Unified corpus builder · MediaCloud / CrowdTangle / CSV")

    if source_mode != "MediaCloud":
        st.markdown(
            '<div class="research-subtitle">Build an IRaMuTeQ-ready textual corpus from MediaCloud, CrowdTangle exports, or an ordinary CSV.</div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            '<div class="citation-box"><strong>Workflow.</strong> Choose source → upload → map columns → inspect → validate → export.</div>',
            unsafe_allow_html=True,
        )
        st.markdown('<div class="section-title">What should an IRaMuTeQ corpus look like?</div>', unsafe_allow_html=True)
        st.markdown("Each document starts with a line beginning with `****`. Metadata variables follow as `*variable_value`. The following line contains the text to analyse. Because `*` is structural, source data containing literal asterisks are cleaned before export.")
        st.code("""**** *source_example *year_2025 *country_greece *rawnb_12
This is the text of the first document.

**** *source_example *year_2025 *country_france *rawnb_13
This is the text of the second document.""", language="text")
        st.caption("The exact variables depend on the source and mappings you choose.")

    if source_mode == "MediaCloud":
        with st.sidebar:
            st.markdown("### MediaCloud settings")
            classification_mode = st.selectbox(
                "Source classification",
                ["Greek corpus — use National / Regional classification", "Other language — no built-in classification"],
                index=0,
            )
            use_press_classification = classification_mode.startswith("Greek corpus")
            delay = st.number_input("Wait between article requests (seconds)", 0.0, 60.0, DELAY, 0.1)
            min_article_length = st.number_input("Minimum extracted article length", 0, 100000, MIN_ARTICLE_LENGTH, 10)
    else:
        use_press_classification = False
        delay = 0.0
        min_article_length = 0

    uploaded = st.file_uploader(
        "Upload CSV",
        type=["csv"],
        label_visibility="collapsed",
        help="CSV exports with comma, semicolon, tab, or pipe delimiters are supported. Header variations are handled through column mapping rather than fixed names.",
    )

    if uploaded is None:
        st.info("Upload a CSV to continue.")
        st.markdown('<div class="footer-line">IRaMuTeQ Corpus Builder · MediaCloud · CrowdTangle · generic CSV</div>', unsafe_allow_html=True)
        st.stop()

    try:
        uploaded_bytes, fieldnames, rows, detected_delimiter = read_uploaded_csv(uploaded)
    except Exception as e:
        st.error(f"Unable to read the CSV: {type(e).__name__}: {e}")
        st.stop()

    if not rows:
        st.warning("The CSV contains no records.")
        st.stop()

    st.caption(f"Detected delimiter: `{repr(detected_delimiter)}` · {len(fieldnames):,} columns · {len(rows):,} records")

    # ============================================================
    # MEDIA CLOUD
    # ============================================================
    if source_mode == "MediaCloud":
        required = {"media_name", "publish_date", "url"}
        missing = required - set(fieldnames)
        if missing:
            st.error("The MediaCloud mode requires the original columns: `media_name`, `publish_date`, and `url`. Missing: " + ", ".join(sorted(missing)))
            st.stop()

        source_counts = defaultdict(int)
        for row in rows:
            source = (row.get("media_name", "") or "").strip()
            if source:
                source_counts[source] += 1
        if not source_counts:
            st.error("No usable `media_name` values were found.")
            st.stop()

        st.markdown('<div class="section-title">2. Corpus scope</div>', unsafe_allow_html=True)
        all_sources = sorted(source_counts, key=str.lower)
        if st.button("Select all MediaCloud sources", key="mc_all"):
            for i in range(len(all_sources)):
                st.session_state[f"mc_source_{i}"] = True
        if st.button("Deselect all MediaCloud sources", key="mc_none"):
            for i in range(len(all_sources)):
                st.session_state[f"mc_source_{i}"] = False
        selected_sources = set()
        source_cols = st.columns(2)
        for i, source in enumerate(all_sources):
            with source_cols[i % 2]:
                if st.checkbox(f"{source} · {source_counts[source]:,}", value=True, key=f"mc_source_{i}"):
                    selected_sources.add(source)
        selected_rows = [r for r in rows if (r.get("media_name", "") or "").strip() in selected_sources]
        st.write(f"**Selected records:** {len(selected_rows):,}")
        if not selected_rows:
            st.warning("Select at least one source.")
            st.stop()

        custom_options = [c for c in fieldnames if c not in required]
        custom_cols = st.multiselect("Optional custom metadata columns", custom_options)
        custom_fields = prepare_custom_metadata_fields(custom_cols)

        st.markdown('<div class="section-title">3. Preview</div>', unsafe_allow_html=True)
        preview_row = selected_rows[0]
        year, month = extract_year_month(preview_row.get("publish_date", ""))
        preview_meta = [
            ("source", clean_source_name(preview_row.get("media_name", ""))),
            ("year", year or "missing"),
            ("yearmonth", f"{year}-{month}" if year and month else "missing"),
            ("rawnb", preview_row.get("_rawnb", "")),
        ]
        if use_press_classification:
            preview_meta.append(("type", classify_source(preview_row.get("media_name", ""))))
        preview_meta.extend((f, preview_row.get(c, "")) for c, f in custom_fields)
        st.code(build_header(preview_meta) + "\n" + "Example extracted article text…", language="text")

        if st.button("Begin MediaCloud corpus construction", type="primary", use_container_width=True):
            job_id = uuid.uuid4().hex
            with EXTRACTION_JOBS_LOCK:
                EXTRACTION_JOBS[job_id] = {"status":"running", "processed":0, "total":0, "successful":0, "errors":0, "current":"Preparing…", "cancel_event":threading.Event()}
            st.session_state["active_job"] = job_id
            worker = threading.Thread(target=mediacloud_worker, args=(job_id, selected_rows, custom_fields, delay, min_article_length, use_press_classification), daemon=True)
            worker.start()

    # ============================================================
    # CROWDTANGLE
    # ============================================================
    elif source_mode == "CrowdTangle":
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
        st.markdown("The supplied CrowdTangle scripts are used as the cleaning reference: asterisks in the Message/Description text are replaced with underscores; page/group names are sanitized; date can generate year and year-month; duplicate entries are removed.")
        include_description = desc_col != "— none —"
        if include_description:
            st.checkbox("Append the selected description to each post", value=True, key="ct_include_desc")
        else:
            st.session_state["ct_include_desc"] = False

        # Require text + metadata. Group/date are not mandatory because exports vary.
        metadata_candidates = []
        if group_col != "— none —": metadata_candidates.append(("groupe", group_col))
        if date_col != "— none —": metadata_candidates.append(("date", date_col))
        for c in extra_cols: metadata_candidates.append((c, c))
        if not metadata_candidates:
            st.error("CrowdTangle mode requires at least one metadata field. Select a page/group, date, or another metadata column.")
            st.stop()

        st.markdown('<div class="section-title">4. Preview</div>', unsafe_allow_html=True)
        p = rows[0]
        text = clean_crowdtangle_text(p.get(text_col, ""))
        if st.session_state.get("ct_include_desc") and desc_col != "— none —":
            desc = clean_crowdtangle_text(p.get(desc_col, ""))
            if desc: text = (text + "\n" + desc).strip()
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
        st.code(build_header(meta) + "\n" + text, language="text")

        if st.button("Build CrowdTangle corpus", type="primary", use_container_width=True):
            corpus, log, stats = build_crowdtangle_corpus(rows, text_col, group_col, date_col, desc_col, bool(st.session_state.get("ct_include_desc")), extra_cols)
            show_local_result("CrowdTangle", corpus, log, stats, "crowdtangle_iramuteq.txt", "crowdtangle_processing_log.txt")

    # ============================================================
    # GENERIC CSV
    # ============================================================
    else:
        st.markdown('<div class="section-title">2. Map CSV columns</div>', unsafe_allow_html=True)
        st.markdown("For a generic CSV, the application makes only two structural assumptions: **one text column** and **at least one metadata column**. You decide which columns serve those roles.")
        text_col = st.selectbox("Text column *", fieldnames, key="generic_text")
        metadata_options = [c for c in fieldnames if c != text_col]
        metadata_cols = st.multiselect("Metadata column(s) * — select at least one", metadata_options, key="generic_meta")
        if not metadata_cols:
            st.info("Select at least one metadata column to continue.")
            st.stop()

        st.markdown('<div class="section-title">3. Text cleaning</div>', unsafe_allow_html=True)
        clean_asterisks = st.checkbox("Replace `*` with `_` in text (recommended for IRaMuTeQ)", value=True)
        generic_cleaner = lambda x: clean_text(x, replace_asterisks=clean_asterisks)

        st.markdown('<div class="section-title">4. Preview</div>', unsafe_allow_html=True)
        p = rows[0]
        preview_text = generic_cleaner(p.get(text_col, ""))
        preview_meta = [(c, p.get(c, "")) for c in metadata_cols]
        preview_meta.append(("rawnb", p.get("_rawnb", "")))
        st.code(build_header(preview_meta) + "\n" + preview_text, language="text")

        if st.button("Build generic CSV corpus", type="primary", use_container_width=True):
            corpus, log, stats = build_generic_corpus(rows, text_col, metadata_cols, generic_cleaner)
            show_local_result("Generic CSV", corpus, log, stats, "csv_iramuteq.txt", "csv_processing_log.txt")

    if st.session_state.get("active_job"):
        render_media_job()

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

    st.markdown('<div class="footer-line">IRaMuTeQ Corpus Builder · MediaCloud · CrowdTangle · generic CSV</div>', unsafe_allow_html=True)


if __name__ == "__main__":
    main()
