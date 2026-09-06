#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unified IRaMuTeQ Corpus Builder Application.

Combines:
1. MediaCloud -> IRaMuTeQ (Web scraping, press classification, extraction monitoring)
2. CrowdTangle / Any CSV -> IRaMuTeQ (Flexible column mapping & cleaning)
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
# SHARED / GLOBAL CONFIGURATION & JOBS
# ============================================================
DELAY = 1.5
MIN_ARTICLE_LENGTH = 100
EXTRACTION_JOBS = {}
EXTRACTION_JOBS_LOCK = threading.Lock()

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "el-GR,el;q=0.9,en;q=0.8",
}

# ============================================================
# APP 1 (MEDIACLOUD) PRESS LISTS & HELPER FUNCTIONS
# ============================================================
NATIONAL_PRESS = {
    "rizospastis.gr", "alphatv.gr", "amna.gr", "elkosmos.gr", "ethnos.gr",
    "kathimerini.gr", "imerisia.gr", "stoxos.gr", "tanea.gr", "naftemporiki.gr",
    "athensvoice.gr", "ekathimerini.com", "enet.gr", "tovima.gr", "enetenglish.gr",
    "protothema.gr", "newsbomb.gr", "tokarfi.gr", "efsyn.gr", "lifo.gr",
    "documentonews.gr", "antenna.gr", "megatv.com", "novasports.gr", "star.gr",
    "daypress.gr", "gavros.gr", "ipop.gr", "newsit.gr", "polispress.gr",
    "politisonline.com", "metrogreece.gr", "reporter.gr", "athinorama.gr",
    "avgi.gr", "espressonews.gr", "kerdos.gr", "press-time.gr", "real.gr",
    "eleftherostypos.gr", "dimokratianews.gr", "parapolitika.gr", "topontiki.gr",
    "sportime.gr", "prin.gr", "fosonline.gr", "freesunday.gr", "vradini.gr",
    "championsday.gr", "kontranews.gr", "dimoprasion.gr", "makeleio.gr",
    "iefimerida.gr", "sport-fm.gr", "ereportaz.gr", "paron.gr", "agronews.gr",
    "agroekfrasi.gr", "axianews.gr", "orthodoxostypos.gr", "wearesolomon.com",
    "insidestory.gr", "thepressproject.gr", "themanifoldfiles.org",
    "reportersunited.gr", "omniatv.com",
}

REGIONAL_PRESS = {
    "makthes.gr", "rodiaki.gr", "trakyaninsesi.com", "alithia.gr", "thrakikigi.gr",
    "xronos.gr", "agonas.gr", "alpha1.gr", "athinapoli.gr", "aixmi-news.gr",
    "pelop.gr", "patrisnews.com", "patris.gr", "star-fm.gr", "novazora.gr",
    "ditiki.gr", "prlogos.gr", "proinoslogos.gr", "enimerosi.com",
    "ioanninatoday.blogspot.com", "neoiagones.gr", "proinanea.gr",
    "kilkistoday.gr", "metrosport.gr", "laos-epea.gr", "haniotika-nea.gr",
    "cretetv.gr", "mesogios.gr", "neakriti.gr", "dimokratiki.gr",
    "eleftheriaonline.gr", "eleftheria.gr", "evrytanika.gr", "kosmoslarissa.gr",
    "e-thessalia.gr", "chiosnews.com", "emprosnet.gr", "estianews.gr", "karfitsa.gr",
}

def clean_source_name(media_name):
    source = media_name.strip().lower()
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

def extract_year(value):
    year, _ = extract_year_month(value)
    return year

def clean_url(url):
    cleaned = str(url).strip().strip(""'").strip()
    parts = urlsplit(cleaned)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))

def valid_url(url):
    if not url:
        return False
    cleaned = str(url).strip().strip(""'").strip()
    return cleaned.startswith("http://") or cleaned.startswith("https://")

# ============================================================
# SHARED PARSING & CLEANING HELPERS
# ============================================================
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
    text = text.replace("\t", " ")
    if replace_asterisks:
        text = text.replace("*", "_")
    text = text.replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n+", " ", text)
    return text.strip()

def clean_crowdtangle_text(text):
    return clean_text(text)

def extract_year_month(value):
    if not value:
        return None, None
    value = str(value).strip()
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
    parts = ["****"]
    for field, value in metadata_pairs:
        parts.append(f"*{safe_metadata_field(field)}_{clean_custom_metadata_token(value)}")
    return " ".join(parts)

# ============================================================
# APP 2 (CROWDTANGLE & GENERIC CSV) BUILDERS
# ============================================================
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
    st.session_state["last_local_result"] = {
        "label": label, "corpus": corpus, "log": log, "stats": stats,
        "corpus_name": corpus_name, "log_name": log_name
    }
    st.rerun()

# ============================================================
# APP 1 (MEDIACLOUD) STATS & WORKERS
# ============================================================
def build_statistics_tables(selected_rows, corpus_text):
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
    initial_by_media = defaultdict(dict)
    saved_by_media = defaultdict(dict)
    for row in initial_rows:
        year = str(row.get("year", ""))
        if not year: continue
        for media, value in row.items():
            if media == "year": continue
            initial_by_media[media][year] = int(value or 0)
    for row in saved_rows:
        year = str(row.get("year", ""))
        if not year: continue
        for media, value in row.items():
            if media == "year": continue
            saved_by_media[media][year] = int(value or 0)
    return initial_by_media, saved_by_media

def build_statistics_plot_png(initial_rows, saved_rows, selected_media=None):
    initial_by_media, saved_by_media = build_interactive_plot_data(initial_rows, saved_rows)
    media = selected_media or (sorted(initial_by_media, key=str.lower)[0] if initial_by_media else None)
    if not media: return None
    years = sorted(set(initial_by_media.get(media, {})) | set(saved_by_media.get(media, {})), key=int)
    if not years: return None

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

# ============================================================
# MAIN APPLICATION INTERFACE
# ============================================================
def main():
    st.set_page_config(
        page_title="IRaMuTeQ Corpus Builder",
        page_icon="◈",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    # Stylesheet combining styles for both applications cleanly
    st.markdown("""
    <style>
    :root { --academic-ink: #172033; --academic-muted: #667085; --academic-line: #d9dee8; --academic-paper: #fbfcfe; }
    .stApp { background: var(--academic-paper); }
    .block-container { max-width: 1180px; padding-top: 2.2rem; padding-bottom: 4rem; }
    .research-kicker { font-size: 0.78rem; letter-spacing: 0.14em; text-transform: uppercase; color: #667085; font-weight: 700; margin-bottom: 0.5rem; }
    .research-title { font-family: Georgia, "Times New Roman", serif; font-size: clamp(2.2rem, 4vw, 3.65rem); line-height: 1.05; color: #172033; margin: 0; font-weight: 600; }
    .research-subtitle { font-size: 1.08rem; line-height: 1.65; color: #667085; max-width: 850px; margin-top: 1rem; }
    .section-title { font-family: Georgia, "Times New Roman", serif; font-size: 1.55rem; color: #172033; margin: 2.2rem 0 0.35rem 0; }
    .section-note { color: #667085; margin-bottom: 1rem; }
    .method-card { border: 1px solid #d9dee8; border-radius: 12px; padding: 1.1rem 1.2rem; background: white; min-height: 125px; }
    .method-number { font-size: 0.75rem; letter-spacing: 0.08em; text-transform: uppercase; color: #667085; font-weight: 700; }
    .method-heading { font-family: Georgia, "Times New Roman", serif; color: #172033; font-size: 1.12rem; margin-top: 0.35rem; }
    .method-text { color: #667085; font-size: 0.91rem; line-height: 1.45; }
    .citation-box { border-left: 3px solid #172033; background: white; padding: 0.85rem 1rem; color: #475467; font-size: 0.9rem; line-height: 1.55; margin: 1rem 0 1.5rem 0; }
    .footer-line { border-top: 1px solid #d9dee8; margin-top: 3rem; padding-top: 1rem; color: #667085; font-size: 0.82rem; }
    div[data-testid="stMetric"] { background: white; border: 1px solid #d9dee8; padding: 0.85rem; border-radius: 10px; }
    div[data-testid="stFileUploader"] { background: white; border: 1px dashed #b9c1cf; border-radius: 12px; padding: 0.35rem; }
    .stButton > button, .stDownloadButton > button { border-radius: 8px; font-weight: 600; }
    [data-testid="stSidebar"] { border-right: 1px solid #d9dee8; background: #111111 !important; }
    [data-testid="stSidebar"] * { color: #ffffff !important; }
    .stApp, .stApp p, .stApp label, .stApp span, .stApp div { color: #111111; }
    .research-kicker, .section-note, .research-subtitle, .method-text, .method-number, .footer-line, .stCaption, [data-testid="stCaptionContainer"] { color: #444444 !important; }
    .research-title, .section-title, .method-heading { color: #111111 !important; }
    [data-testid="stMetricValue"], [data-testid="stMetricLabel"] { color: #111111 !important; }
    input, textarea, [data-baseweb="select"] * { color: #111111 !important; }
    code { color: #111111 !important; }
    [data-testid="stCode"] pre, [data-testid="stCode"] code, [data-testid="stCode"] code span {
        color: #fff !important; -webkit-text-fill-color: #fff !important; background: #191c24 !important;
    }
    .stButton > button, .stDownloadButton > button, [data-testid="stFormSubmitButton"] > button {
        background-color: #111111 !important; color: #ffffff !important; -webkit-text-fill-color: #ffffff !important; border: 1px solid #111111 !important;
    }
    .stButton > button:hover, .stDownloadButton > button:hover, [data-testid="stFormSubmitButton"] > button:hover {
        background-color: #2b2b2b !important; color: #ffffff !important;
    }
    .metadata-card { background: #111111 !important; border: 1px solid #333333 !important; border-radius: 10px !important; padding: 1.1rem 1.2rem !important; }
    .metadata-card, .metadata-card * { color: #ffffff !important; -webkit-text-fill-color: #ffffff !important; }
    </style>
    """, unsafe_allow_html=True)

    # Global Selector for Case Selection
    st.markdown('<div class="research-kicker">Open research utility · corpus preparation</div>', unsafe_allow_html=True)
    st.markdown('<h1 class="research-title">IRaMuTeQ Corpus Builder</h1>', unsafe_allow_html=True)
    
    app_choice = st.radio(
        "Choose Corpus Generation Engine:",
        ["MediaCloud → IRaMuTeQ (Web Extraction Workflow)", "CrowdTangle / Generic CSV → IRaMuTeQ"],
        horizontal=True,
        key="app_choice_selector"
    )

    st.markdown("---")

    # ============================================================
    # CASE 1: MEDIACLOUD ENGINE
    # ============================================================
    if app_choice == "MediaCloud → IRaMuTeQ (Web Extraction Workflow)":
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

        with st.sidebar:
            with st.expander("About", expanded=False):
                st.markdown(
                    """
                    **MediaCloud → IRaMuTeQ**
                    A research tool for transforming MediaCloud article records into an IRaMuTeQ-compatible corpus.
                    **Created by**  
                    Panos Tsimpoukis with ChatGPT  
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
                help="Choose the second option for French or other non-Greek corpora.",
            )
            use_press_classification = classification_mode.startswith("Greek corpus")

            st.markdown("### Processing settings")
            delay = st.number_input(
                "Wait between article requests (seconds)",
                min_value=0.0, max_value=60.0, value=float(DELAY), step=0.1,
            )
            min_article_length = st.number_input(
                "Minimum article length (characters)",
                min_value=0, max_value=100000, value=int(MIN_ARTICLE_LENGTH), step=10,
            )

        st.markdown('<div class="section-title">1. Corpus input</div>', unsafe_allow_html=True)
        uploaded = st.file_uploader("MediaCloud CSV", type=["csv"], label_visibility="collapsed")

        if uploaded is None:
            st.info("No corpus loaded yet. Upload a CSV to inspect its sources and begin.")
            return

        try:
            uploaded_bytes = uploaded.getvalue()
            decoded = uploaded_bytes.decode("utf-8-sig", errors="replace")
            reader, _ = read_csv_robustly(decoded)

            if reader is None or not reader.fieldnames:
                st.error("The CSV contains no header.")
                return

            required_columns = {"media_name", "publish_date", "url"}
            missing = required_columns - set(reader.fieldnames)
            if missing:
                st.error("Missing required columns: " + ", ".join(sorted(missing)))
                return

            rows = []
            for row_number, row in enumerate(reader, 2):
                normalized_row = {
                    normalize_csv_header(key): value for key, value in row.items() if normalize_csv_header(key)
                }
                normalized_row["_rawnb"] = row_number
                rows.append(normalized_row)
        except Exception as e:
            st.error(f"Unable to read CSV: {e}")
            return

        source_counts = {}
        for row in rows:
            m = (row.get("media_name", "") or "").strip()
            if m: source_counts[m] = source_counts.get(m, 0) + 1

        overview = st.columns(3)
        overview[0].metric("Input records", f"{len(rows):,}")
        overview[1].metric("Unique media names", f"{len(source_counts):,}")
        overview[2].metric("CSV size", f"{len(uploaded_bytes) / 1024:.1f} KB")

        st.markdown('<div class="section-title">2. Corpus scope</div>', unsafe_allow_html=True)
        sorted_sources = sorted(source_counts.items(), key=lambda item: item[0].lower())
        
        source_fingerprint = "|".join(f"{s}:{c}" for s, c in sorted_sources)
        if st.session_state.get("source_fingerprint") != source_fingerprint:
            st.session_state["source_fingerprint"] = source_fingerprint
            for idx, (source, _) in enumerate(sorted_sources):
                st.session_state[f"source_choice_{idx}"] = True

        def set_all_sources(val):
            for idx, _ in enumerate(sorted_sources):
                st.session_state[f"source_choice_{idx}"] = val

        c_col1, c_col2, _ = st.columns([1, 1, 2])
        c_col1.button("Select all", on_click=set_all_sources, args=(True,), use_container_width=True)
        c_col2.button("Deselect all", on_click=set_all_sources, args=(False,), use_container_width=True)

        selected_sources = set()
        source_cols = st.columns(2)
        for i, (source, count) in enumerate(sorted_sources):
            with source_cols[i % 2]:
                if st.checkbox(f"{source} · {count:,} articles", key=f"source_choice_{i}"):
                    selected_sources.add(source)

        selected_rows = [r for r in rows if (r.get("media_name", "") or "").strip() in selected_sources]

        st.markdown('<div class="section-title">3. Custom metadata</div>', unsafe_allow_html=True)
        custom_metadata_options = [c for c in reader.fieldnames if c not in required_columns and not c.startswith("_")]
        custom_metadata_columns = st.multiselect("Select CSV columns to add as metadata", options=custom_metadata_options)
        custom_metadata_fields = prepare_custom_metadata_fields(custom_metadata_columns)

        st.markdown('<div class="section-title">4. Corpus construction</div>', unsafe_allow_html=True)
        run = st.button("Begin corpus construction", type="primary", use_container_width=True, disabled=bool(st.session_state.get("active_extraction_job_id")))

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
            if not job_id: return
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
                if st.button("Cancel extraction", key=f"cancel_{job_id}"):
                    job["cancel_event"].set()
                    job["status"] = "cancelling"
                return

            if job["status"] == "error":
                st.error(f"Extraction failed: {job.get('error', 'Unknown error')}")
                st.session_state.pop("active_extraction_job_id", None)
                return

            out = job["result"]
            st.success("Corpus construction complete.")
            d1, d2 = st.columns(2)
            with d1:
                st.download_button("Download IRaMuTeQ corpus", data=out["corpus"], file_name="news_iramuteq.txt", mime="text/plain", use_container_width=True)
            with d2:
                st.download_button("Download failure log", data=out["failed"], file_name="failed_articles.txt", mime="text/plain", use_container_width=True)

            st.session_state["research_outputs"] = out
            st.session_state["last_extraction_job_id"] = job_id
            st.session_state.pop("active_extraction_job_id", None)

        extraction_monitor()

    # ============================================================
    # CASE 2: CROWDTANGLE / GENERIC CSV ENGINE
    # ============================================================
    else:
        st.markdown(
            '<div class="research-subtitle">Prepare textual corpora for IRaMuTeQ from CrowdTangle exports or from any CSV file, with explicit control over text and metadata fields.</div>',
            unsafe_allow_html=True,
        )

        st.markdown('<div class="section-title">Choose input type</div>', unsafe_allow_html=True)
        source_mode = st.radio(
            "Input source", ["CrowdTangle", "Any CSV"], horizontal=True, key="input_source_mode", label_visibility="collapsed"
        )

        uploaded = st.file_uploader("Upload CSV", type=["csv"], key="corpus_csv_uploader")
        if uploaded is None:
            st.info("Upload a CSV to continue.")
            return

        try:
            _, fieldnames, rows, detected_delimiter = read_uploaded_csv(uploaded)
        except Exception as e:
            st.error(f"Unable to read CSV: {e}")
            return

        if not rows:
            st.warning("The CSV contains no records.")
            return

        st.caption(f"Detected delimiter: `{repr(detected_delimiter)}` · {len(fieldnames):,} columns · {len(rows):,} records")

        if source_mode == "CrowdTangle":
            st.markdown('<div class="section-title">2. Map CrowdTangle columns</div>', unsafe_allow_html=True)
            
            def suggest_column(candidates):
                lowered = {c.lower().replace("_", " "): c for c in fieldnames}
                for cand in candidates:
                    if cand in lowered: return lowered[cand]
                for c in fieldnames:
                    norm = c.lower().replace("_", " ")
                    if any(cand in norm for cand in candidates): return c
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

            include_description = desc_col != "— none —"
            if include_description:
                st.checkbox("Append the selected description to each post", value=True, key="ct_include_desc")
            else:
                st.session_state["ct_include_desc"] = False

            if st.button("Build CrowdTangle corpus", type="primary", use_container_width=True):
                corpus, log, stats = build_crowdtangle_corpus(
                    rows, text_col, group_col, date_col, desc_col,
                    bool(st.session_state.get("ct_include_desc")), extra_cols
                )
                show_local_result("CrowdTangle", corpus, log, stats, "crowdtangle_iramuteq.txt", "crowdtangle_processing_log.txt")

        else:
            st.markdown('<div class="section-title">2. Map CSV columns</div>', unsafe_allow_html=True)
            text_col = st.selectbox("Text column *", fieldnames, key="generic_text")
            metadata_options = [c for c in fieldnames if c != text_col]
            metadata_cols = st.multiselect("Metadata column(s) * — select at least one", metadata_options, key="generic_meta")
            
            clean_asterisks = st.checkbox("Replace `*` with `_` in text (recommended for IRaMuTeQ)", value=True)
            generic_cleaner = lambda x: clean_text(x, replace_asterisks=clean_asterisks)

            if metadata_cols and st.button("Build generic CSV corpus", type="primary", use_container_width=True):
                corpus, log, stats = build_generic_corpus(rows, text_col, metadata_cols, generic_cleaner)
                show_local_result("Generic CSV", corpus, log, stats, "csv_iramuteq.txt", "csv_processing_log.txt")

        if st.session_state.get("last_local_result"):
            result = st.session_state["last_local_result"]
            st.markdown('<div class="section-title">3. Research outputs</div>', unsafe_allow_html=True)
            st.success(f"{result['label']} corpus construction completed.")
            d1, d2 = st.columns(2)
            with d1:
                st.download_button("Download IRaMuTeQ corpus", result["corpus"], result["corpus_name"], "text/plain", use_container_width=True)
            with d2:
                st.download_button("Download processing log", result["log"], result["log_name"], "text/plain", use_container_width=True)

if __name__ == "__main__":
    main()
