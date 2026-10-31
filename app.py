#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Combined IRaMuTeQ corpus builder.

This single Streamlit app lets the user pick which tool to work with, in
the sidebar. Each tool's internal mechanism is unchanged from its original
standalone script:

1. "IRaMuTeQ Corpus Builder" (originally app.py)
   - Meta Content Library -> IRaMuTeQ (flexible column mapping + Meta Content Library-specific cleaning, for Facebook and Instagram CSV exports)
   - Any CSV -> IRaMuTeQ (one text column + at least one metadata column)

2. "MediaCloud -> IRaMuTeQ" (originally app1.py, created by Panos Tsimpoukis
   with ChatGPT / September 2026)
   - MediaCloud CSV -> web article extraction -> IRaMuTeQ corpus,
     with National/Regional press classification and publication statistics.

Only one Streamlit page config is set (once, at the top), and each tool's
own logic lives in its own function so the two never share variable or
function names.
"""

import threading
import time
import os
import re
import json
import uuid
import shutil
from datetime import datetime

import streamlit as st

# ============================================================
# Version — increase it at every published change and add a matching
# section to CHANGELOG.md and a GitHub release tagged v<APP_VERSION>.
# MAJOR.MINOR.PATCH: PATCH = bug fixes, MINOR = new features,
# MAJOR = changes that break existing habits or file formats.
# ============================================================
APP_VERSION = "1.2.0"

# ============================================================
# Shared state that MUST persist across Streamlit reruns
# (used by the MediaCloud tool's background extraction jobs)
# ============================================================
EXTRACTION_JOBS = {}
EXTRACTION_JOBS_LOCK = threading.Lock()

# ============================================================
# Persistent extraction runs (survive tab closes, and — as long as
# /app/data is mounted as a Docker volume — container restarts too).
# Used by all three pipelines for the "Extractions manager" tab, and by
# the MediaCloud pipeline for pause/resume.
# ============================================================
DATA_DIR = os.environ.get("IRAMUTEQ_DATA_DIR", "/app/data")
RUNS_DIR = os.path.join(DATA_DIR, "runs")


def _run_dir(run_id):
    return os.path.join(RUNS_DIR, run_id)


def _write_run_meta(run_id, meta):
    try:
        d = _run_dir(run_id)
        os.makedirs(d, exist_ok=True)
        meta = dict(meta)
        meta["updated_at"] = datetime.now().isoformat()
        tmp = os.path.join(d, "meta.json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(meta, f)
        os.replace(tmp, os.path.join(d, "meta.json"))
    except Exception:
        pass  # persistence is best-effort; it must never break extraction


def create_run(pipeline, label, total=0, extra=None):
    run_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    meta = {
        "run_id": run_id, "pipeline": pipeline, "label": label,
        "started_at": datetime.now().isoformat(),
        "status": "running", "total": total, "processed": 0,
        "successful": 0, "errors": 0, "duplicate_count": 0,
        "national_count": 0, "regional_count": 0, "unclassified_count": 0,
        "missing_metadata": 0, "request_errors": 0, "extraction_errors": 0,
        "short_articles": 0, "unexpected_errors": 0, "invalid_date_rows": 0,
        "current": "", "error_message": "", "connection_lost": False,
        "eta_seconds": None,
        "cancel_requested": False,
        "app_version": APP_VERSION,
    }
    if extra:
        meta.update(extra)
    _write_run_meta(run_id, meta)
    return run_id


def read_run_meta(run_id):
    path = os.path.join(_run_dir(run_id), "meta.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def update_run(run_id, **fields):
    meta = read_run_meta(run_id)
    if meta is None:
        return
    meta.update(fields)
    _write_run_meta(run_id, meta)


def rename_run(run_id, new_label):
    """Persist a user-facing name for an extraction."""
    new_label = (new_label or "").strip()
    if not new_label:
        return False
    meta = read_run_meta(run_id)
    if meta is None:
        return False
    update_run(run_id, label=new_label)
    return True


def list_runs(pipeline=None):
    try:
        os.makedirs(RUNS_DIR, exist_ok=True)
        runs = []
        for name in os.listdir(RUNS_DIR):
            meta = read_run_meta(name)
            if meta and (pipeline is None or meta.get("pipeline") == pipeline):
                runs.append(meta)
        runs.sort(key=lambda m: m.get("started_at", ""), reverse=True)
        return runs
    except Exception:
        return []


def delete_run(run_id):
    shutil.rmtree(_run_dir(run_id), ignore_errors=True)


def write_run_file(run_id, name, data_bytes):
    try:
        d = _run_dir(run_id)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, name), "wb") as f:
            f.write(data_bytes)
    except Exception:
        pass


def append_run_file(run_id, name, data_bytes):
    try:
        d = _run_dir(run_id)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, name), "ab") as f:
            f.write(data_bytes)
    except Exception:
        pass


def read_run_file(run_id, name):
    path = os.path.join(_run_dir(run_id), name)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as f:
            return f.read()
    except Exception:
        return None


def write_run_json(run_id, name, obj):
    write_run_file(run_id, name, json.dumps(obj).encode("utf-8"))


def read_run_json(run_id, name, default=None):
    data = read_run_file(run_id, name)
    if data is None:
        return default
    try:
        return json.loads(data.decode("utf-8"))
    except Exception:
        return default


def format_eta(seconds):
    if seconds is None or seconds < 0 or seconds != seconds:  # NaN-safe
        return ""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


# ============================================================
# TOOL 1: IRaMuTeQ Corpus Builder (Meta Content Library / Any CSV)
# — unchanged mechanism from the original app.py —
# ============================================================
def run_corpus_builder_app(forced_source_mode=None):
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


    def clean_metadata_field_name(name, fallback="metadata"):
        """Cleans a metadata *field name* for the IRaMuTeQ header.

        Field names must be a single word with no separators, since the
        underscore is reserved to separate the field name from its value
        (``*fieldname_value``). All non-alphanumeric characters (spaces,
        underscores, punctuation) are simply dropped, not replaced.
        """
        name = "" if name is None else str(name).strip().lower()
        name = unicodedata.normalize("NFKD", name)
        name = "".join(ch for ch in name if not unicodedata.combining(ch))
        name = name.encode("ascii", "ignore").decode("ascii")
        name = re.sub(r"[^a-z0-9]+", "", name)
        return name or fallback


    def clean_metadata_value_token(value, fallback="missing"):
        """Cleans a metadata *value* for the IRaMuTeQ header.

        Runs of non-alphanumeric characters become a single dash (``-``),
        not an underscore, so the value never collides with the
        name/value separator, e.g. "2023-02-24" instead of "2023_02_24".
        """
        value = "" if value is None else str(value).strip().lower()
        value = unicodedata.normalize("NFKD", value)
        value = "".join(ch for ch in value if not unicodedata.combining(ch))
        value = value.encode("ascii", "ignore").decode("ascii")
        value = re.sub(r"[^a-z0-9]+", "-", value)
        value = re.sub(r"-+", "-", value).strip("-")
        return value or fallback


    def prepare_custom_metadata_fields(columns):
        used = {}
        prepared = []
        for column in columns:
            base = clean_metadata_field_name(column, fallback="metadata")
            count = used.get(base, 0) + 1
            used[base] = count
            field = base if count == 1 else f"{base}{count}"
            prepared.append((column, field))
        return prepared


    _STRUCTURAL_CHARS_TO_STRIP = ["*", "£"]

    def clean_text(text, replace_asterisks=True):
        if text is None:
            return ""
        text = str(text).replace("\ufeff", "")
        # Tabs are reserved for IRaMuTeQ metadata.
        text = text.replace("\t", " ")
        # Asterisks are structural in IRaMuTeQ and therefore cannot be left in
        # the text (this was also the central cleaning rule in the original
        # CrowdTangle-era scripts this app is descended from). The £ sign is
        # cleaned the same way, replaced with a space. The Greek guillemets
        # «» are kept (not stripped) — see space_out_guillemets(), applied
        # once on the finished corpus.
        if replace_asterisks:
            for ch in _STRUCTURAL_CHARS_TO_STRIP:
                text = text.replace(ch, " ")
        text = text.replace("\r", "\n")
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n+", " ", text)
        return text.strip()


    def space_out_guillemets(text):
        """Ensures the Greek guillemets «» are never glued to the word they
        quote, e.g. «Ξηγηθήκαμε» -> « Ξηγηθήκαμε ». Run once on the finished
        corpus text (not per-row), since it only needs to fix spacing, not
        re-clean anything. Important for correct downstream lemmatization:
        a tokenizer that doesn't split punctuation from words would otherwise
        treat «Ξηγηθήκαμε» and Ξηγηθήκαμε as two different word forms.
        """
        if not text:
            return text
        text = re.sub(r"«\s*", "« ", text)
        text = re.sub(r"\s*»", " »", text)
        text = re.sub(r"[ \t]+", " ", text)
        return text


    def replace_ellipses_with_space(text):
        """Replaces ellipses (three or more dots, or the single "…" character)
        with a single space, e.g. "Και μετά..." -> "Και μετά ". Run once on
        the finished corpus text, alongside space_out_guillemets — for the
        same lemmatization reason, "μετά..." would otherwise be tokenized as
        a different form than "μετά".
        """
        if not text:
            return text
        text = re.sub(r"\.{3,}|…", " ", text)
        text = re.sub(r"[ \t]+", " ", text)
        return text


    def clean_meta_text(text):
        """Apply the cleaning rule demonstrated in the supplied scripts."""
        return clean_text(text)


    # ------------------------------------------------------------
    # Optional, user-controlled advanced cleaning
    # ------------------------------------------------------------
    # Each step below is independently toggleable in the UI so the
    # researcher always knows exactly what was changed in their text,
    # and can opt out of anything they disagree with.

    _GREEK_LETTER_CLASS = r"[\u0370-\u03FF\u1F00-\u1FFFa-zA-Z]"
    _URL_RE = re.compile(r"https?://\S+")
    _HASHTAG_MENTION_RE = re.compile(r"[#@](\w+)", re.UNICODE)
    _REPEATED_PUNCT_RE = re.compile(r"([.!?…])\1{2,}")
    _FINAL_SIGMA_RE = re.compile(r"σ(?!" + _GREEK_LETTER_CLASS + r")")

    def convert_polytonic_to_monotonic(text):
        """Best-effort conversion from polytonic to monotonic Greek.

        This unifies the grave and circumflex (perispomeni) stress marks
        with the acute accent, and drops the smooth/rough breathings and
        the iota subscript. It is a LOSSY, approximate transformation:
        it cannot recover which stress accent a word originally carried
        in cases where that distinction mattered, and it will also affect
        any other accented text (e.g. French or other languages quoted
        inside the corpus) that happens to use the same combining marks.
        Only enable this if you understand and accept that trade-off.
        """
        if not text:
            return text
        decomposed = unicodedata.normalize("NFD", text)
        out = []
        for ch in decomposed:
            code = ord(ch)
            if code in (0x0300, 0x0342):  # grave, perispomeni -> acute
                out.append("\u0301")
            elif code in (0x0313, 0x0314, 0x0345):  # breathings, iota subscript -> drop
                continue
            else:
                out.append(ch)
        return unicodedata.normalize("NFC", "".join(out))


    def smart_lowercase(text):
        """Lowercase text while fixing Greek word-final sigma.

        Python's built-in str.lower() always turns Σ/σ into 'σ', even at
        the end of a word, where standard Greek orthography requires the
        final form 'ς'. Without this fix, lowercasing ALL-CAPS posts would
        introduce spelling errors (e.g. "ΛΑΘΡΟΜΕΤΑΝΑΣΤΕΣ" -> "λαθρομεταναστεσ"
        instead of "λαθρομετανάστες").
        """
        if not text:
            return text
        lowered = text.lower()
        return _FINAL_SIGMA_RE.sub("ς", lowered)


    def apply_advanced_cleaning(text, options):
        """Apply the optional cleaning steps selected in the UI, in a
        fixed, predictable order."""
        if not text:
            return text
        options = options or {}
        if options.get("polytonic_to_monotonic"):
            text = convert_polytonic_to_monotonic(text)
        if options.get("remove_urls"):
            text = _URL_RE.sub(" ", text)
        if options.get("strip_hashtags_mentions"):
            text = _HASHTAG_MENTION_RE.sub(r"\1", text)
        if options.get("normalize_punctuation"):
            text = _REPEATED_PUNCT_RE.sub(r"\1\1\1", text)
        if options.get("lowercase"):
            text = smart_lowercase(text)
        return re.sub(r"[ \t]+", " ", text).strip()


    def render_advanced_cleaning_options(key_prefix):
        """Render the advanced cleaning checkboxes/inputs and return
        (options_dict, min_length)."""
        st.markdown("**Advanced cleaning (optional)** — each option below changes the corpus text; review the preview after toggling.")
        c1, c2 = st.columns(2)
        with c1:
            lowercase = st.checkbox(
                "Convert text to lowercase",
                value=True,
                key=f"{key_prefix}_lowercase",
                help="Recommended: without this, the same word typed in lowercase, Capitalized, and ALL CAPS is counted as three different words. Final sigma (ς) is handled correctly.",
            )
            remove_urls = st.checkbox(
                "Remove URLs",
                value=True,
                key=f"{key_prefix}_remove_urls",
                help="Strips http(s):// links, which carry no lexical value.",
            )
            strip_tags = st.checkbox(
                "Strip # and @ symbols (keep the word)",
                value=True,
                key=f"{key_prefix}_strip_tags",
                help="'#Greece' becomes 'Greece' and '@someuser' becomes 'someuser'.",
            )
        with c2:
            normalize_punct = st.checkbox(
                "Normalize repeated punctuation",
                value=True,
                key=f"{key_prefix}_normalize_punct",
                help="Collapses runs such as '.....' or '!!!!!' down to three characters.",
            )
            dedupe_text_only = st.checkbox(
                "Remove duplicate posts (by text content)",
                value=True,
                key=f"{key_prefix}_dedupe_text_only",
                help="Two posts with identical text (e.g. a reposted message) are treated as duplicates even if their page, date, or other metadata differ. Without this, exact copy-paste campaigns can inflate word frequencies.",
            )
            min_length = st.number_input(
                "Minimum text length (characters, 0 = no filter)",
                min_value=0,
                max_value=2000,
                value=0,
                step=10,
                key=f"{key_prefix}_min_length",
                help="Documents shorter than this, after cleaning, are excluded and logged instead of being saved.",
            )
        polytonic_to_monotonic = st.checkbox(
            "Convert polytonic accents to monotonic (experimental, lossy)",
            value=False,
            key=f"{key_prefix}_polytonic",
            help=(
                "Greek text sometimes mixes the modern monotonic accent system with older polytonic "
                "diacritics (e.g. quoted historical or religious text), which IRaMuTeQ treats as different "
                "word forms. This option unifies stress marks and drops breathings/iota subscript to reduce "
                "that fragmentation. It is an approximation, not a linguistically exact conversion, and it can "
                "erase distinctions that mattered in the original text — leave it off if you are unsure."
            ),
        )
        options = {
            "lowercase": lowercase,
            "remove_urls": remove_urls,
            "strip_hashtags_mentions": strip_tags,
            "normalize_punctuation": normalize_punct,
            "polytonic_to_monotonic": polytonic_to_monotonic,
        }
        return options, int(min_length), dedupe_text_only


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
        return clean_metadata_field_name(name, fallback="metadata")


    def build_header(metadata_pairs):
        """metadata_pairs is a list of (field_name, value)."""
        parts = ["****"]
        for field, value in metadata_pairs:
            parts.append(f"*{safe_metadata_field(field)}_{clean_metadata_value_token(value)}")
        return " ".join(parts)


    def row_text_from_columns(row, columns, cleaner=clean_text, joiner="\n"):
        chunks = []
        for col in columns:
            value = cleaner(row.get(col, ""))
            if value:
                chunks.append(value)
        return joiner.join(chunks).strip()





    def build_generic_corpus(rows, text_col, metadata_cols, cleaner, advanced_options=None, min_length=0, dedupe_on_text_only=False):
        output = io.StringIO()
        log = io.StringIO()
        saved = failed = duplicates = empty_text = filtered_short = 0
        seen = set()
        for row in rows:
            rownumber = row.get("_rawnb", "")
            text = cleaner(row.get(text_col, ""))
            text = apply_advanced_cleaning(text, advanced_options)
            if not text:
                failed += 1; empty_text += 1
                log.write(f"[ROW {rownumber}]\nerror: Empty text in selected column '{text_col}'\n{'-'*70}\n\n")
                continue
            if min_length and len(text) < min_length:
                failed += 1; filtered_short += 1
                log.write(f"[ROW {rownumber}]\nerror: Text shorter than the minimum length ({len(text)} < {min_length} characters)\n{'-'*70}\n\n")
                continue
            meta = [(c, row.get(c, "")) for c in metadata_cols]
            meta.append(("rownumber", rownumber))
            record = build_header(meta) + "\n" + text
            dedupe_key = text if dedupe_on_text_only else record
            if dedupe_key in seen:
                duplicates += 1
                what = "text (identical to an earlier row, ignoring metadata)" if dedupe_on_text_only else "record"
                log.write(f"[ROW {rownumber}]\nerror: Duplicate corpus {what}\n{'-'*70}\n\n")
                continue
            seen.add(dedupe_key)
            output.write(record + "\n\n")
            saved += 1
        stats = {"input": len(rows), "saved": saved, "failed": failed, "duplicates": duplicates, "empty_text": empty_text, "filtered_short": filtered_short}
        return replace_ellipses_with_space(space_out_guillemets(output.getvalue())).encode("utf-8"), log.getvalue().encode("utf-8"), stats


    def build_meta_corpus(rows, text_col, group_col, date_col, desc_col, include_description, extra_cols, advanced_options=None, min_length=0, dedupe_on_text_only=False):
        output = io.StringIO()
        log = io.StringIO()
        saved = failed = duplicates = empty_text = filtered_short = 0
        seen = set()
        for row in rows:
            rownumber = row.get("_rawnb", "")
            text = clean_meta_text(row.get(text_col, ""))
            if include_description and desc_col != "— none —":
                desc = clean_meta_text(row.get(desc_col, ""))
                if desc:
                    text = (text + "\n" + desc).strip() if text else desc
            text = apply_advanced_cleaning(text, advanced_options)
            if not text:
                failed += 1; empty_text += 1
                log.write(f"[ROW {rownumber}]\nerror: Empty text\n{'-'*70}\n\n")
                continue
            if min_length and len(text) < min_length:
                failed += 1; filtered_short += 1
                log.write(f"[ROW {rownumber}]\nerror: Text shorter than the minimum length ({len(text)} < {min_length} characters)\n{'-'*70}\n\n")
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
            meta.append(("rownumber", rownumber))
            record = build_header(meta) + "\n" + text
            dedupe_key = text if dedupe_on_text_only else record
            if dedupe_key in seen:
                duplicates += 1
                what = "text (identical to an earlier row, ignoring metadata)" if dedupe_on_text_only else "record"
                log.write(f"[ROW {rownumber}]\nerror: Duplicate {what}\n{'-'*70}\n\n")
                continue
            seen.add(dedupe_key)
            output.write(record + "\n\n")
            saved += 1
        stats = {"input": len(rows), "saved": saved, "failed": failed, "duplicates": duplicates, "empty_text": empty_text, "filtered_short": filtered_short}
        return replace_ellipses_with_space(space_out_guillemets(output.getvalue())).encode("utf-8"), log.getvalue().encode("utf-8"), stats


    def show_local_result(label, corpus, log, stats, corpus_name, log_name):
        pipeline = "crowdtangle" if label == "Meta Content Library" else "csv"
        run_id = create_run(pipeline, label, total=stats.get("input", 0), extra={"status": "completed"})
        write_run_file(run_id, "corpus.txt", corpus)
        write_run_file(run_id, "failed.txt", log)
        update_run(
            run_id, status="completed", processed=stats.get("input", 0),
            successful=stats.get("saved", 0), errors=stats.get("failed", 0),
            duplicate_count=stats.get("duplicates", 0),
        )
        st.session_state["last_local_result"] = {"label": label, "corpus": corpus, "log": log, "stats": stats, "corpus_name": corpus_name, "log_name": log_name}
        st.rerun()

    # ============================================================
    # UI
    # ============================================================
    def main():

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

        _page_titles = {
            "Meta Content Library": (
                "Meta Content Library to IRaMuTeQ",
                "Prepare a textual corpus for IRaMuTeQ from a Meta Content Library CSV export (Facebook or Instagram), with flexible column mapping and Meta-specific text cleaning.",
            ),
            "Any CSV": (
                "Any CSV to IRaMuTeQ",
                "Prepare a textual corpus for IRaMuTeQ from any CSV file, with explicit control over the text and metadata fields.",
            ),
        }
        _page_title, _page_subtitle = _page_titles.get(
            forced_source_mode,
            (
                "IRaMuTeQ Corpus Builder",
                "Prepare textual corpora for IRaMuTeQ from Meta Content Library exports or from any CSV file, with explicit control over the text and metadata fields.",
            ),
        )

        st.markdown(f'<h1 class="research-title">{_page_title}</h1>', unsafe_allow_html=True)
        st.markdown(
            f'<div class="research-subtitle">{_page_subtitle}</div>',
            unsafe_allow_html=True,
        )

        st.markdown('<div class="citation-box"><strong>What is an IRaMuTeQ corpus?</strong> Each document begins with a line starting with <code>****</code>. Metadata variables are written as <code>*variable_value</code>, followed by the text of the document on the next line. The application cleans structural characters such as <code>*</code> and tabs before export.</div>', unsafe_allow_html=True)

        st.markdown('<div class="section-title">IRaMuTeQ text format</div>', unsafe_allow_html=True)
        st.code("""**** *source_example *year_2025 *country_greece *rownumber_12
    This is the text of the first document.

    **** *source_example *year_2025 *country_france *rownumber_13
    This is the text of the second document.""", language=None)
        st.caption("The exact metadata variables depend on the source and the columns you select.")

        if forced_source_mode in ("Meta Content Library", "Any CSV"):
            # The input source was already chosen on the toolkit's landing
            # page, so it is not asked again here.
            source_mode = forced_source_mode
        else:
            st.markdown('<div class="section-title">Choose your input source</div>', unsafe_allow_html=True)
            source_mode = st.radio(
                "Input source",
                ["Meta Content Library", "Any CSV"],
                horizontal=True,
                key="input_source_mode",
                label_visibility="collapsed",
            )

        st.markdown('<div class="section-title">1. Corpus input</div>', unsafe_allow_html=True)
        if source_mode == "Meta Content Library":
            st.markdown("Upload a Meta Content Library CSV export — Facebook or Instagram. Column names differ slightly between the two; the application will suggest mappings that you can change.")
            uploader_help = "Meta Content Library exports (Facebook or Instagram) with comma, semicolon, tab, or pipe delimiters are supported."
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
            st.markdown('<div class="footer-line">IRaMuTeQ Corpus Builder · Meta Content Library · generic CSV</div>', unsafe_allow_html=True)
            return

        # A previous build's result is stored in session_state so it survives
        # the rerun show_local_result() triggers. Without this check, that
        # stored result keeps reappearing under "Research outputs" on every
        # later rerun too — e.g. switching input source, or just changing a
        # column selection here — making it look like a new corpus was built
        # the instant you touched a widget, when actually nothing was built.
        # Clear it whenever the file or the input source actually changes.
        _upload_signature = (source_mode, uploaded.name, uploaded.size)
        if st.session_state.get("_local_upload_signature") != _upload_signature:
            st.session_state.pop("last_local_result", None)
            st.session_state["_local_upload_signature"] = _upload_signature

        try:
            _, fieldnames, rows, detected_delimiter = read_uploaded_csv(uploaded)
        except Exception as e:
            st.error(f"Unable to read the CSV: {type(e).__name__}: {e}")
            return

        if not rows:
            st.warning("The CSV contains no records.")
            return

        st.caption(f"Detected delimiter: `{repr(detected_delimiter)}` · {len(fieldnames):,} columns · {len(rows):,} records")

        if source_mode == "Meta Content Library":
            st.markdown('<div class="section-title">2. Map Meta Content Library columns</div>', unsafe_allow_html=True)
            st.markdown("Facebook and Instagram exports from Meta Content Library use slightly different headers. The application therefore suggests mappings, but **you control the final mapping**.")

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

            text_default = suggest_column(["text", "message", "post message", "post text", "post content"])
            group_default = suggest_column(["surface.name", "post owner.name", "page name", "group name", "page", "group", "account name"])
            date_default = suggest_column(["creation time", "post created date", "created date", "created time", "post date", "date", "time"])
            desc_default = suggest_column(["link attachment.description", "link attachment.caption", "page description", "description", "group description"])

            def select_with_optional(label, default, key):
                options = ["— none —"] + fieldnames
                index = options.index(default) if default in options else 0
                return st.selectbox(label, options, index=index, key=key)

            text_col = st.selectbox("Post text column *", fieldnames, index=fieldnames.index(text_default) if text_default in fieldnames else 0, key="ct_text")
            group_col = select_with_optional("Page / account name (recommended)", group_default, "ct_group")
            date_col = select_with_optional("Post date (recommended)", date_default, "ct_date")
            desc_col = select_with_optional("Link / page description (optional)", desc_default, "ct_desc")

            reserved = {text_col}
            for c in [group_col, date_col, desc_col]:
                if c != "— none —": reserved.add(c)
            extra_options = [c for c in fieldnames if c not in reserved]
            extra_cols = st.multiselect("Additional metadata columns (optional)", extra_options, key="ct_extra",
                                         help="Meta Content Library also exports columns such as lang, content_type, hashtags, and reaction/engagement statistics (statistics.like_count, statistics.comment_count, etc.) — pick any of these to keep as metadata.")

            st.markdown('<div class="section-title">3. Meta Content Library cleaning</div>', unsafe_allow_html=True)
            st.markdown("Structural characters (`*`, `«`, `»`, `£`) in text are replaced with a space; page/account names are sanitized; dates can generate year and year-month; duplicate entries are removed.")
            include_description = desc_col != "— none —"
            if include_description:
                st.checkbox("Append the selected description to each post", value=True, key="ct_include_desc")
            else:
                st.session_state["ct_include_desc"] = False

            ct_advanced_options, ct_min_length, ct_dedupe_text_only = render_advanced_cleaning_options("ct")

            metadata_candidates = []
            if group_col != "— none —": metadata_candidates.append(("groupe", group_col))
            if date_col != "— none —": metadata_candidates.append(("date", date_col))
            for c in extra_cols:
                metadata_candidates.append((c, c))
            if not metadata_candidates:
                st.error("Meta Content Library mode requires at least one metadata field. Select a page/account, date, or another metadata column.")
                return

            st.markdown('<div class="section-title">4. Preview</div>', unsafe_allow_html=True)
            p = rows[0]
            text = clean_meta_text(p.get(text_col, ""))
            if st.session_state.get("ct_include_desc") and desc_col != "— none —":
                desc = clean_meta_text(p.get(desc_col, ""))
                if desc:
                    text = (text + "\n" + desc).strip()
            text = apply_advanced_cleaning(text, ct_advanced_options)
            meta = []
            if group_col != "— none —": meta.append(("groupe", p.get(group_col, "")))
            if date_col != "— none —":
                date = str(p.get(date_col, "") or "").strip()
                year, month = extract_year_month(date)
                meta.append(("date", date))
                if year: meta.append(("year", year))
                if year and month: meta.append(("ym", f"{year}-{month}"))
            for c in extra_cols: meta.append((c, p.get(c, "")))
            meta.append(("rownumber", p.get("_rawnb", "")))
            st.code(build_header(meta) + "\n" + text, language=None)

            if st.button("Build Meta Content Library corpus", type="primary", use_container_width=True):
                corpus, log, stats = build_meta_corpus(
                    rows, text_col, group_col, date_col, desc_col,
                    bool(st.session_state.get("ct_include_desc")), extra_cols,
                    advanced_options=ct_advanced_options, min_length=ct_min_length,
                    dedupe_on_text_only=ct_dedupe_text_only,
                )
                show_local_result("Meta Content Library", corpus, log, stats, "meta_content_library_iramuteq.txt", "meta_content_library_processing_log.txt")

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
            clean_asterisks = st.checkbox("Replace structural characters `*`, `«`, `»`, `£` with a space (recommended for IRaMuTeQ)", value=True)
            generic_cleaner = lambda x: clean_text(x, replace_asterisks=clean_asterisks)

            csv_advanced_options, csv_min_length, csv_dedupe_text_only = render_advanced_cleaning_options("csv")

            st.markdown('<div class="section-title">4. Preview</div>', unsafe_allow_html=True)
            p = rows[0]
            preview_text = generic_cleaner(p.get(text_col, ""))
            preview_text = apply_advanced_cleaning(preview_text, csv_advanced_options)
            preview_meta = [(c, p.get(c, "")) for c in metadata_cols]
            preview_meta.append(("rownumber", p.get("_rawnb", "")))
            st.code(build_header(preview_meta) + "\n" + preview_text, language=None)

            if st.button("Build IRaMuTeQ corpus", type="primary", use_container_width=True):
                corpus, log, stats = build_generic_corpus(
                    rows, text_col, metadata_cols, generic_cleaner,
                    advanced_options=csv_advanced_options, min_length=csv_min_length,
                    dedupe_on_text_only=csv_dedupe_text_only,
                )
                show_local_result("Generic CSV", corpus, log, stats, "csv_iramuteq.txt", "csv_processing_log.txt")

        if st.session_state.get("last_local_result"):
            result = st.session_state["last_local_result"]
            st.markdown('<div class="section-title">5. Research outputs</div>', unsafe_allow_html=True)
            st.success(f"{result['label']} corpus construction completed.")
            r = result["stats"]
            a = st.columns(5)
            a[0].metric("Input records", r["input"])
            a[1].metric("Saved documents", r["saved"])
            a[2].metric("Duplicates removed", r["duplicates"])
            a[3].metric("Too short (filtered)", r.get("filtered_short", 0))
            a[4].metric("Failed / empty", r["failed"])
            d1, d2 = st.columns(2)
            with d1:
                st.download_button("Download IRaMuTeQ corpus", result["corpus"], result["corpus_name"], "text/plain", use_container_width=True, key="local_corpus_download")
            with d2:
                st.download_button("Download processing log", result["log"], result["log_name"], "text/plain", use_container_width=True, key="local_log_download")
            with st.expander("Processing diagnostics", expanded=False):
                st.json(r)

        st.markdown('<div class="footer-line">IRaMuTeQ Corpus Builder · Meta Content Library · generic CSV</div>', unsafe_allow_html=True)


    main()


# Visual identity of the MediaCloud and TikTok pages (light research canvas,
# dark sidebar, dark buttons with white text).
ACADEMIC_PAGE_CSS = """
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
"""


# ============================================================
# TOOL 2: MediaCloud -> IRaMuTeQ
# — unchanged mechanism from the original app1.py —
# ============================================================
def run_mediacloud_app():

    import csv
    import re
    import time
    import unicodedata
    import threading
    import uuid
    import socket
    from io import StringIO, BytesIO


    from collections import defaultdict, deque
    from datetime import datetime
    from urllib.parse import urlsplit, urlunsplit
    import statistics

    import requests
    import trafilatura
    import plotly.graph_objects as go


    # Background extraction jobs. A small in-process job registry lets Streamlit
    # rerun the UI while the extraction worker continues, making cancellation
    # possible without refreshing the page.


    # ============================================================
    # CONFIGURATION
    # ============================================================

    INPUT_FILE = "mediacloud_articles.csv"

    OUTPUT_FILE = "news_iramuteq.txt"

    FAILED_FILE = "failed_articles.txt"

    STATS_FILE = "publication_counts_by_year.csv"

    DELAY = 1.5

    MIN_ARTICLE_LENGTH = 100

    # Governs only host/URL-specific connection failures (see
    # internet_reachable() below) — a genuine internet outage always retries
    # indefinitely regardless of this value, since it's worth waiting out.
    # A single connection failure to one host (dead domain, DNS hiccup for
    # that host, a block that only affects the server's IP, etc.), while the
    # rest of the internet is reachable, is logged as failed and the run
    # moves straight on to the next article. Raise this above 0 to give a
    # flaky host a couple of extra tries before giving up on it.
    MAX_CONNECTION_RETRIES = 0


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
        Extract year, month, and (when available) day from MediaCloud
        publish_date.

        Supports common formats including:

            2026-06-23
            2026-06-23T12:30:00Z
            2026-06-23T12:30:00+00:00
            2026-06

        Returns (year, month, day) — day is None when the source value
        doesn't include a day (e.g. "2026-06").
        """

        if not value:

            return None, None, None

        value = value.strip()

        # --------------------------------------------------------
        # YYYY-MM-DD
        # --------------------------------------------------------

        match = re.search(
            r"\b(20\d{2})-(\d{1,2})-(\d{1,2})(?!\d)",
            value
        )

        if match:

            year = match.group(1)

            month = match.group(2).zfill(2)

            day = match.group(3).zfill(2)

            try:

                datetime(
                    int(year),
                    int(month),
                    int(day)
                )

                return year, month, day

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

                return year, month, None

            except ValueError:

                pass

        return None, None, None


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

        year, month, day = extract_year_month(
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


    def space_out_guillemets(text):
        """Ensures the Greek guillemets «» are never glued to the word they
        quote, e.g. «Ξηγηθήκαμε» -> « Ξηγηθήκαμε ». Run once on the finished
        corpus text, important for correct downstream lemmatization: a
        tokenizer that doesn't split punctuation from words would otherwise
        treat «Ξηγηθήκαμε» and Ξηγηθήκαμε as two different word forms.
        """
        if not text:
            return text
        text = re.sub(r"«\s*", "« ", text)
        text = re.sub(r"\s*»", " »", text)
        text = re.sub(r"[ \t]+", " ", text)
        return text


    def replace_ellipses_with_space(text):
        """Replaces ellipses (three or more dots, or the single "…" character)
        with a single space. Run once on the finished corpus text, alongside
        space_out_guillemets — for the same lemmatization reason.
        """
        if not text:
            return text
        text = re.sub(r"\.{3,}|…", " ", text)
        text = re.sub(r"[ \t]+", " ", text)
        return text


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
    # POLISHED ACADEMIC RESEARCH INTERFACE
    # ============================================================

    import io
    from datetime import datetime

    import matplotlib.pyplot as plt
    import streamlit as st



    # ------------------------------------------------------------
    # Visual identity (shared with the TikTok page: ACADEMIC_PAGE_CSS)
    # ------------------------------------------------------------
    st.markdown(ACADEMIC_PAGE_CSS, unsafe_allow_html=True)

    # ------------------------------------------------------------
    # Header
    # ------------------------------------------------------------
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
    # Reconnect to an active extraction before showing the fresh uploader.
    # This is intentionally based on persistent run metadata, not only
    # st.session_state, so changing input source cannot make a running job
    # appear to have disappeared.
    #
    # While a run is active, only this banner is shown — the upload form
    # below is hidden (it's not useful while one extraction is already in
    # flight) and this fragment polls every couple of seconds. As soon as
    # the run is no longer active, it triggers a full app rerun on its own,
    # which lands back here with no active run and falls through to the
    # normal upload form — no manual refresh needed.
    # ------------------------------------------------------------
    _active_mc_runs = [
        m for m in list_runs(pipeline="mediacloud")
        if m.get("status") in _ACTIVE_RUN_STATUSES
    ]
    _active_mc_run = _active_mc_runs[0] if _active_mc_runs else None

    @st.fragment(run_every="2s")
    def _active_run_banner():
        live_runs = [
            m for m in list_runs(pipeline="mediacloud")
            if m.get("status") in _ACTIVE_RUN_STATUSES
        ]
        live_run = live_runs[0] if live_runs else None
        if not live_run:
            # Finished (or was cancelled) while the user was sitting on this
            # page — do a full rerun so the rest of the page (the upload
            # form) reappears, ready for a new extraction.
            st.rerun(scope="app")
            return
        st.markdown("### 🔴 MediaCloud extraction still running")
        _active_total = max(int(live_run.get("total") or 0), 1)
        _active_done = min(int(live_run.get("processed") or 0), _active_total)
        _active_current = live_run.get("current") or "Processing…"
        st.progress(_active_done / _active_total, text=f"Processed {_active_done:,} / {_active_total:,} · {_active_current}")
        st.info("Your extraction did not disappear. It is running in the background and its progress is stored persistently. Open **Extractions manager** to follow it or download the result when it finishes.")
        if st.button("📂 Open Extractions manager", key="open_recent_from_active_mediacloud", use_container_width=True):
            st.session_state["toolkit_input_source"] = "Extractions manager"
            st.session_state["scroll_to_top_once"] = True
            st.rerun(scope="app")

    if _active_mc_run:
        _active_run_banner()
        return

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

    if use_press_classification:
        # Group sources into National / Regional / Unclassified to make
        # selection easier — this only changes how they are displayed here;
        # the classification logic itself, and everything else, is unchanged.
        buckets = {"nationalpress": [], "regionalpress": [], "unclassified": []}
        for i, (source, count) in enumerate(sorted_sources):
            buckets[classify_source(source)].append((i, source, count))

        bucket_labels = {
            "nationalpress": "National Press",
            "regionalpress": "Regional Press",
            "unclassified": "Unclassified",
        }
        for bucket_key in ("nationalpress", "regionalpress", "unclassified"):
            entries = buckets[bucket_key]
            if not entries:
                continue
            st.markdown(f"**{bucket_labels[bucket_key]}** ({len(entries):,} sources)")
            source_cols = st.columns(2)
            for j, (i, source, count) in enumerate(entries):
                with source_cols[j % 2]:
                    checked = st.checkbox(
                        f"{source}  ·  {count:,} articles",
                        key=f"source_choice_{i}",
                    )
                    if checked:
                        selected_sources.add(source)
    else:
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
            "No custom metadata selected. The corpus will use the built-in source/date/rownumber metadata, plus type only when Greek classification is enabled."
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

            Each successfully extracted article receives up to six core metadata fields:

            - `source` — cleaned name of the publication or media source.
            - `year` — publication year.
            - `yearmonth` — publication year and month, in `YYYY-MM` format.
            - `date` — publication date, in `YYYY-MM-DD` format. Included only when the day is known; otherwise omitted.
            - `type` — built-in source category for Greek corpora: national press or regional press. It is omitted entirely for other-language corpora.
            - `rownumber` — original row number of the article in the uploaded CSV, useful for tracing the corpus entry back to the source data.

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

    # Never leave the extraction button disabled because of a stale session id
    # after a run has completed or been cancelled.
    _session_active_id = st.session_state.get("active_extraction_job_id")
    if _session_active_id:
        _session_active_meta = read_run_meta(_session_active_id) or {}
        if _session_active_meta.get("status") not in _ACTIVE_RUN_STATUSES:
            st.session_state.pop("active_extraction_job_id", None)
            _session_active_id = None

    _persistent_mc_active = any(
        m.get("status") in _ACTIVE_RUN_STATUSES
        for m in list_runs(pipeline="mediacloud")
    )

    run = st.button(
        "Begin corpus construction",
        type="primary",
        use_container_width=True,
        disabled=bool(_session_active_id or _persistent_mc_active),
    )


    def build_statistics_tables(selected_rows, corpus_text):
        """Create initial and saved year x media matrices.

        Initial counts come from the selected CSV before URL deduplication.
        Saved counts are reconstructed from the generated IRaMuTeQ TXT corpus by
        reading each saved article header and using its rownumber to recover the
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
        # Every saved article has a header containing *year_YYYY and *rownumber_N.
        saved_counts = defaultdict(lambda: defaultdict(int))
        header_re = re.compile(r"^\*\*\*\*.*?\*year_(\d{4}).*?\*rownumber_([^\s]+)")
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


    def build_daily_totals_csv(selected_rows):
        """One row per calendar date present in the initial (found) MediaCloud
        rows, with the count summed across ALL media (not broken down by
        media) — feeds the "Media coverage over time" line chart, which lets
        the viewer roll this day-level data up to month or year themselves.

        Rows whose publish_date has no day component (e.g. "2026-06") are
        bucketed onto the 1st of that month — this only affects the day
        view for those rows; month/year rollups are unaffected either way.
        """
        daily_counts = defaultdict(int)
        for row in selected_rows:
            media = (row.get("media_name", "") or "").strip()
            if not media:
                continue
            year, month, day = extract_year_month((row.get("publish_date", "") or "").strip())
            if not year:
                continue
            daily_counts[f"{year}-{month}-{day or '01'}"] += 1
        output = StringIO()
        writer = csv.writer(output)
        writer.writerow(["date", "total"])
        for date in sorted(daily_counts):
            writer.writerow([date, daily_counts[date]])
        return output.getvalue().encode("utf-8-sig")


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

    def build_coverage_bubble_spec(initial_rows, saved_rows, default_sort="volume"):
        """Vega-Lite spec: one bubble per media x year.

        Bubble size = articles initially found (how much was at stake);
        bubble color = share actually saved (coverage ratio). This makes
        under-represented media/years visible at a glance across the whole
        corpus, unlike the single-media line plot above.

        The "sort by" control is a native Vega-Lite param bound to a select
        input (not a Streamlit widget), so it is baked into the spec itself
        and keeps working when the chart is exported to a standalone .html
        file and opened outside Streamlit.
        """
        saved_lookup = defaultdict(dict)
        for row in saved_rows:
            year = str(row.get("year", "")).strip()
            if not year:
                continue
            for media, value in row.items():
                if media == "year":
                    continue
                saved_lookup[year][media] = int(value or 0)

        records = []
        totals = defaultdict(lambda: {"initial": 0, "saved": 0})
        for row in initial_rows:
            year = str(row.get("year", "")).strip()
            if not year:
                continue
            for media, value in row.items():
                if media == "year":
                    continue
                initial = int(value or 0)
                if initial <= 0:
                    continue
                saved = saved_lookup.get(year, {}).get(media, 0)
                records.append({
                    "year": int(year), "media": media, "initial": initial,
                    "saved": saved, "missing": initial - saved,
                    "ratio": round(saved / initial, 4),
                })
                totals[media]["initial"] += initial
                totals[media]["saved"] += saved

        # Pre-compute a numeric rank per media for every sort mode (0 = shown
        # first/top). Vega-Lite can't reorder a categorical axis dynamically
        # from a param on its own, so we bake all five orderings in as
        # constant-per-media fields and let a calculate transform pick the
        # active one based on the "sortMode" param.
        def ranks_from_order(order):
            return {m: i for i, m in enumerate(order)}

        order_volume = sorted(totals, key=lambda m: totals[m]["initial"], reverse=True)
        order_ratio = sorted(
            totals, key=lambda m: (totals[m]["saved"] / totals[m]["initial"]) if totals[m]["initial"] else 1,
        )
        order_alpha = sorted(totals, key=str.lower)

        national_rank = {"nationalpress": 0, "regionalpress": 1, "unclassified": 2}
        regional_rank = {"regionalpress": 0, "nationalpress": 1, "unclassified": 2}
        order_national = sorted(totals, key=lambda m: (national_rank[classify_source(m)], -totals[m]["initial"]))
        order_regional = sorted(totals, key=lambda m: (regional_rank[classify_source(m)], -totals[m]["initial"]))

        ranks = {
            "volume": ranks_from_order(order_volume),
            "ratio": ranks_from_order(order_ratio),
            "national": ranks_from_order(order_national),
            "regional": ranks_from_order(order_regional),
            "alpha": ranks_from_order(order_alpha),
        }
        for rec in records:
            for mode, rank_map in ranks.items():
                rec[f"sort_{mode}"] = rank_map[rec["media"]]

        row_step = 22
        col_step = 90

        return {
            "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
            "data": {"values": records},
            "width": {"step": col_step},
            "height": {"step": row_step},
            "background": "transparent",
            "config": {
                "axis": {"labelFontSize": 12, "titleFontSize": 13, "labelLimit": 200},
                "legend": {"labelFontSize": 12, "titleFontSize": 13, "symbolSize": 140},
                "view": {"continuousWidth": 300, "continuousHeight": 300},
            },
            "params": [
                {
                    "name": "search", "value": "",
                    "bind": {"input": "text", "name": "Search media: "},
                },
                {
                    "name": "sortMode", "value": default_sort,
                    "bind": {
                        "input": "select",
                        "name": "Sort media by: ",
                        "options": ["volume", "ratio", "national", "regional", "alpha"],
                        "labels": [
                            "Most articles found", "Worst coverage first",
                            "National press first", "Regional press first", "Alphabetical",
                        ],
                    },
                },
            ],
            "transform": [
                {"calculate": "lower(datum.media)", "as": "media_lc"},
                {"filter": "!search || indexof(datum.media_lc, lower(search)) >= 0"},
                {"calculate": "datum['sort_' + sortMode]", "as": "sortKey"},
            ],
            "mark": {"type": "circle", "opacity": 0.9, "stroke": "white", "strokeWidth": 0.5},
            "encoding": {
                "x": {
                    "field": "year", "type": "ordinal",
                    "axis": {"orient": "top", "title": None, "labelAngle": 0, "labelFontSize": 13, "labelPadding": 8},
                },
                "y": {
                    "field": "media", "type": "nominal",
                    "sort": {"field": "sortKey", "op": "min"},
                    "axis": {"title": None, "labelLimit": 200, "labelFontSize": 12},
                },
                "size": {
                    "field": "initial", "type": "quantitative",
                    "scale": {"type": "sqrt", "range": [10, 700]},
                    "legend": {"title": "Articles found"},
                },
                "color": {
                    "field": "ratio", "type": "quantitative",
                    "scale": {"domain": [0, 1], "scheme": "redyellowgreen"},
                    "legend": {"title": "Coverage", "format": ".0%"},
                },
                "tooltip": [
                    {"field": "media", "title": "Media"},
                    {"field": "year", "title": "Year"},
                    {"field": "initial", "title": "Found"},
                    {"field": "saved", "title": "Saved"},
                    {"field": "missing", "title": "Missing"},
                    {"field": "ratio", "title": "Coverage", "format": ".1%"},
                ],
            },
        }

    def build_media_coverage_line_spec(daily_rows, default_granularity="year"):
        """Vega-Lite spec for the "Media coverage over time" line chart:
        total articles initially found, summed across ALL media, with a
        day/month/year granularity control.

        daily_rows is the day-level {date, total} data from
        build_daily_totals_csv — the day/month/year rollup happens inside
        the spec itself (a native param, like search/sort on the bubble
        chart above), not in Python, so a single dataset drives all three
        views and the control keeps working in the exported standalone
        HTML too, not just live in the app.

        A fixed width (not "container" or a per-category step) means Month
        (~dozens of categories) and especially Day (up to ~thousands) don't
        all fit at once — labelOverlap thins overlapping labels out instead
        of letting them collide, so the chart never needs horizontal
        scrolling and stays easy to save/export as a static file. The line
        and tooltips still carry full detail regardless. For real day-by-day
        reading, "Focus month" (Day view only) filters down to a single
        calendar month — at most 31 points, so every label fits with no
        thinning at all. Its options are the actual months present in this
        data, not a fixed list.
        """
        records = [{"date": r["date"], "total": int(r["total"] or 0)} for r in daily_rows if r.get("date")]

        months = sorted({r["date"][:7] for r in records})
        month_names = ["January", "February", "March", "April", "May", "June",
                        "July", "August", "September", "October", "November", "December"]

        def month_label(ym):
            y, m = ym.split("-")
            return f"{month_names[int(m) - 1]} {y}"

        focus_month_options = ["All"] + months
        focus_month_labels = ["All months (thinned overview)"] + [month_label(m) for m in months]

        return {
            "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
            "data": {"values": records},
            "width": 1500,
            "height": 380,
            # "height" alone only sizes the plot's data area, not the margin
            # needed for the rotated x-axis labels below it — that margin is
            # normally auto-computed, but some hosts (e.g. Streamlit's chart
            # component) clip at that computed boundary instead of expanding
            # to fit it. This explicit bottom padding reserves the space
            # directly, regardless of how the host handles auto-sizing.
            "padding": {"left": 10, "right": 10, "top": 10, "bottom": 90},
            "autosize": {"type": "pad", "contains": "padding"},
            "background": "transparent",
            "config": {"axis": {"labelFontSize": 11, "titleFontSize": 13}},
            "params": [
                {
                    "name": "granularity", "value": default_granularity,
                    "bind": {
                        "input": "select", "name": "View by: ",
                        "options": ["day", "month", "year"],
                        "labels": ["Day", "Month", "Year"],
                    },
                },
                {
                    "name": "focusMonth", "value": "All",
                    "bind": {
                        "input": "select", "name": "Focus month (Day view only): ",
                        "options": focus_month_options, "labels": focus_month_labels,
                    },
                },
            ],
            "transform": [
                {"filter": "granularity != 'day' || focusMonth == 'All' || indexof(datum.date, focusMonth) == 0"},
                {
                    "calculate": (
                        "granularity == 'year' ? timeFormat(toDate(datum.date), '%Y') : "
                        "granularity == 'month' ? timeFormat(toDate(datum.date), '%Y-%m') : "
                        "datum.date"
                    ),
                    "as": "period",
                },
                {"aggregate": [{"op": "sum", "field": "total", "as": "period_total"}], "groupby": ["period"]},
            ],
            "layer": [
                {
                    "mark": {
                        "type": "area",
                        "line": {"color": "#b3542e", "strokeWidth": 2.5},
                        "color": {
                            "x1": 1, "y1": 1, "x2": 1, "y2": 0, "gradient": "linear",
                            "stops": [
                                {"offset": 0, "color": "rgba(179,84,46,0.02)"},
                                {"offset": 1, "color": "rgba(179,84,46,0.28)"},
                            ],
                        },
                    },
                    "encoding": {
                        "x": {
                            "field": "period", "type": "ordinal", "title": None,
                            "axis": {"labelAngle": -45, "labelOverlap": "greedy", "labelFlush": True, "labelPadding": 6},
                        },
                        "y": {"field": "period_total", "type": "quantitative", "title": "Articles initially found (all media)"},
                    },
                },
                {
                    "mark": {"type": "point", "filled": True, "size": 45, "color": "#b3542e"},
                    "encoding": {
                        "x": {"field": "period", "type": "ordinal"},
                        "y": {"field": "period_total", "type": "quantitative"},
                        "tooltip": [
                            {"field": "period", "title": "Period"},
                            {"field": "period_total", "title": "Articles found"},
                        ],
                    },
                },
            ],
        }

    def coverage_charts_to_html(bubble_spec, line_spec, title="MediaCloud extraction — coverage charts"):
        """Wrap both Vega-Lite specs into one self-contained HTML file
        (vega-embed via CDN) so the user can save/share both charts together,
        outside Streamlit."""
        bubble_json = json.dumps(bubble_spec)
        line_json = json.dumps(line_spec)
        html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><title>{title}</title>
<script src="https://cdn.jsdelivr.net/npm/vega@5"></script>
<script src="https://cdn.jsdelivr.net/npm/vega-lite@5"></script>
<script src="https://cdn.jsdelivr.net/npm/vega-embed@6"></script>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
          margin: 24px 40px; color: #1e1c1a; background: #faf9f7; }}
  h1 {{ font-size: 1.3rem; margin: 0 0 4px; }}
  h2 {{ font-size: 1.05rem; margin: 2.5rem 0 4px; border-top: 1px solid #e6e2dc; padding-top: 1.5rem; }}
  p {{ color: #6b6560; font-size: .95rem; max-width: 900px; line-height: 1.5; }}
  #bubble {{ overflow-x: auto; }}
  #line {{ max-width: 1500px; }}
</style></head>
<body>
<h1>Extraction coverage by media &amp; year</h1>
<p>Bubble size = articles found; color = share actually saved. Small, dark-red bubbles mark the
media/years most under-represented in this extraction.</p>
<div id="bubble"></div>

<h2>Media coverage — total articles initially found, over time</h2>
<p>All media summed together, per day/month/year — shows how MediaCloud's overall
coverage volume evolved, before any saving/filtering. Use "View by" to switch granularity, and
"Focus month" (Day view only) to zoom into a single month's day-by-day detail.</p>
<div id="line"></div>

<script>
  vegaEmbed('#bubble', {bubble_json}, {{actions: {{export: true, source: false, compiled: false, editor: false}}}});
  vegaEmbed('#line', {line_json}, {{actions: {{export: true, source: false, compiled: false, editor: false}}}});
</script>
</body></html>"""
        return html.encode("utf-8")

    def extraction_worker(job_id, selected_rows, custom_metadata_fields, delay, min_article_length, use_press_classification):
        with EXTRACTION_JOBS_LOCK:
            job = EXTRACTION_JOBS[job_id]
            job["status"] = "running"

        extraction_started_at = datetime.now()

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
        start_time = time.time()

        # ETA is based on a rolling window of the last ETA_WINDOW completed
        # articles (their median processing time * remaining count), not the
        # whole run's average. This makes it react to a real change in pace
        # (e.g. the site's gotten slower) within a few articles, while the
        # median — rather than the mean — keeps a single unusually slow
        # article (a retry, a big page) from swinging the estimate around.
        ETA_WINDOW = 10
        row_durations = deque(maxlen=ETA_WINDOW)

        def compute_eta(processed_count):
            remaining = total - processed_count
            if remaining <= 0:
                return 0
            if row_durations:
                per_article = statistics.median(row_durations)
            elif processed_count > 0:
                # Not enough of a rolling window yet (start of the run) —
                # fall back to the whole-run average just for these first
                # few articles.
                per_article = (time.time() - start_time) / processed_count
            else:
                return None
            return per_article * remaining

        def cancellation_requested():
            # Check both the in-memory Event and the persistent run record.
            # This allows the Extractions manager to cancel a worker even when
            # the user is viewing the run from a different Streamlit session.
            try:
                with EXTRACTION_JOBS_LOCK:
                    live_job = EXTRACTION_JOBS.get(job_id)
                    if live_job and live_job.get("cancel_event") is not None:
                        if live_job["cancel_event"].is_set():
                            return True
            except Exception:
                pass
            meta = read_run_meta(job_id) or {}
            return bool(meta.get("cancel_requested")) or meta.get("status") == "cancelling"

        def internet_reachable(timeout=3):
            """Cheap, DNS-independent check of whether *this machine* has any
            internet connectivity at all, by opening a raw TCP connection to a
            couple of well-known, extremely reliable IPs (Cloudflare's and
            Google's public DNS resolvers). Used to tell apart a genuine
            internet outage (worth waiting out) from a single host/URL being
            unreachable while everything else is fine (not worth waiting out).
            """
            for host in ("1.1.1.1", "8.8.8.8"):
                try:
                    socket.create_connection((host, 53), timeout=timeout).close()
                    return True
                except OSError:
                    continue
            return False

        def update(**kwargs):
            with EXTRACTION_JOBS_LOCK:
                job.update(kwargs)
            # Mirror a lightweight snapshot to disk so the landing page's
            # "Extractions manager" list and running-indicator can see live
            # progress without needing this browser session at all.
            update_run(job_id, **{k: v for k, v in kwargs.items() if k not in ("cancel_event",)})

        def write_failure_web(row_number, media_name, publish_date, url, reason):
            failed_buffer.write(f"[ROW {row_number}]\n")
            failed_buffer.write(f"source: {media_name}\n")
            failed_buffer.write(f"publish_date: {publish_date}\n")
            failed_buffer.write(f"url: {url}\n")
            failed_buffer.write(f"error: {reason}\n")
            failed_buffer.write("-" * 70 + "\n\n")

        session = requests.Session()
        total = len(unique_rows)
        update_run(job_id, total=total, cancel_requested=False)

        try:
            for number, row in enumerate(unique_rows, 1):
                if cancellation_requested():
                    cancelled = True
                    break

                row_start_time = time.time()
                raw_number = row.get("_rawnb")
                media_name = (row.get("media_name", "") or "").strip()
                publish_date = (row.get("publish_date", "") or "").strip()
                raw_url = (row.get("url", "") or "").strip()
                eta_seconds = compute_eta(processed)
                update(processed=number - 1, total=total, current=f"{media_name} · MediaCloud row {raw_number}",
                       eta_seconds=eta_seconds, connection_lost=False)

                if not media_name:
                    write_failure_web(raw_number, media_name, publish_date, raw_url, "Missing media_name")
                    errors += 1; missing_metadata += 1
                    processed = number
                    row_durations.append(time.time() - row_start_time)
                    update(processed=processed, successful=successful, errors=errors, eta_seconds=compute_eta(processed))
                    continue

                year, month, day = extract_year_month(publish_date)
                if not year:
                    write_failure_web(raw_number, media_name, publish_date, raw_url, "Could not parse publish_date")
                    errors += 1; missing_metadata += 1
                    processed = number
                    row_durations.append(time.time() - row_start_time)
                    update(processed=processed, successful=successful, errors=errors, eta_seconds=compute_eta(processed))
                    continue

                if not valid_url(raw_url):
                    write_failure_web(raw_number, media_name, publish_date, raw_url, "Invalid or missing URL")
                    errors += 1; missing_metadata += 1
                    processed = number
                    row_durations.append(time.time() - row_start_time)
                    update(processed=processed, successful=successful, errors=errors, eta_seconds=compute_eta(processed))
                    continue

                url = clean_url(raw_url)

                # Retry loop: a broad connectivity failure (no response at all —
                # DNS/connection/timeout) is treated as "connection lost". Two
                # very different situations produce that same exception, and
                # they need very different handling:
                #   - a genuine internet outage on this machine → worth waiting
                #     out indefinitely, since it always recovers eventually
                #   - this one host/URL being unreachable while the rest of the
                #     internet is fine (dead domain, a block on this server's
                #     IP, DNS trouble for just that host) → not worth waiting
                #     out; retry a bounded number of times (MAX_CONNECTION_RETRIES)
                #     then log it as failed and move on, so one bad link can't
                #     stall the whole run
                # A response that DID come back (even an HTTP error) is not a
                # connectivity issue and is handled as a normal per-article error.
                outage_attempt = 0
                host_attempt = 0
                article_handled = False
                while not article_handled:
                    if cancellation_requested():
                        cancelled = True
                        break
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
                                if day:
                                    header_parts.append(f"*date_{year}-{month}-{day}")
                                if press_type is not None:
                                    header_parts.append(f"*type_{press_type}")
                                header_parts.extend([f"*rownumber_{raw_number}", *custom_tokens])
                                output_buffer.write(" ".join(header_parts) + "\n")
                                output_buffer.write(article + "\n\n")
                                successful += 1
                                saved_counts[media_name][year] += 1

                                if press_type == "unclassified" and use_press_classification:
                                    write_failure_web(raw_number, media_name, publish_date, raw_url,
                                                      "Source could not be classified as National Press or Regional Press")
                        article_handled = True

                    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                        # No response reached us at all. Check whether this
                        # machine has internet connectivity at all right now —
                        # that's the difference between "wait it out" and
                        # "give up on this one link".
                        if not internet_reachable():
                            # Genuine internet outage: always worth waiting out,
                            # since it will recover — retried indefinitely, no cap.
                            outage_attempt += 1
                            wait_s = min(60, 5 * (2 ** (outage_attempt - 1)))  # 5s, 10s, 20s, 40s, 60s, 60s...
                            update(connection_lost=True,
                                   current=f"Internet connection appears to be down — retrying in {wait_s}s (attempt {outage_attempt})…")
                            waited = 0.0
                            while waited < wait_s:
                                if cancellation_requested():
                                    cancelled = True
                                    break
                                time.sleep(0.5)
                                waited += 0.5
                            if cancelled:
                                break
                            update(connection_lost=False)
                            continue  # retry the same row, no limit

                        # Internet is reachable — this one host/URL is the
                        # problem (dead domain, DNS trouble just for it, a
                        # block on this server's IP, etc.), not your connection.
                        # Retry a bounded number of times so a single bad link
                        # can't stall the whole run.
                        host_attempt += 1
                        if host_attempt > MAX_CONNECTION_RETRIES:
                            write_failure_web(
                                raw_number, media_name, publish_date, raw_url,
                                f"Connection error (no response received) after {host_attempt} attempt(s), "
                                f"though internet connectivity looks fine — likely this host/URL specifically "
                                f"is unreachable. Skipping this article and continuing. Last error: "
                                f"{type(e).__name__}: {e}",
                            )
                            errors += 1; request_errors += 1
                            update(connection_lost=False)
                            article_handled = True
                            continue
                        wait_s = min(60, 5 * (2 ** (host_attempt - 1)))  # 5s, 10s, 20s, 40s, 60s, 60s...
                        update(connection_lost=True,
                               current=f"This article's request failed — retrying in {wait_s}s (attempt {host_attempt}/{MAX_CONNECTION_RETRIES})…")
                        waited = 0.0
                        while waited < wait_s:
                            if cancellation_requested():
                                cancelled = True
                                break
                            time.sleep(0.5)
                            waited += 0.5
                        if cancelled:
                            break
                        update(connection_lost=False)
                        continue  # retry the same row

                    except requests.exceptions.RequestException as e:
                        write_failure_web(raw_number, media_name, publish_date, raw_url, f"Request error: {type(e).__name__}: {e}")
                        errors += 1; request_errors += 1
                        article_handled = True
                    except Exception as e:
                        write_failure_web(raw_number, media_name, publish_date, raw_url, f"Unexpected error: {type(e).__name__}: {e}")
                        errors += 1; unexpected_errors += 1
                        article_handled = True

                if cancelled:
                    break

                processed = number
                update(processed=processed, successful=successful, errors=errors,
                       national_count=national_count, regional_count=regional_count,
                       unclassified_count=unclassified_count, eta_seconds=compute_eta(processed))

                if number < total:
                    for _ in range(max(0, int(delay * 10))):
                        if cancellation_requested():
                            cancelled = True
                            break
                        time.sleep(0.1)
                    if cancelled:
                        break

                # Record this row's full wall-clock cost (network/extraction time
                # plus the deliberate inter-request delay) so the *next* row's
                # ETA reflects real, current pace — see ETA_WINDOW above.
                row_durations.append(time.time() - row_start_time)

            corpus_text = replace_ellipses_with_space(space_out_guillemets(output_buffer.getvalue()))
            initial_stats_bytes, saved_stats_bytes, invalid_date_rows = build_statistics_tables(selected_rows, corpus_text)
            initial_stats_rows = list(csv.DictReader(StringIO(initial_stats_bytes.decode("utf-8-sig"))))
            saved_stats_rows = list(csv.DictReader(StringIO(saved_stats_bytes.decode("utf-8-sig"))))
            plot_bytes = build_statistics_plot_png(initial_stats_rows, saved_stats_rows)
            daily_totals_bytes = build_daily_totals_csv(selected_rows)
            daily_totals_rows = list(csv.DictReader(StringIO(daily_totals_bytes.decode("utf-8-sig"))))
            coverage_spec = build_coverage_bubble_spec(initial_stats_rows, saved_stats_rows, default_sort="volume")
            line_spec = build_media_coverage_line_spec(daily_totals_rows, default_granularity="year")
            coverage_html_bytes = coverage_charts_to_html(coverage_spec, line_spec)

            corpus_bytes = corpus_text.encode("utf-8")
            failed_bytes = failed_buffer.getvalue().encode("utf-8")

            def _format_bytes(num_bytes):
                size = float(num_bytes)
                for unit in ("bytes", "KB", "MB", "GB"):
                    if size < 1024 or unit == "GB":
                        return f"{size:.1f} {unit}" if unit != "bytes" else f"{int(size)} bytes"
                    size /= 1024

            output_name = f"{job_id}_corpus.txt"
            summary_lines = [
                "MediaCloud extraction — summary log",
                f"IRaMuTeQ corpus construction toolkit, version {APP_VERSION}",
                "",
                f"Date and time of extraction: {extraction_started_at.strftime('%Y-%m-%d %H:%M:%S')}",
                f"Input CSV: {uploaded.name if uploaded is not None else 'unknown'} ({_format_bytes(uploaded.size)})" if uploaded is not None else "Input CSV: unknown",
                f"Output corpus: {output_name} ({_format_bytes(len(corpus_bytes))})",
                "",
                f"Media selected ({len(selected_sources):,}):",
            ]
            summary_lines.extend(f"  - {source}" for source in sorted(selected_sources, key=str.lower))
            summary_lines.extend([
                "",
                f"Articles saved: {successful:,}",
                f"Articles failed: {errors:,}",
            ])
            summary_bytes = ("\n".join(summary_lines) + "\n").encode("utf-8")

            result = {
                "corpus": corpus_bytes, "failed": failed_bytes,
                "initial_stats": initial_stats_bytes, "saved_stats": saved_stats_bytes,
                "daily_totals": daily_totals_bytes,
                "plot": plot_bytes, "coverage_html": coverage_html_bytes, "successful": successful, "errors": errors,
                "duplicate_count": duplicate_count, "national_count": national_count,
                "regional_count": regional_count, "unclassified_count": unclassified_count,
                "rows_processed": processed, "missing_metadata": missing_metadata,
                "request_errors": request_errors, "extraction_errors": extraction_errors,
                "short_articles": short_articles, "unexpected_errors": unexpected_errors,
                "invalid_date_rows": invalid_date_rows, "custom_metadata_columns": [x[0] for x in custom_metadata_fields],
                "cancelled": cancelled,
            }
            update(status="cancelled" if cancelled else "completed", result=result, processed=processed)
            write_run_file(job_id, "corpus.txt", corpus_bytes)
            write_run_file(job_id, "failed.txt", failed_bytes)
            write_run_file(job_id, "summary.txt", summary_bytes)
            write_run_file(job_id, "initial_stats.csv", initial_stats_bytes)
            write_run_file(job_id, "saved_stats.csv", saved_stats_bytes)
            write_run_file(job_id, "daily_totals.csv", daily_totals_bytes)
            write_run_file(job_id, "coverage_chart.html", coverage_html_bytes)
            update_run(
                job_id, status="cancelled" if cancelled else "completed", processed=processed,
                successful=successful, errors=errors, duplicate_count=duplicate_count,
                national_count=national_count, regional_count=regional_count,
                unclassified_count=unclassified_count, connection_lost=False,
            )
        except Exception as e:
            update(status="error", error=f"{type(e).__name__}: {e}")
            update_run(job_id, status="error", error_message=f"{type(e).__name__}: {e}")
        finally:
            session.close()


    if run:
        job_id = create_run(
            "mediacloud", uploaded.name if uploaded is not None else "MediaCloud extraction",
            total=len(selected_rows),
        )
        with EXTRACTION_JOBS_LOCK:
            EXTRACTION_JOBS[job_id] = {
                "status": "starting", "processed": 0, "total": 0,
                "successful": 0, "errors": 0, "current": "Preparing requests…",
                "cancel_event": threading.Event(),
            }
        st.session_state["active_extraction_job_id"] = job_id
        st.session_state.pop("mc_confirm_cancel", None)
        worker = threading.Thread(
            target=extraction_worker,
            args=(job_id, selected_rows, custom_metadata_fields, delay, min_article_length, use_press_classification),
            daemon=True,
        )
        worker.start()
        # Extraction progress and cancellation now live entirely in
        # "Manage extractions" — send the user straight there.
        st.session_state["toolkit_input_source"] = "Extractions manager"
        st.session_state["scroll_to_top_once"] = True
        st.rerun()


    @st.fragment(run_every="1s")
    def extraction_monitor():
        job_id = st.session_state.get("active_extraction_job_id")
        if not job_id:
            return

        with EXTRACTION_JOBS_LOCK:
            job = dict(EXTRACTION_JOBS.get(job_id, {}))
        meta = read_run_meta(job_id) or {}
        if not job and not meta:
            st.session_state.pop("active_extraction_job_id", None)
            return

        status = job.get("status") or meta.get("status", "unknown")
        total = max(int(job.get("total") or meta.get("total") or 0), 1)
        done = min(int(job.get("processed") or meta.get("processed") or 0), total)
        current = job.get("current") or meta.get("current") or ""

        if status in {"starting", "running"}:
            st.markdown("### Extraction progress")
            st.info("You can safely close this tab or switch to another page — the extraction keeps running in the background. The progress is stored persistently.")
            eta_seconds = job.get("eta_seconds")
            if eta_seconds is None:
                eta_seconds = meta.get("eta_seconds")
            eta_text = f" · ~{format_eta(eta_seconds)} remaining" if eta_seconds is not None else " · estimating remaining time…"
            if job.get("connection_lost") or meta.get("connection_lost"):
                st.warning(f"⚠ {current or 'Connection lost — retrying…'}")
            st.progress(done / total, text=f"Processed {done:,} / {total:,}{eta_text} · {current}")

            c1, c2 = st.columns([1, 3])
            with c1:
                if not st.session_state.get("mc_confirm_cancel"):
                    if st.button("⏹ Cancel extraction", type="primary", use_container_width=True, key=f"cancel_{job_id}"):
                        st.session_state["mc_confirm_cancel"] = True
                        st.rerun(scope="fragment")
                else:
                    st.warning("Cancel this extraction? Progress made so far will still be saved and downloadable, but the rest of the CSV won't be processed.")
                    cc1, cc2 = st.columns(2)
                    with cc1:
                        if st.button("Yes, cancel", type="primary", use_container_width=True, key=f"cancel_confirm_{job_id}"):
                            with EXTRACTION_JOBS_LOCK:
                                live_job = EXTRACTION_JOBS.get(job_id)
                                if live_job and live_job.get("cancel_event") is not None:
                                    live_job["cancel_event"].set()
                                    live_job["status"] = "cancelling"
                            update_run(job_id, status="cancelling", cancel_requested=True)
                            st.session_state.pop("mc_confirm_cancel", None)
                            st.rerun(scope="fragment")
                    with cc2:
                        if st.button("No, keep going", use_container_width=True, key=f"cancel_back_{job_id}"):
                            st.session_state.pop("mc_confirm_cancel", None)
                            st.rerun(scope="fragment")
            with c2:
                st.caption("Cancellation stops the workflow after the current web request finishes.")
            return

        if status == "cancelling":
            st.markdown("### Extraction progress")
            st.info("Cancelling extraction… the current request will finish, then processing will stop.")
            st.progress(done / total, text=f"Processed {done:,} / {total:,} · Cancelling…")
            return

        if status == "error":
            st.error(f"Extraction failed: {job.get('error') or meta.get('error_message') or 'Unknown error'}")
            st.session_state.pop("active_extraction_job_id", None)
            st.session_state.pop("mc_confirm_cancel", None)
            st.rerun(scope="app")
            return

        # Finished: save the result in session state and force one full rerun.
        # This immediately unlocks the MediaCloud form for a new extraction.
        out = job.get("result")
        if out is not None:
            st.session_state["research_outputs"] = out
        st.session_state["last_extraction_job_id"] = job_id
        st.session_state.pop("active_extraction_job_id", None)
        st.session_state.pop("mc_confirm_cancel", None)
        st.rerun(scope="app")

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
        st.caption("Initial counts come from the selected CSV. Saved counts are reconstructed from the generated .txt corpus using each article header and its original MediaCloud row number (rownumber).")
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

        st.markdown("#### Coverage overview — which media/years are under-represented")
        coverage_spec_live = build_coverage_bubble_spec(initial_rows_for_plot, saved_rows_for_plot)
        st.caption("Bubble size = articles found · bubble color = share actually saved. Use the built-in search box and \"Sort media by\" dropdown above the chart to explore. Small, dark-red bubbles are what to check first.")
        # use_container_width=False: the spec uses fixed step sizing (a set
        # pixel width per year column) so labels never get squeezed by
        # legends/media names — Streamlit scrolls horizontally if it doesn't fit.
        st.vega_lite_chart(coverage_spec_live, use_container_width=False)

        st.markdown("#### Media coverage — total articles initially found, over time")
        daily_rows_for_plot = list(csv.DictReader(StringIO(out["daily_totals"].decode("utf-8-sig")))) if out.get("daily_totals") else []
        line_spec_live = build_media_coverage_line_spec(daily_rows_for_plot)
        st.caption("All media summed together, per day/month/year — shows how overall coverage volume evolved, before any saving/filtering. Use \"View by\" above the chart to switch granularity, and \"Focus month\" (Day view only) to zoom into a single month's day-by-day detail.")
        st.vega_lite_chart(line_spec_live, use_container_width=False)

        st.download_button(
            "⬇ Coverage graphs (.html)", data=coverage_charts_to_html(coverage_spec_live, line_spec_live),
            file_name="coverage_chart.html", mime="text/html", key="coverage_html_download_persistent",
            help="A self-contained interactive HTML file with both charts and the same search/sort/view controls — open it in any browser, no Streamlit needed.",
        )

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

                **IRaMuTeQ metadata** retain the original MediaCloud row number through the `rownumber` field, allowing the resulting corpus to be traced back to the source CSV.
                """
            )

    st.markdown(
        '<div class="footer-line">'
        'MediaCloud → IRaMuTeQ · corpus preparation interface'
        '</div>',
        unsafe_allow_html=True,
    )


# ============================================================
# TOOL 3: TikTok Research API -> IRaMuTeQ
# Each user enters their OWN Research API credentials (client key +
# secret of an approved TikTok research project). Credentials live only
# in the browser session and in the worker thread's memory: they are
# never written to /app/data, to run metadata, or to any log.
# ============================================================
TIKTOK_TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
TIKTOK_VIDEO_QUERY_URL = "https://open.tiktokapis.com/v2/research/video/query/"
TIKTOK_FIELDS = [
    "id", "create_time", "username", "region_code", "video_description",
    "voice_to_text", "hashtag_names", "music_id", "video_duration",
    "view_count", "like_count", "comment_count", "share_count",
]
TIKTOK_WINDOW_DAYS = 30          # the API accepts at most 30 days per request
TIKTOK_PAGE_SIZE = 100           # max_count upper bound
TIKTOK_PAGE_DELAY = 1.0          # politeness delay between pages (seconds)

TIKTOK_METADATA_CHOICES = {
    "date": "Publication date (*date_2026-09-14)",
    "year": "Year (*year_2026)",
    "month": "Month (*month_202609)",
    "region": "Region code (*region_fr)",
    "user": "Account username (*user_…)",
    "views": "Views, in thirds of this corpus (*views_low / medium / high)",
    "likes": "Likes, in thirds of this corpus (*likes_low / medium / high)",
    "comments": "Comments, in thirds of this corpus (*comments_low / medium / high)",
    "shares": "Shares, in thirds of this corpus (*shares_low / medium / high)",
    "videoid": "TikTok video ID, for traceability (*videoid_…)",
    "texttype": "Text type of the entry (*texttype_description / transcript / both)",
}
TIKTOK_DEFAULT_METADATA = ["date", "year", "month", "region", "texttype", "views", "videoid"]
TIKTOK_ENGAGEMENT_EXPLANATION = (
    "**What do low / medium / high mean?** For each engagement variable, the videos of *your* corpus "
    "are ranked from the least to the most viewed (or liked, commented, shared) and cut into three groups "
    "of roughly equal size: **low** = the bottom third, **medium** = the middle third, **high** = the top third. "
    "The groups are therefore relative to the corpus you collected, not absolute TikTok standards: "
    "in one corpus *high* may start at 5,000 views, in another at 2 million. The exact cut-off values used "
    "for your corpus are written in the extraction's **Summary log** (Extractions manager). "
    "*missing* means TikTok returned no value; *na* means the corpus has fewer than 3 videos."
)


class TikTokAPIError(Exception):
    def __init__(self, message, status=None, code=None):
        super().__init__(message)
        self.status = status
        self.code = code


class TikTokQuotaExceeded(TikTokAPIError):
    pass



def tiktok_get_token(client_key, client_secret):
    import requests
    try:
        r = requests.post(
            TIKTOK_TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={"client_key": client_key, "client_secret": client_secret,
                  "grant_type": "client_credentials"},
            timeout=30,
        )
    except requests.RequestException as e:
        raise TikTokAPIError(f"Could not reach TikTok to obtain an access token ({type(e).__name__}).")
    try:
        payload = r.json()
    except ValueError:
        payload = {}
    token = payload.get("access_token")
    if r.status_code != 200 or not token:
        msg = payload.get("error_description") or payload.get("error") or (r.text or "")[:300]
        raise TikTokAPIError(f"Could not obtain an access token: {msg}", status=r.status_code, code=payload.get("error"))
    return token, int(payload.get("expires_in") or 7200)


class _TikTokToken:
    """Client access token, refreshed shortly before its 2-hour expiry."""

    def __init__(self, client_key, client_secret):
        self._key, self._secret = client_key, client_secret
        self.token, self.expires_at = None, 0.0

    def get(self, force=False):
        import time
        if force or not self.token or time.time() > self.expires_at - 300:
            self.token, ttl = tiktok_get_token(self._key, self._secret)
            self.expires_at = time.time() + ttl
        return self.token


def tiktok_query_page(token_mgr, query, start_date, end_date, cursor=0, search_id=None, cancelled=None):
    """One page of /research/video/query/. Returns the response's `data`.

    Refreshes the token on 401, backs off on 5xx / network errors, waits on
    a 429 and raises TikTokQuotaExceeded if it persists (daily quota)."""
    import time
    import requests
    body = {"query": query, "start_date": start_date, "end_date": end_date,
            "max_count": TIKTOK_PAGE_SIZE, "cursor": int(cursor or 0), "is_random": False}
    if search_id:
        body["search_id"] = search_id
    url = f"{TIKTOK_VIDEO_QUERY_URL}?fields={','.join(TIKTOK_FIELDS)}"
    backoffs = [5, 15, 30, 60, 90]
    rate_limit_waits = 0
    force_refresh = False
    last_problem = ""
    attempt = 0
    while attempt <= len(backoffs):
        if cancelled and cancelled():
            raise TikTokAPIError("Cancelled")
        token = token_mgr.get(force=force_refresh)
        force_refresh = False
        try:
            r = requests.post(url, json=body, timeout=180, headers={
                "authorization": f"bearer {token}", "Content-Type": "application/json"})
        except requests.RequestException as e:
            last_problem = f"network error ({type(e).__name__})"
            if attempt < len(backoffs):
                time.sleep(backoffs[attempt])
            attempt += 1
            continue
        try:
            payload = r.json()
        except ValueError:
            payload = {}
        err = payload.get("error") or {}
        code = err.get("code") or ""
        message = err.get("message") or (r.text or "")[:300]
        if r.status_code == 200 and code in ("", "ok"):
            return payload.get("data") or {}
        if r.status_code == 401 or code in ("access_token_invalid", "access_token_expired"):
            if attempt == 0 or last_problem != "401":
                force_refresh = True
                last_problem = "401"
                attempt += 1
                continue
            raise TikTokAPIError(f"Authentication failed: {message}", status=r.status_code, code=code)
        if r.status_code == 429 or code == "rate_limit_exceeded":
            if rate_limit_waits < 2:
                rate_limit_waits += 1
                time.sleep(60)
                continue
            raise TikTokQuotaExceeded(
                "TikTok rate limit / daily quota reached. Resume this extraction later "
                "(the daily quota resets every 24 hours).", status=r.status_code, code=code)
        if r.status_code >= 500:
            last_problem = f"HTTP {r.status_code} {code}: {message}"
            if attempt < len(backoffs):
                time.sleep(backoffs[attempt])
            attempt += 1
            continue
        raise TikTokAPIError(f"HTTP {r.status_code} {code}: {message}", status=r.status_code, code=code)
    raise TikTokAPIError(f"TikTok kept failing after several retries: {last_problem}")


def tiktok_date_windows(start, end, days=TIKTOK_WINDOW_DAYS):
    """Split [start, end] (dates, inclusive) into YYYYMMDD windows of <= `days` days."""
    from datetime import timedelta
    windows, cur = [], start
    while cur <= end:
        w_end = min(cur + timedelta(days=days - 1), end)
        windows.append([cur.strftime("%Y%m%d"), w_end.strftime("%Y%m%d")])
        cur = w_end + timedelta(days=1)
    return windows


def _split_terms(text):
    terms = []
    for chunk in re.split(r"[\n,;]+", text or ""):
        chunk = chunk.strip().lstrip("#@").strip()
        if chunk and chunk not in terms:
            terms.append(chunk)
    return terms


def tiktok_build_query(keywords, hashtags, usernames, regions, exclude_keywords,
                       keyword_mode="any", hashtag_mode="any", combine="or"):
    """Build a Research API query (TikTok's query object has one level only:
    every `and` condition must hold, at least one `or` condition must hold,
    and no `not` condition may hold).

    keyword_mode / hashtag_mode: "any" (OR within the group, one IN condition)
    or "all" (AND within the group, one EQ condition per term).
    combine: how the keyword group and the hashtag group are joined, "or"/"and".
    Accounts and regions are always restrictions (AND), each matching any listed value.

    Returns (query, problem) — problem is a message when the requested logic
    cannot be expressed in TikTok's one-level query syntax."""
    def group(field, terms, mode):
        if not terms:
            return []
        if mode == "all" and len(terms) > 1:
            return [{"operation": "EQ", "field_name": field, "field_values": [t]} for t in terms]
        return [{"operation": "IN", "field_name": field, "field_values": list(terms)}]

    kw = group("keyword", keywords, keyword_mode)
    ht = group("hashtag_name", hashtags, hashtag_mode)
    and_c, or_c = [], []
    if kw and ht and combine == "or":
        if len(kw) > 1 or len(ht) > 1:
            return {}, ("TikTok's query syntax cannot express “(all of these keywords) OR (hashtags)”. "
                        "Choose “any” for both groups, combine them with AND, or write the query yourself "
                        "in the advanced JSON box.")
        or_c = kw + ht
    else:
        and_c = kw + ht
    if usernames:
        and_c.append({"operation": "IN", "field_name": "username", "field_values": list(usernames)})
    if regions:
        and_c.append({"operation": "IN", "field_name": "region_code", "field_values": list(regions)})
    not_c = [{"operation": "IN", "field_name": "keyword", "field_values": list(exclude_keywords)}] if exclude_keywords else []
    q = {}
    if and_c:
        q["and"] = and_c
    if or_c:
        q["or"] = or_c
    if not_c:
        q["not"] = not_c
    return q, None


def tiktok_describe_query(keywords, hashtags, usernames, regions, exclude_keywords,
                          keyword_mode, hashtag_mode, combine):
    """Plain-language reading of the query, shown to the user before launch."""
    def phrase(terms, mode, what):
        quoted = ", ".join(f"“{t}”" for t in terms)
        if len(terms) == 1:
            return f"the {what} {quoted}"
        return f"{'ALL' if mode == 'all' else 'ANY'} of the {what}s {quoted}"
    parts = []
    content = [p for p in (phrase(keywords, keyword_mode, "keyword") if keywords else "",
                           phrase(hashtags, hashtag_mode, "hashtag") if hashtags else "") if p]
    if content:
        parts.append(f" {combine.upper()} ".join(content))
    if usernames:
        parts.append("posted by " + (f"@{usernames[0]}" if len(usernames) == 1 else "any of " + ", ".join("@" + u for u in usernames)))
    if regions:
        parts.append("from region " + " or ".join(regions))
    text = "Videos containing " + " — AND — ".join(parts) if parts else ""
    if exclude_keywords:
        text += " — but NOT containing " + " or ".join(f"“{k}”" for k in exclude_keywords)
    return text


# ---------- corpus construction (module-level, used by the worker) ----------
_TT_URL_RE = re.compile(r"https?://\S+")
_TT_TAG_RE = re.compile(r"[#@](\w+)", re.UNICODE)
_TT_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF"
    "\U00002190-\U000021FF\U00002B00-\U00002BFF\U0000FE0F\U0000200D\U000020E3]+")


def _tt_clean_text(text, opts):
    if not text:
        return ""
    text = str(text).replace("﻿", "").replace("\t", " ")
    for ch in ("*", "£"):           # structural characters in IRaMuTeQ
        text = text.replace(ch, " ")
    if opts.get("remove_urls", True):
        text = _TT_URL_RE.sub(" ", text)
    if opts.get("strip_tags", True):
        text = _TT_TAG_RE.sub(r"\1", text)
    if opts.get("remove_emojis", True):
        text = _TT_EMOJI_RE.sub(" ", text)
    if opts.get("lowercase", True):
        text = text.lower()         # Python's lower() handles Greek final sigma
    text = re.sub(r"\.{3,}|…", " ", text)
    text = re.sub(r"«\s*", "« ", text)
    text = re.sub(r"\s*»", " »", text)
    text = re.sub(r"\s+", " ", text.replace("\r", "\n"))
    return text.strip()


def _tt_meta_token(value, fallback="missing"):
    import unicodedata
    value = "" if value is None else str(value).strip().lower()
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value or fallback


def _tt_tercile_binner(values):
    """Return (binner, (q1, q2)): < q1 low, < q2 medium, otherwise high."""
    nums = sorted(v for v in values if isinstance(v, (int, float)))
    if len(nums) < 3:
        return (lambda v: "missing" if not isinstance(v, (int, float)) else "na"), None
    q1, q2 = nums[len(nums) // 3], nums[(2 * len(nums)) // 3]

    def binner(v):
        if not isinstance(v, (int, float)):
            return "missing"
        if v < q1:
            return "low"
        if v < q2:
            return "medium"
        return "high"
    return binner, (q1, q2)


def _tt_video_datetime(v):
    from datetime import datetime as _dt, timezone as _tz
    try:
        return _dt.fromtimestamp(int(v.get("create_time")), tz=_tz.utc)
    except Exception:
        return None


def tiktok_build_corpus(videos, opts):
    """Return (corpus_bytes, failed_log_bytes, stats)."""
    import io
    text_source = opts.get("text_source", "description")
    metadata = opts.get("metadata", TIKTOK_DEFAULT_METADATA)
    min_length = int(opts.get("min_length") or 0)
    count_key = {"views": "view_count", "likes": "like_count", "comments": "comment_count", "shares": "share_count"}
    binners, thresholds = {}, {}
    for f in count_key:
        if f in metadata:
            binners[f], thresholds[f] = _tt_tercile_binner([v.get(count_key[f]) for v in videos])

    out, log = io.StringIO(), io.StringIO()
    saved = failed = duplicates = empty = short = 0
    seen_text = set()
    videos = sorted(videos, key=lambda v: int(v.get("create_time") or 0))
    for v in videos:
        vid = str(v.get("id", ""))
        desc = _tt_clean_text(v.get("video_description"), opts)
        transcript = _tt_clean_text(v.get("voice_to_text"), opts)
        if text_source == "transcript":
            text, texttype = transcript, "transcript"
        elif text_source == "both":
            text = " ".join(t for t in (desc, transcript) if t)
            # what this entry actually contains (a video may lack one of the two)
            texttype = "both" if (desc and transcript) else ("description" if desc else "transcript")
        else:
            text, texttype = desc, "description"
        if not text:
            failed += 1; empty += 1
            log.write(f"[VIDEO {vid}]\nerror: Empty text ({text_source})\n{'-'*70}\n\n")
            continue
        if min_length and len(text) < min_length:
            failed += 1; short += 1
            log.write(f"[VIDEO {vid}]\nerror: Text shorter than the minimum length ({len(text)} < {min_length} characters)\n{'-'*70}\n\n")
            continue
        if opts.get("dedupe_text", True):
            if text in seen_text:
                duplicates += 1
                log.write(f"[VIDEO {vid}]\nerror: Duplicate text (identical to an earlier video)\n{'-'*70}\n\n")
                continue
            seen_text.add(text)
        dt = _tt_video_datetime(v)
        parts = ["****"]
        for field in metadata:
            if field == "date":
                parts.append(f"*date_{dt.strftime('%Y-%m-%d') if dt else 'missing'}")
            elif field == "year":
                parts.append(f"*year_{dt.strftime('%Y') if dt else 'missing'}")
            elif field == "month":
                parts.append(f"*month_{dt.strftime('%Y%m') if dt else 'missing'}")
            elif field == "region":
                parts.append(f"*region_{_tt_meta_token(v.get('region_code'))}")
            elif field == "user":
                parts.append(f"*user_{_tt_meta_token(v.get('username'))}")
            elif field in binners:
                parts.append(f"*{field}_{binners[field](v.get(count_key[field]))}")
            elif field == "videoid":
                parts.append(f"*videoid_{_tt_meta_token(vid)}")
            elif field == "texttype":
                parts.append(f"*texttype_{texttype}")
        out.write(" ".join(parts) + "\n" + text + "\n\n")
        saved += 1
    stats = {"input": len(videos), "saved": saved, "failed": failed, "duplicates": duplicates,
             "empty_text": empty, "filtered_short": short, "thresholds": thresholds}
    return out.getvalue().encode("utf-8"), log.getvalue().encode("utf-8"), stats


def tiktok_videos_to_csv(videos):
    import csv
    import io
    buf = io.StringIO()
    cols = ["id", "create_time_utc"] + [f for f in TIKTOK_FIELDS if f not in ("id", "create_time")]
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for v in sorted(videos, key=lambda v: int(v.get("create_time") or 0)):
        row = dict(v)
        dt = _tt_video_datetime(v)
        row["create_time_utc"] = dt.strftime("%Y-%m-%d %H:%M:%S") if dt else ""
        for k, val in list(row.items()):
            if isinstance(val, list):
                row[k] = "|".join(str(x) for x in val)
        w.writerow(row)
    return buf.getvalue().encode("utf-8-sig")


def _tt_load_videos(run_id):
    data = read_run_file(run_id, "videos.jsonl") or b""
    videos, ids = [], set()
    for line in data.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            v = json.loads(line)
        except ValueError:
            continue
        vid = str(v.get("id", ""))
        if vid and vid not in ids:
            ids.add(vid)
            videos.append(v)
    return videos, ids


def tiktok_finalize_outputs(run_id, status_label=None):
    """(Re)build corpus/log/CSV/summary from everything fetched so far."""
    params = read_run_json(run_id, "params.json", default={}) or {}
    videos, _ = _tt_load_videos(run_id)
    corpus, failed_log, stats = tiktok_build_corpus(videos, params.get("corpus_options", {}))
    write_run_file(run_id, "corpus.txt", corpus)
    write_run_file(run_id, "failed.txt", failed_log)
    write_run_file(run_id, "videos.csv", tiktok_videos_to_csv(videos))
    activity = (read_run_file(run_id, "activity.log") or b"").decode("utf-8", errors="replace")
    meta = read_run_meta(run_id) or {}
    summary = [
        "TikTok Research API → IRaMuTeQ — summary log",
        f"IRaMuTeQ corpus construction toolkit, version {meta.get('app_version') or APP_VERSION}",
        "=" * 60,
        f"Run: {run_id}",
        f"Label: {meta.get('label', '')}",
        f"Status: {status_label or meta.get('status', '')}",
        f"Started: {meta.get('started_at', '')}",
        f"Last update: {datetime.now().isoformat(timespec='seconds')}",
        f"Date range: {params.get('start_date')} → {params.get('end_date')} ({len(params.get('windows', []))} window(s) of ≤{TIKTOK_WINDOW_DAYS} days)",
        f"Max videos: {params.get('max_videos') or 'no limit'}",
        f"Fields requested: {', '.join(TIKTOK_FIELDS)}",
        "",
        f"Query (plain language): {params.get('query_reading', '')}",
        "Query sent to the API:",
        json.dumps(params.get("query", {}), ensure_ascii=False, indent=2),
        "",
        "Corpus options:",
        json.dumps(params.get("corpus_options", {}), ensure_ascii=False, indent=2),
        "",
        f"Unique videos fetched: {len(videos):,}",
        f"Texts saved to corpus: {stats['saved']:,}",
        f"Empty text: {stats['empty_text']:,} · Too short: {stats['filtered_short']:,} · Duplicate text: {stats['duplicates']:,}",
        "",
        "Engagement variables (thirds of the videos fetched):",
    ] + ([f"  *{f}: low < {t[0]:,} ≤ medium < {t[1]:,} ≤ high" if t else f"  *{f}: fewer than 3 videos — all 'na'"
          for f, t in stats["thresholds"].items()] or ["  (none selected)"]) + [
        "  low = bottom third, medium = middle third, high = top third of this corpus;",
        "  the cut-offs are relative to this corpus, not absolute TikTok standards.",
        "",
        "Activity:",
        activity,
    ]
    write_run_file(run_id, "summary.txt", "\n".join(summary).encode("utf-8"))
    return stats, len(videos)


def _tiktok_log(run_id, line):
    append_run_file(run_id, "activity.log", f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {line}\n".encode("utf-8"))


def _tiktok_worker(run_id, client_key, client_secret, cancel_event):
    import time
    params = read_run_json(run_id, "params.json", default={}) or {}
    windows = params.get("windows", [])
    query = params.get("query", {})
    max_videos = int(params.get("max_videos") or 0)
    state = read_run_json(run_id, "state.json", default=None) or {"window": 0, "cursor": 0, "search_id": None}
    _, seen = _tt_load_videos(run_id)
    token_mgr = _TikTokToken(client_key, client_secret)
    t0 = time.time()
    start_window = state.get("window", 0)

    # Liveness signal, in its own file (never in meta.json, so it cannot race
    # with status updates). Streamlit re-executes app.py on every interaction,
    # which empties module-level dicts such as EXTRACTION_JOBS — so "is this
    # run still alive?" must be answered from disk, not from memory.
    heartbeat_stop = threading.Event()

    def _beat():
        while not heartbeat_stop.wait(TIKTOK_HEARTBEAT_SECONDS):
            _tiktok_heartbeat(run_id)
    _tiktok_heartbeat(run_id)
    threading.Thread(target=_beat, daemon=True).start()

    def cancelled():
        if cancel_event.is_set():
            return True
        m = read_run_meta(run_id) or {}
        return bool(m.get("cancel_requested"))

    def progress(**fields):
        with EXTRACTION_JOBS_LOCK:
            job = EXTRACTION_JOBS.get(run_id)
            if job is not None:
                job.update(fields)
        update_run(run_id, **fields)

    final_status, error_message = "completed", ""
    try:
        token_mgr.get()
        wi = state.get("window", 0)
        cursor, search_id = state.get("cursor", 0), state.get("search_id")
        resumed_mid_window = bool(cursor)
        stop_all = False
        while wi < len(windows) and not stop_all:
            w_start, w_end = windows[wi]
            window_new = 0
            while True:
                if cancelled():
                    final_status, stop_all = "cancelled", True
                    break
                if max_videos and len(seen) >= max_videos:
                    _tiktok_log(run_id, f"Reached the maximum of {max_videos:,} videos; stopping.")
                    stop_all = True
                    break
                progress(current=f"Window {wi + 1}/{len(windows)} ({w_start}–{w_end}) · {len(seen):,} videos so far")
                try:
                    data = tiktok_query_page(token_mgr, query, w_start, w_end, cursor, search_id, cancelled=cancelled)
                except TikTokQuotaExceeded:
                    raise
                except TikTokAPIError as e:
                    if str(e) == "Cancelled":
                        final_status, stop_all = "cancelled", True
                        break
                    if resumed_mid_window and cursor:
                        # search_id from a previous session may have expired:
                        # restart this window from the beginning (duplicates are skipped).
                        _tiktok_log(run_id, f"Window {w_start}–{w_end}: could not continue the earlier pagination ({e}); restarting this window.")
                        cursor, search_id, resumed_mid_window = 0, None, False
                        continue
                    if getattr(e, "status", None) == 401:
                        raise
                    _tiktok_log(run_id, f"Window {w_start}–{w_end}: stopped at cursor {cursor} — {e}. "
                                        f"Videos collected so far in this window are kept.")
                    progress(errors=int((read_run_meta(run_id) or {}).get("errors", 0)) + 1)
                    break
                resumed_mid_window = False
                videos = data.get("videos") or []
                new = []
                for v in videos:
                    vid = str(v.get("id", ""))
                    if vid and vid not in seen:
                        seen.add(vid)
                        new.append(v)
                        if max_videos and len(seen) >= max_videos:
                            break
                if new:
                    append_run_file(run_id, "videos.jsonl",
                                    ("\n".join(json.dumps(v, ensure_ascii=False) for v in new) + "\n").encode("utf-8"))
                window_new += len(new)
                cursor, search_id = data.get("cursor", 0), data.get("search_id")
                write_run_json(run_id, "state.json", {"window": wi, "cursor": cursor, "search_id": search_id})
                progress(successful=len(seen))
                if not data.get("has_more"):
                    break
                time.sleep(TIKTOK_PAGE_DELAY)
            if final_status == "cancelled":
                break
            _tiktok_log(run_id, f"Window {w_start}–{w_end}: {window_new:,} new videos.")
            wi += 1
            cursor, search_id = 0, None
            write_run_json(run_id, "state.json", {"window": wi, "cursor": 0, "search_id": None})
            done_now = wi - start_window
            eta = (time.time() - t0) / done_now * (len(windows) - wi) if done_now > 0 else None
            progress(processed=wi, eta_seconds=eta)
    except TikTokQuotaExceeded as e:
        final_status, error_message = "paused", str(e)
        _tiktok_log(run_id, f"Paused: {e}")
    except TikTokAPIError as e:
        # Credentials rejected (token request refused, or a 401 that a fresh
        # token did not fix): keep the run resumable instead of ending it.
        if getattr(e, "status", None) == 401 or str(e).startswith(("Could not obtain an access token", "Could not reach TikTok", "Authentication failed")):
            final_status = "paused"
            error_message = f"Paused — credentials rejected ({e}). Check your client key and secret, then resume."
        else:
            final_status, error_message = "error", str(e)
        _tiktok_log(run_id, error_message)
    except Exception as e:  # never leave a run stuck in "running"
        final_status, error_message = "error", f"{type(e).__name__}: {e}"
        _tiktok_log(run_id, f"Unexpected error: {error_message}")
    finally:
        heartbeat_stop.set()
        try:
            if final_status == "completed":
                _tiktok_log(run_id, "Extraction completed.")
            elif final_status == "cancelled":
                _tiktok_log(run_id, "Extraction cancelled by the user.")
            progress(current="Building the corpus…")
            try:
                stats, n_videos = tiktok_finalize_outputs(run_id, status_label=final_status)
                extra = dict(successful=stats["saved"], errors=stats["failed"],
                             duplicate_count=stats["duplicates"], videos_fetched=n_videos,
                             current=f"{n_videos:,} videos fetched")
            except Exception as e:
                extra = {}
                final_status = "error"
                error_message = (error_message + " · " if error_message else "") + f"Corpus building failed: {type(e).__name__}: {e}"
            # Status flips last, only once every output file exists.
            update_run(run_id, status=final_status, error_message=error_message,
                       cancel_requested=False, eta_seconds=None, **extra)
        finally:
            with EXTRACTION_JOBS_LOCK:
                job = EXTRACTION_JOBS.get(run_id)
                if job is not None:
                    job["status"] = final_status


TIKTOK_HEARTBEAT_SECONDS = 10
TIKTOK_STALE_AFTER_SECONDS = 90


def _tiktok_heartbeat(run_id):
    write_run_file(run_id, "heartbeat", str(time.time()).encode("ascii"))


def _tiktok_seconds_since_heartbeat(run_id):
    raw = read_run_file(run_id, "heartbeat")
    try:
        return time.time() - float(raw.decode("ascii"))
    except Exception:
        return None


def tiktok_start_worker(run_id, client_key, client_secret):
    _tiktok_heartbeat(run_id)
    cancel_event = threading.Event()
    with EXTRACTION_JOBS_LOCK:
        EXTRACTION_JOBS[run_id] = {"status": "running", "cancel_event": cancel_event,
                                   "processed": (read_run_meta(run_id) or {}).get("processed", 0),
                                   "total": (read_run_meta(run_id) or {}).get("total", 0), "current": "Starting…"}
    update_run(run_id, status="running", cancel_requested=False, error_message="")
    t = threading.Thread(target=_tiktok_worker, args=(run_id, client_key, client_secret, cancel_event), daemon=True)
    t.start()


def tiktok_mark_orphans():
    """A TikTok run marked active whose worker has stopped beating (e.g. the
    container was restarted) becomes 'paused' so it can be resumed."""
    for m in list_runs(pipeline="tiktok"):
        if m.get("status") in _ACTIVE_RUN_STATUSES:
            age = _tiktok_seconds_since_heartbeat(m.get("run_id"))
            if age is None or age > TIKTOK_STALE_AFTER_SECONDS:
                update_run(m["run_id"], status="paused",
                           error_message="Interrupted (the app was restarted). Resume to continue.")


def run_tiktok_app():
    from datetime import date, timedelta

    st.markdown(ACADEMIC_PAGE_CSS, unsafe_allow_html=True)
    st.markdown("""
    <style>
    /* Multiselect chips (metadata variables): dark chip, white text. */
    [data-baseweb="tag"] { background-color:#111111 !important; border-color:#111111 !important; }
    [data-baseweb="tag"] *, [data-baseweb="tag"] span { color:#ffffff !important; -webkit-text-fill-color:#ffffff !important; fill:#ffffff !important; }
    @keyframes iramuteq-live-pulse { 0% { opacity:1; } 50% { opacity:.35; } 100% { opacity:1; } }
    .iramuteq-live-badge { display:inline-block; margin:.25rem 0 .25rem; padding:.2rem .6rem; border-radius:999px;
        background:#dc2626; color:#fff !important; font-size:.72rem; font-weight:700; letter-spacing:.04em;
        animation:iramuteq-live-pulse 1.4s ease-in-out infinite; }
    .query-reading { border:1px solid #d9dee8; border-radius:10px; background:#ffffff; padding:.75rem 1rem; color:#111111; margin:.5rem 0 1rem; }

    /* Input fields of the main area: white background, black text, visible border
       (whatever the browser's light/dark preference). The sidebar keeps its dark style. */
    section.main [data-baseweb="input"], section.main [data-baseweb="base-input"],
    section.main [data-baseweb="textarea"], section.main [data-baseweb="input"] > div,
    [data-testid="stMain"] [data-baseweb="input"], [data-testid="stMain"] [data-baseweb="base-input"],
    [data-testid="stMain"] [data-baseweb="textarea"], [data-testid="stMain"] [data-baseweb="input"] > div,
    [data-testid="stMain"] [data-testid="stMultiSelect"] [data-baseweb="select"] > div,
    section.main [data-testid="stMultiSelect"] [data-baseweb="select"] > div {
        background-color:#ffffff !important;
        border-color:#9aa3b2 !important;
    }
    [data-testid="stMain"] input, [data-testid="stMain"] textarea,
    section.main input, section.main textarea {
        background-color:#ffffff !important;
        color:#111111 !important;
        -webkit-text-fill-color:#111111 !important;
        caret-color:#111111 !important;
    }
    [data-testid="stMain"] input::placeholder, [data-testid="stMain"] textarea::placeholder,
    section.main input::placeholder, section.main textarea::placeholder {
        color:#8a919c !important;
        -webkit-text-fill-color:#8a919c !important;
    }
    /* Date picker calendar (rendered in a popover outside the main area):
       white background, black text; selected days dark with white text. */
    [data-baseweb="popover"] [data-baseweb="calendar"],
    [data-baseweb="popover"] [data-baseweb="calendar"] > div,
    [data-baseweb="popover"] [data-baseweb="calendar"] [role="grid"],
    [data-baseweb="popover"] [data-baseweb="calendar"] [role="row"],
    [data-baseweb="popover"] [data-baseweb="calendar"] [role="gridcell"],
    [data-baseweb="popover"] [data-baseweb="calendar"] button {
        background-color:#ffffff !important;
        color:#111111 !important;
        -webkit-text-fill-color:#111111 !important;
    }
    [data-baseweb="popover"] [data-baseweb="calendar"] *,
    [data-baseweb="popover"] [data-baseweb="calendar"] svg {
        color:#111111 !important;
        -webkit-text-fill-color:#111111 !important;
        fill:#111111 !important;
    }
    [data-baseweb="popover"] [data-baseweb="calendar"] [aria-selected="true"],
    [data-baseweb="popover"] [data-baseweb="calendar"] [aria-selected="true"] *,
    [data-baseweb="popover"] [data-baseweb="calendar"] [aria-selected="true"]::after {
        background-color:#111111 !important;
        color:#ffffff !important;
        -webkit-text-fill-color:#ffffff !important;
        border-color:#111111 !important;
    }
    [data-baseweb="popover"] [data-baseweb="calendar"] [role="gridcell"]:hover,
    [data-baseweb="popover"] [data-baseweb="calendar"] [role="gridcell"]:hover * {
        background-color:#e5e7eb !important;
    }
    /* Month / year dropdowns inside the calendar */
    [data-baseweb="popover"] [data-baseweb="calendar"] [data-baseweb="select"] > div {
        background-color:#ffffff !important; border-color:#9aa3b2 !important;
    }
    /* Drop-down lists on this page (calendar month/year, variables selector):
       white, like the fields they belong to. */
    [data-baseweb="popover"] ul[role="listbox"],
    [data-baseweb="popover"] [role="option"],
    [data-baseweb="popover"] [role="option"] * {
        background-color:#ffffff !important; color:#111111 !important; -webkit-text-fill-color:#111111 !important;
    }
    [data-baseweb="popover"] [role="option"]:hover,
    [data-baseweb="popover"] [role="option"][aria-selected="true"] {
        background-color:#e5e7eb !important;
    }

    /* +/- of number fields and the eye of password fields */
    [data-testid="stMain"] [data-testid="stNumberInput"] button,
    [data-testid="stMain"] [data-baseweb="input"] button {
        background-color:#f2f4f7 !important;
        color:#111111 !important;
    }
    [data-testid="stMain"] [data-testid="stNumberInput"] button *,
    [data-testid="stMain"] [data-baseweb="input"] button * {
        color:#111111 !important; fill:#111111 !important;
    }
    </style>
    """, unsafe_allow_html=True)

    def go_to_manager():
        st.session_state["toolkit_input_source"] = "Extractions manager"
        st.session_state["scroll_to_top_once"] = True
        st.rerun()

    st.markdown('<div class="research-kicker">Open research utility · corpus preparation</div>', unsafe_allow_html=True)
    st.markdown('<h1 class="research-title">TikTok Research API → IRaMuTeQ</h1>', unsafe_allow_html=True)
    st.markdown(
        '<div class="research-subtitle">Query TikTok videos with your own Research API credentials and turn '
        'their descriptions and/or speech transcripts into an IRaMuTeQ corpus, with date, region, text-type '
        'and engagement metadata.</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="citation-box"><strong>Requirements.</strong> An approved TikTok Research API project. '
        'Your client key and secret are kept only in this browser session and in memory while the extraction runs — '
        'they are never saved to disk or written to any log. Data obtained through the Research API remains subject to '
        "TikTok's Research API terms (use within your approved project, retention and refresh rules).</div>",
        unsafe_allow_html=True)

    steps = [
        ("01", "Authenticate", "Enter the client key and secret of your approved research project."),
        ("02", "Query", "Keywords, hashtags (AND / OR), accounts, regions, dates."),
        ("03", "Collect", "30-day windows, paginated; pauses on quota and resumes later."),
        ("04", "Export", "Corpus, raw CSV, failure log and a summary for your methods section."),
    ]
    for col, (num, heading, description) in zip(st.columns(4), steps):
        with col:
            st.markdown(f'<div class="method-card"><div class="method-number">{num}</div>'
                        f'<div class="method-heading">{heading}</div>'
                        f'<div class="method-text">{description}</div></div>', unsafe_allow_html=True)

    with st.sidebar:
        with st.expander("About", expanded=False):
            st.markdown(
                "**TikTok Research API → IRaMuTeQ**\n\n"
                "Collects public TikTok videos through the official Research API "
                "(`/v2/research/video/query/`) with the user's own credentials, and builds an "
                "IRaMuTeQ corpus from their descriptions and/or transcripts.")
        with st.expander("Parameters", expanded=False):
            st.markdown(
                f"- Date windows: ≤ {TIKTOK_WINDOW_DAYS} days\n"
                f"- Page size: {TIKTOK_PAGE_SIZE} videos\n"
                f"- Delay between pages: {TIKTOK_PAGE_DELAY:g} s\n"
                f"- Fields: {', '.join(TIKTOK_FIELDS)}")
        with st.expander("low / medium / high", expanded=False):
            st.markdown(TIKTOK_ENGAGEMENT_EXPLANATION)

    tiktok_mark_orphans()

    # ---------- an extraction is running: lock the page (as MediaCloud does) ----------
    # Only this banner is shown while a TikTok run is active; it polls every
    # 2 s and, once the run ends, reruns the whole app so the form reappears.
    if any(m.get("status") in _ACTIVE_RUN_STATUSES for m in list_runs(pipeline="tiktok")):
        @st.fragment(run_every="2s")
        def _active_run_banner():
            tiktok_mark_orphans()
            live = [m for m in list_runs(pipeline="tiktok") if m.get("status") in _ACTIVE_RUN_STATUSES]
            if not live:
                st.rerun(scope="app")
                return
            run = live[0]
            st.markdown('<div class="iramuteq-live-badge">● Extraction running</div>', unsafe_allow_html=True)
            st.markdown("### 🔴 TikTok extraction still running")
            total = max(int(run.get("total") or 0), 1)
            done = min(int(run.get("processed") or 0), total)
            eta = run.get("eta_seconds")
            eta_text = f" · ~{format_eta(eta)} remaining" if eta is not None else ""
            st.progress(done / total, text=f"Date windows {done:,} / {total:,}{eta_text} · {run.get('current') or 'Processing…'}")
            st.info("Your extraction did not disappear. It is running in the background and its progress is stored "
                    "persistently. Open **Extractions manager** to follow it, cancel it, or download the result when it "
                    "finishes. A new TikTok extraction can be launched once this one has ended.")
            if st.button("📂 Open Extractions manager", key="open_recent_from_active_tiktok", use_container_width=True):
                st.session_state["toolkit_input_source"] = "Extractions manager"
                st.session_state["scroll_to_top_once"] = True
                st.rerun(scope="app")
        _active_run_banner()
        return

    # ---------- 1. credentials ----------
    st.markdown('<div class="section-title">1 · Your Research API credentials</div>', unsafe_allow_html=True)
    st.markdown('<div class="section-note">On <a href="https://developers.tiktok.com/research/" target="_blank">developers.tiktok.com/research</a> → your project → Client key / Client secret (click the eye icon to display them).</div>', unsafe_allow_html=True)
    c1, c2 = st.columns(2)
    with c1:
        client_key = st.text_input("Client key", type="password", key="tt_client_key").strip()
    with c2:
        client_secret = st.text_input("Client secret", type="password", key="tt_client_secret").strip()
    if st.button("Test credentials", disabled=not (client_key and client_secret)):
        try:
            tiktok_get_token(client_key, client_secret)
            st.success("Credentials accepted — an access token was obtained.")
        except TikTokAPIError as e:
            st.error(str(e))

    # ---------- paused runs ----------
    paused = [m for m in list_runs(pipeline="tiktok") if m.get("status") == "paused"]
    if paused:
        st.markdown('<div class="section-title">Paused extractions</div>', unsafe_allow_html=True)
        st.markdown('<div class="section-note">These stopped early (daily quota reached or app restarted). Enter your credentials above, then resume: collection continues from the last saved page.</div>', unsafe_allow_html=True)
        for m in paused:
            rid = m["run_id"]
            with st.container(border=True):
                st.markdown(f"**{m.get('label', rid)}** · {int(m.get('processed') or 0)} / {int(m.get('total') or 0)} date windows · "
                            f"{int(m.get('videos_fetched') or 0):,} videos so far")
                if m.get("error_message"):
                    st.caption(m["error_message"])
                if st.button("▶ Resume", key=f"tt_resume_{rid}", disabled=not (client_key and client_secret)):
                    try:
                        tiktok_get_token(client_key, client_secret)
                    except TikTokAPIError as e:
                        # The run stays paused: nothing is lost, the user can retry.
                        st.error(f"Not resumed — {e} Check your client key and secret, then try again.")
                    else:
                        tiktok_start_worker(rid, client_key, client_secret)
                        go_to_manager()

    # ---------- 2. query ----------
    st.markdown('<div class="section-title">2 · What to collect</div>', unsafe_allow_html=True)
    st.markdown('<div class="section-note">Enter one term per line. TikTok matches keywords approximately (in descriptions and hashtags), so check the results.</div>', unsafe_allow_html=True)
    mode_labels = {"any": "ANY of them (OR)", "all": "ALL of them (AND)"}
    q1, q2 = st.columns(2)
    with q1:
        kw_text = st.text_area("Keywords", key="tt_keywords", height=110, placeholder="intelligence artificielle\nChatGPT")
        keyword_mode = st.radio("Videos must contain…", ["any", "all"], format_func=mode_labels.get,
                                horizontal=True, key="tt_kw_mode")
    with q2:
        tags_text = st.text_area("Hashtags (without #)", key="tt_hashtags", height=110, placeholder="ia\nchatgpt")
        hashtag_mode = st.radio("Videos must carry…", ["any", "all"], format_func=mode_labels.get,
                                horizontal=True, key="tt_ht_mode")
    combine = st.radio("Combine the keyword group and the hashtag group with",
                       ["or", "and"], format_func={"or": "OR — either group is enough", "and": "AND — both groups must match"}.get,
                       horizontal=True, key="tt_combine")
    q3, q4 = st.columns(2)
    with q3:
        users_text = st.text_area("Restrict to accounts (usernames — optional, any of them)", key="tt_users", height=80)
    with q4:
        excl_text = st.text_area("Exclude keywords (optional, none of them)", key="tt_exclude", height=80)
    regions_text = st.text_input("Restrict to region codes (comma-separated ISO codes, e.g. FR, GR, BE — optional, any of them)", key="tt_regions")
    d1, d2, d3 = st.columns(3)
    today = date.today()
    with d1:
        start_date = st.date_input("From date", value=today - timedelta(days=30), min_value=date(2018, 1, 1), max_value=today, key="tt_start")
    with d2:
        end_date = st.date_input("To date (inclusive)", value=today - timedelta(days=1), min_value=date(2018, 1, 1), max_value=today, key="tt_end")
    with d3:
        max_videos = st.number_input("Maximum number of videos (0 = no limit)", min_value=0, value=10000, step=1000, key="tt_max",
                                     help="Protects your daily quota (about 1,000 requests of up to 100 videos each — check your project's current limits).")

    keywords, hashtags = _split_terms(kw_text), _split_terms(tags_text)
    usernames, excluded = _split_terms(users_text), _split_terms(excl_text)
    regions = [r.upper() for r in _split_terms(regions_text)]
    query, query_problem = tiktok_build_query(keywords, hashtags, usernames, regions, excluded,
                                              keyword_mode, hashtag_mode, combine)
    reading = tiktok_describe_query(keywords, hashtags, usernames, regions, excluded, keyword_mode, hashtag_mode, combine)

    with st.expander("Advanced: edit the raw API query (JSON)"):
        st.caption("Leave empty to use the query built from the fields above. TikTok's syntax: every `and` "
                   "condition must hold, at least one `or` condition must hold, no `not` condition may hold.")
        raw_query = st.text_area("Raw query", key="tt_raw_query", height=160, placeholder=json.dumps(query, ensure_ascii=False, indent=2))
        if raw_query.strip():
            try:
                query, query_problem, reading = json.loads(raw_query), None, "Custom JSON query (see below)."
            except ValueError as e:
                st.error(f"Invalid JSON: {e}")
                query = {}
        st.json(query)
    if query_problem:
        st.error(query_problem)
    elif reading:
        st.markdown(f'<div class="query-reading"><strong>Your query reads:</strong> {reading}.</div>', unsafe_allow_html=True)

    # ---------- 3. corpus ----------
    st.markdown('<div class="section-title">3 · Corpus options</div>', unsafe_allow_html=True)
    text_source = st.radio(
        "Text of each corpus entry",
        options=["description", "transcript", "both"],
        format_func={"description": "Video description (caption)",
                     "transcript": "Speech transcript (voice_to_text, when TikTok provides one)",
                     "both": "Description + transcript"}.get,
        horizontal=True, key="tt_text_source")
    metadata = st.multiselect("IRaMuTeQ metadata variables", options=list(TIKTOK_METADATA_CHOICES),
                              default=TIKTOK_DEFAULT_METADATA, format_func=TIKTOK_METADATA_CHOICES.get, key="tt_meta")
    if "texttype" in metadata:
        st.caption("*texttype* records what each entry actually contains. With “Description + transcript”, "
                   "a video that has only one of the two gets *description* or *transcript*, so you can tell them apart in IRaMuTeQ.")
    if any(m in metadata for m in ("views", "likes", "comments", "shares")):
        st.info(TIKTOK_ENGAGEMENT_EXPLANATION)
    o1, o2 = st.columns(2)
    with o1:
        lowercase = st.checkbox("Convert text to lowercase", value=True, key="tt_lower")
        remove_urls = st.checkbox("Remove URLs", value=True, key="tt_urls")
        strip_tags = st.checkbox("Strip # and @ symbols (keep the word)", value=True, key="tt_tags")
    with o2:
        remove_emojis = st.checkbox("Remove emojis", value=True, key="tt_emojis",
                                    help="Emojis otherwise become separate 'words' in IRaMuTeQ.")
        dedupe = st.checkbox("Skip videos whose text is identical to an earlier one", value=True, key="tt_dedupe")
        min_length = st.number_input("Minimum text length (characters)", min_value=0, value=20, step=10, key="tt_minlen")

    corpus_options = {"text_source": text_source, "metadata": metadata, "lowercase": lowercase,
                      "remove_urls": remove_urls, "strip_tags": strip_tags, "remove_emojis": remove_emojis,
                      "dedupe_text": dedupe, "min_length": int(min_length)}
    example = {"date": "*date_2026-09-14", "year": "*year_2026", "month": "*month_202609", "region": "*region_fr",
               "user": "*user_example", "views": "*views_high", "likes": "*likes_medium",
               "comments": "*comments_low", "shares": "*shares_low", "videoid": "*videoid_7412345678901234567",
               "texttype": "*texttype_" + ("both" if text_source == "both" else text_source)}
    st.markdown("**Header preview:**")
    st.code(" ".join(["****"] + [example[m] for m in metadata]), language=None)

    # ---------- 4. launch ----------
    st.markdown('<div class="section-title">4 · Run</div>', unsafe_allow_html=True)
    problems = []
    if not (client_key and client_secret):
        problems.append("Enter your client key and secret.")
    if query_problem:
        problems.append("Fix the query logic above.")
    elif not query:
        problems.append("Give at least one keyword, hashtag, account or region.")
    if start_date > end_date:
        problems.append("The start date is after the end date.")
    windows = tiktok_date_windows(start_date, end_date) if start_date <= end_date else []
    if windows:
        st.caption(f"{len(windows)} date window(s) of up to {TIKTOK_WINDOW_DAYS} days will be queried, each paginated by {TIKTOK_PAGE_SIZE} videos.")
    label = st.text_input("Name for this extraction", value=f"TikTok · {', '.join((keywords + hashtags + usernames)[:3]) or 'query'} · "
                                                            f"{start_date:%Y-%m-%d}→{end_date:%Y-%m-%d}", key="tt_label")
    for p in problems:
        st.warning(p)
    if st.button("Launch extraction", type="primary", disabled=bool(problems), key="tt_launch"):
        try:
            tiktok_get_token(client_key, client_secret)
        except TikTokAPIError as e:
            st.error(str(e))
            return
        run_id = create_run("tiktok", label, total=len(windows), extra={"status": "starting", "videos_fetched": 0})
        write_run_json(run_id, "params.json", {
            "query": query, "query_reading": reading, "start_date": start_date.strftime("%Y%m%d"),
            "end_date": end_date.strftime("%Y%m%d"), "windows": windows, "max_videos": int(max_videos),
            "corpus_options": corpus_options,
        })
        write_run_json(run_id, "state.json", {"window": 0, "cursor": 0, "search_id": None})
        _tiktok_log(run_id, f"Extraction started: {len(windows)} window(s), query {json.dumps(query, ensure_ascii=False)}")
        tiktok_start_worker(run_id, client_key, client_secret)
        go_to_manager()

    st.markdown('<div class="footer-line">TikTok Research API → IRaMuTeQ · corpus preparation interface</div>', unsafe_allow_html=True)


# ============================================================
# ENTRY POINT: a single landing page where the input source is
# chosen once, before proceeding into the matching pipeline.
# ============================================================
INPUT_SOURCES = {
    "Meta Content Library": {
        "label": "Meta Content Library",
        "description": "Upload a Meta Content Library CSV export — Facebook or Instagram. Flexible column mapping, Meta-specific text cleaning.",
    },
    "Any CSV": {
        "label": "Any CSV",
        "description": "Upload any CSV file. You choose one text column and at least one metadata column yourself.",
    },
    "MediaCloud": {
        "label": "MediaCloud",
        "description": "Upload a MediaCloud CSV export. Article text is fetched from each URL and cleaned automatically, with National/Regional press classification and publication statistics.",
    },
    "TikTok Research API": {
        "label": "TikTok",
        "description": "Use your own TikTok Research API credentials to search videos by keyword, hashtag, account, region and date, and build a corpus from their descriptions and/or transcripts.",
    },
}

_PIPELINE_LABELS = {"crowdtangle": "Meta Content Library", "csv": "Any CSV", "mediacloud": "MediaCloud", "tiktok": "TikTok"}
_BACKGROUND_PIPELINES = ("mediacloud", "tiktok")
_ACTIVE_RUN_STATUSES = {"starting", "running", "cancelling"}


def render_recent_extractions():
    # Right after launching a new extraction, the user is redirected here —
    # but Streamlit keeps the browser's previous scroll offset across
    # reruns, so without this they'd land wherever they happened to be
    # scrolled to on the *previous* page (often the bottom, near the
    # "Launch extraction" button) instead of the top of this one.
    _scroll_to_top_pending = st.session_state.pop("scroll_to_top_once", False)
    st.markdown('<h2 style="font-family:Georgia,\'Times New Roman\',serif;">Extractions manager</h2>', unsafe_allow_html=True)
    st.caption("Every corpus you've built (Meta Content Library, Any CSV, MediaCloud, or TikTok) is kept here — even after closing the tab — until you delete it.")

    @st.fragment(run_every="1s")
    def _recent_content():
        with EXTRACTION_JOBS_LOCK:
            _active_job_ids = [jid for jid, j in EXTRACTION_JOBS.items() if j.get("status") in _ACTIVE_RUN_STATUSES]
        _active_persistent_runs = [m for m in list_runs() if m.get("pipeline") in _BACKGROUND_PIPELINES and m.get("status") in _ACTIVE_RUN_STATUSES]
        _live_run_id = _active_persistent_runs[0].get("run_id") if _active_persistent_runs else (_active_job_ids[0] if _active_job_ids else None)

        if _live_run_id:
            with EXTRACTION_JOBS_LOCK:
                job = dict(EXTRACTION_JOBS.get(_live_run_id, {}))
            meta_live = read_run_meta(_live_run_id) or {}
            status = job.get("status") or meta_live.get("status", "unknown")
            total = max(int(job.get("total") or meta_live.get("total") or 0), 1)
            done = min(int(job.get("processed") or meta_live.get("processed") or 0), total)
            current = job.get("current") or meta_live.get("current") or ""

            st.markdown("### 🔴 Extraction in progress")
            eta_seconds = job.get("eta_seconds")
            if eta_seconds is None:
                eta_seconds = meta_live.get("eta_seconds")
            eta_text = f" · ~{format_eta(eta_seconds)} remaining" if eta_seconds is not None else " · estimating remaining time…"
            st.progress(done / total, text=f"Processed {done:,} / {total:,}{eta_text} · {current}")
            st.caption("You can close this tab, or even quit your browser entirely, and come back later — the extraction keeps running in the background. Just don't stop or quit the Docker container (or Docker Desktop), since that's what's actually doing the work.")

            if status == "cancelling":
                st.info("Cancelling… the current request will finish, then processing will stop.")
            else:
                # Always show Cancel for an active MediaCloud run. The request is
                # persisted, so the background worker can observe it even when the
                # current browser session cannot access its in-memory Event.
                confirm_key = f"mc_recent_confirm_cancel_{_live_run_id}"
                if not st.session_state.get(confirm_key):
                    if st.button("⏹ Cancel extraction", type="primary", use_container_width=False, key=f"recent_cancel_{_live_run_id}"):
                        st.session_state[confirm_key] = True
                        st.rerun(scope="fragment")
                else:
                    st.warning("Cancel this extraction? Progress made so far will still be saved and downloadable, but the rest won't be processed.")
                    rc1, rc2 = st.columns(2)
                    with rc1:
                        if st.button("Yes, cancel", type="primary", use_container_width=True, key=f"recent_cancel_confirm_{_live_run_id}"):
                            with EXTRACTION_JOBS_LOCK:
                                live_job = EXTRACTION_JOBS.get(_live_run_id)
                                if live_job and live_job.get("cancel_event") is not None:
                                    live_job["cancel_event"].set()
                                    live_job["status"] = "cancelling"
                            update_run(_live_run_id, status="cancelling", cancel_requested=True)
                            st.session_state.pop(confirm_key, None)
                            st.rerun(scope="fragment")
                    with rc2:
                        if st.button("No, keep going", use_container_width=True, key=f"recent_cancel_back_{_live_run_id}"):
                            st.session_state.pop(confirm_key, None)
                            st.rerun(scope="fragment")
            st.markdown("---")

        runs = list_runs()
        if not runs:
            st.info("No extractions yet. Build a corpus from any of the input sources and it will show up here.")
            return

        for meta in runs:
            run_id = meta.get("run_id", "")
            pipeline = _PIPELINE_LABELS.get(meta.get("pipeline"), meta.get("pipeline", "?"))
            status = meta.get("status", "unknown")
            started_at = meta.get("started_at", "")[:19].replace("T", " ")
            label = meta.get("label", "")
            status_badge = {
                "completed": "✅ Completed", "cancelled": "⏹ Cancelled", "error": "❌ Error",
                "starting": "🔴 Running", "running": "🔴 Running", "cancelling": "🟠 Cancelling…",
                "paused": "⏸ Paused — resume it from the TikTok page",
            }.get(status, status)

            with st.container(border=True):
                top = st.columns([3, 1])
                with top[0]:
                    name_key = f"rename_extraction_{run_id}"
                    edit_key = f"editing_extraction_name_{run_id}"
                    if st.session_state.get(edit_key):
                        edit_cols = st.columns([6, 1])
                        with edit_cols[0]:
                            edited_label = st.text_input(
                                "Extraction name",
                                value=label,
                                key=name_key,
                                label_visibility="collapsed",
                            )
                        with edit_cols[1]:
                            if st.button("✓", key=f"save_name_{run_id}", help="Save name"):
                                if rename_run(run_id, edited_label):
                                    st.session_state.pop(edit_key, None)
                                    st.session_state.pop(name_key, None)
                                    st.rerun(scope="fragment")
                    else:
                        name_cols = st.columns([1, 0.08])
                        with name_cols[0]:
                            st.markdown(f"**{pipeline}** — {label}")
                        with name_cols[1]:
                            if st.button("✏️", key=f"edit_name_{run_id}", help="Rename extraction"):
                                st.session_state[edit_key] = True
                                st.rerun(scope="fragment")
                    _ver = f" · v{meta['app_version']}" if meta.get("app_version") else ""
                    st.caption(f"{started_at} · {status_badge}{_ver}")
                with top[1]:
                    total = max(meta.get("total", 0), 0)
                    if total:
                        st.caption(f"{meta.get('processed', 0):,} / {total:,} processed")

                if status in _ACTIVE_RUN_STATUSES:
                    if run_id == _live_run_id:
                        st.caption("Live progress and Cancel are shown at the top of this page ↑")
                    else:
                        st.caption("Not active in this app instance (e.g. after a restart) — nothing to cancel.")
                else:
                    m = st.columns(5)
                    m[0].metric("Saved", f"{meta.get('successful', 0):,}")
                    m[1].metric("Failed", f"{meta.get('errors', 0):,}")
                    m[2].metric("Duplicates", f"{meta.get('duplicate_count', 0):,}")
                    if meta.get("pipeline") == "mediacloud":
                        m[3].metric("National press", f"{meta.get('national_count', 0):,}")
                        m[4].metric("Regional press", f"{meta.get('regional_count', 0):,}")
                    if meta.get("pipeline") == "tiktok":
                        m[3].metric("Videos fetched", f"{meta.get('videos_fetched', 0):,}")
                        if meta.get("error_message"):
                            st.caption(meta["error_message"])

                    corpus_bytes = read_run_file(run_id, "corpus.txt")
                    failed_bytes = read_run_file(run_id, "failed.txt")
                    n_cols = {"mediacloud": 6, "tiktok": 4}.get(meta.get("pipeline"), 2)
                    dl = st.columns(n_cols)
                    with dl[0]:
                        if corpus_bytes:
                            st.download_button("Corpus", data=corpus_bytes, file_name=f"{run_id}_corpus.txt", mime="text/plain", use_container_width=True, key=f"dl_corpus_{run_id}")
                    with dl[1]:
                        if failed_bytes:
                            st.download_button("Failure log", data=failed_bytes, file_name=f"{run_id}_failed.txt", mime="text/plain", use_container_width=True, key=f"dl_failed_{run_id}")
                    if meta.get("pipeline") == "mediacloud":
                        initial_stats_bytes = read_run_file(run_id, "initial_stats.csv")
                        saved_stats_bytes = read_run_file(run_id, "saved_stats.csv")
                        coverage_html_bytes = read_run_file(run_id, "coverage_chart.html")
                        summary_log_bytes = read_run_file(run_id, "summary.txt")
                        with dl[2]:
                            if initial_stats_bytes:
                                st.download_button("Initial stats", data=initial_stats_bytes, file_name=f"{run_id}_initial_stats.csv", mime="text/csv", use_container_width=True, key=f"dl_ist_{run_id}")
                        with dl[3]:
                            if saved_stats_bytes:
                                st.download_button("Saved stats", data=saved_stats_bytes, file_name=f"{run_id}_saved_stats.csv", mime="text/csv", use_container_width=True, key=f"dl_sst_{run_id}")
                        with dl[4]:
                            if coverage_html_bytes:
                                st.download_button("Coverage graph", data=coverage_html_bytes, file_name=f"{run_id}_coverage_chart.html", mime="text/html", use_container_width=True, key=f"dl_cov_{run_id}")
                        with dl[5]:
                            if summary_log_bytes:
                                st.download_button("Summary log", data=summary_log_bytes, file_name=f"{run_id}_summary.txt", mime="text/plain", use_container_width=True, key=f"dl_summary_{run_id}")

                    if meta.get("pipeline") == "tiktok":
                        videos_csv_bytes = read_run_file(run_id, "videos.csv")
                        tt_summary_bytes = read_run_file(run_id, "summary.txt")
                        with dl[2]:
                            if videos_csv_bytes:
                                st.download_button("Raw data (CSV)", data=videos_csv_bytes, file_name=f"{run_id}_tiktok_videos.csv", mime="text/csv", use_container_width=True, key=f"dl_ttcsv_{run_id}")
                        with dl[3]:
                            if tt_summary_bytes:
                                st.download_button("Summary log", data=tt_summary_bytes, file_name=f"{run_id}_summary.txt", mime="text/plain", use_container_width=True, key=f"dl_ttsum_{run_id}")

                    confirm_key = f"confirm_delete_{run_id}"
                    if not st.session_state.get(confirm_key):
                        if st.button("Delete", key=f"delete_{run_id}"):
                            st.session_state[confirm_key] = True
                            st.rerun(scope="fragment")
                    else:
                        st.warning("Delete this extraction permanently? This cannot be undone.")
                        dc1, dc2 = st.columns(2)
                        with dc1:
                            if st.button("Yes, delete", type="primary", key=f"delete_confirm_{run_id}"):
                                delete_run(run_id)
                                st.session_state.pop(confirm_key, None)
                                st.rerun(scope="fragment")
                        with dc2:
                            if st.button("Cancel", key=f"delete_back_{run_id}"):
                                st.session_state.pop(confirm_key, None)
                                st.rerun(scope="fragment")

    _recent_content()

    if _scroll_to_top_pending:
        # Placed after _recent_content() (rather than at the top of the page)
        # so it runs once the full run list has actually been sent to the
        # browser — scrolling too early, before that content streams in,
        # left the page still short and the scroll had nothing to "stick" to.
        # Belt-and-suspenders: try several likely scroll targets (Streamlit's
        # DOM structure/selectors have changed across versions) and repeat
        # for ~1.5s in case more content keeps arriving right after.
        st.components.v1.html(
            """<script>
            (function () {
                function scrollTopmost() {
                    try {
                        var w = window.parent;
                        var d = w.document;
                        w.scrollTo(0, 0);
                        if (d.scrollingElement) d.scrollingElement.scrollTop = 0;
                        if (d.documentElement) d.documentElement.scrollTop = 0;
                        if (d.body) d.body.scrollTop = 0;
                        var sel = 'section.main, [data-testid="stMain"], [data-testid="stAppViewContainer"], main';
                        d.querySelectorAll(sel).forEach(function (el) { el.scrollTop = 0; });
                    } catch (e) { /* cross-origin or not ready yet: ignore, next tick retries */ }
                }
                scrollTopmost();
                var tries = 0;
                var interval = setInterval(function () {
                    scrollTopmost();
                    tries += 1;
                    if (tries > 15) { clearInterval(interval); }
                }, 100);
            })();
            </script>""",
            height=0,
        )

APP_CREDIT = f"Created by Panos Tsimpoukis, LERASS (UT) · PhEPoC-ST (NTUA) · v{APP_VERSION}"
APP_CITATION = (
    f"Tsimpoukis, P. (2026). IRaMuTeQ corpus construction toolkit (Version {APP_VERSION}) [Computer software]. "
    "LERASS, Université de Toulouse · PhEPoC-ST, NTUA. https://github.com/panagiotix/corpusiramuteq"
)


def render_credit_with_copy():
    """Credit line fixed at the bottom-left of the landing page. Clicking the
    text or the small button copies a ready-made citation of the tool.

    Streamlit sanitises scripts in st.markdown, so the element is created by
    a tiny zero-height component that injects it into the page itself (same
    origin). It is hidden on every other page by CREDIT_HIDE_CSS."""
    import streamlit.components.v1 as components
    parent_code = """
(function () {
  if (document.getElementById("app-credit-copy")) { return; }
  var CITATION = %s, CREDIT = %s;
  var box = document.createElement("div");
  box.id = "app-credit-copy";
  box.style.cssText = "position:fixed;bottom:6px;left:10px;z-index:9999;display:flex;align-items:center;gap:.45rem;" +
                      "font-size:.7rem;color:#fff;font-family:'Source Sans Pro',sans-serif;";
  var txt = document.createElement("span");
  txt.textContent = CREDIT;
  txt.title = "Click to copy a citation of this tool";
  txt.style.cssText = "opacity:.75;cursor:pointer;border-bottom:1px dotted rgba(255,255,255,.5);";
  var btn = document.createElement("button");
  btn.textContent = "\ud83d\udccb Copy citation";
  btn.title = "Copy a citation of this tool";
  btn.style.cssText = "background:#fff;color:#111;border:0;border-radius:5px;padding:1px 7px;font-size:.66rem;font-weight:600;cursor:pointer;";
  function done(ok) {
    btn.textContent = ok ? "\u2713 Copied" : "Copy failed";
    setTimeout(function () { btn.textContent = "\ud83d\udccb Copy citation"; }, 1800);
  }
  function fallback() {
    var ta = document.createElement("textarea");
    ta.value = CITATION; ta.style.position = "fixed"; ta.style.opacity = "0";
    document.body.appendChild(ta); ta.select();
    var ok = false; try { ok = document.execCommand("copy"); } catch (e) {}
    document.body.removeChild(ta); return ok;
  }
  function copy() {
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(CITATION).then(function () { done(true); }, function () { done(fallback()); });
    } else { done(fallback()); }
  }
  txt.addEventListener("click", copy);
  btn.addEventListener("click", copy);
  box.appendChild(txt); box.appendChild(btn);
  document.body.appendChild(box);
})();
""" % (json.dumps(APP_CITATION), json.dumps(APP_CREDIT))
    components.html(
        "<script>(function(){try{var d=window.parent.document;var s=d.createElement('script');"
        f"s.textContent={json.dumps(parent_code)};d.body.appendChild(s);"
        "var c=d.getElementById('app-credit-copy');if(c){c.style.display='flex';}}catch(e){}})();</script>",
        height=0,
    )


# The credit and the update notice belong to the landing page only.
CREDIT_HIDE_CSS = "<style>#app-credit-copy, #app-update-notice{display:none !important;}</style>"


# ============================================================
# Update notice (landing page, bottom-right). Asks GitHub for the latest
# release; shown only for a newer FEATURE version (first or second number
# higher, e.g. 1.1.x -> 1.2.0). Fix-only releases (1.1.1) show nothing.
# ============================================================
GITHUB_REPO = "panagiotix/corpusiramuteq"
UPDATE_CHECK_TTL_SECONDS = 6 * 3600   # GitHub allows 60 anonymous requests/hour per IP
UPDATE_GIT_COMMANDS = (
    "git pull\n"
    "docker stop iramuteq-toolkit\n"
    "docker rm iramuteq-toolkit\n"
    "docker build -t iramuteq-toolkit .\n"
    "docker run -d --name iramuteq-toolkit -p 8501:8501 \\\n"
    "  -v ~/iramuteq-data:/app/data \\\n"
    "  --restart unless-stopped iramuteq-toolkit"
)


def parse_version(text):
    """'v1.2.0' / '1.2' -> (1, 2, 0); None if it isn't a version."""
    m = re.match(r"^\s*v?(\d+)(?:\.(\d+))?(?:\.(\d+))?", str(text or ""))
    if not m:
        return None
    return tuple(int(g or 0) for g in m.groups())


def is_newer_feature_version(latest, current=APP_VERSION):
    lv, cv = parse_version(latest), parse_version(current)
    return bool(lv and cv and lv[:2] > cv[:2])


def release_highlights(body, limit=4):
    """First bullet points of a release description, as plain text."""
    items = []
    for line in (body or "").splitlines():
        m = re.match(r"^\s*[-*]\s+(.*\S)", line)
        if not m:
            continue
        text = m.group(1)
        text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)      # [label](url) -> label
        text = re.sub(r"\*\*|__|`", "", text).strip()               # bold / code marks, keep *var_ names
        text = re.split(r"(?<=[.!?])\s", text)[0]                 # first sentence only
        if text:
            items.append(text if len(text) <= 90 else text[:87].rstrip() + "…")
        if len(items) >= limit:
            break
    return items


@st.cache_data(ttl=UPDATE_CHECK_TTL_SECONDS, show_spinner=False)
def fetch_latest_release():
    """Latest GitHub release, or None (offline, no release yet, API error)."""
    import requests
    try:
        r = requests.get(f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
                         headers={"Accept": "application/vnd.github+json"}, timeout=4)
        if r.status_code != 200:
            return None
        data = r.json()
        return {"tag": data.get("tag_name") or "", "name": data.get("name") or "",
                "published_at": data.get("published_at") or "", "body": data.get("body") or "",
                "url": data.get("html_url") or f"https://github.com/{GITHUB_REPO}/releases"}
    except Exception:
        return None


def render_update_notice():
    """Inject the notice into the page (like the credit line) when a newer
    feature version exists. Dismissal is remembered per version in the
    browser (localStorage), so it reappears only for the next one."""
    release = fetch_latest_release()
    if not release or not is_newer_feature_version(release["tag"]):
        return
    import streamlit.components.v1 as components
    latest = ".".join(str(n) for n in parse_version(release["tag"]))
    released = ""
    try:
        d = datetime.strptime(release["published_at"][:10], "%Y-%m-%d")
        released = f"released {d.day} {d.strftime('%B')} {d.year}"
    except Exception:
        pass
    data = {
        "latest": latest, "current": APP_VERSION, "released": released,
        "highlights": release_highlights(release["body"]), "url": release["url"],
        "commands": UPDATE_GIT_COMMANDS, "repo": f"https://github.com/{GITHUB_REPO}",
    }
    parent_code = """
(function () {
  var D = %s;
  var KEY = "iramuteq-dismissed-update";
  var old = document.getElementById("app-update-notice");
  if (old && old.getAttribute("data-version") === D.latest) { old.style.display = ""; return; }
  if (old) { old.remove(); }
  try { if (localStorage.getItem(KEY) === D.latest) { return; } } catch (e) {}

  function el(tag, css, text) {
    var e = document.createElement(tag);
    if (css) { e.style.cssText = css; }
    if (text !== undefined) { e.textContent = text; }
    return e;
  }
  var BTN = "border-radius:6px;padding:5px 10px;font-size:12px;font-weight:600;cursor:pointer;line-height:1.3;";
  var DARK = BTN + "background:#111;color:#fff;-webkit-text-fill-color:#fff;border:1px solid #111;";
  var LIGHT = BTN + "background:#fff;color:#111;-webkit-text-fill-color:#111;border:1px solid #9aa3b2;";
  var CODE = "background:#f2f4f7;border-radius:6px;padding:7px 9px;font-family:ui-monospace,Menlo,Consolas,monospace;" +
             "font-size:11px;color:#111;white-space:pre-wrap;word-break:break-all;margin:4px 0;";

  var box = el("div", "position:fixed;right:16px;bottom:12px;z-index:9999;width:340px;max-height:75vh;overflow-y:auto;" +
               "background:#fff;color:#111;border-radius:10px;padding:12px 14px;box-sizing:border-box;" +
               "font-family:'Source Sans Pro',sans-serif;font-size:13px;line-height:1.45;border:1px solid #d9dee8;");
  box.id = "app-update-notice";
  box.setAttribute("data-version", D.latest);

  var head = el("div", "display:flex;justify-content:space-between;align-items:flex-start;gap:8px;");
  head.appendChild(el("div", "font-size:15px;font-weight:600;color:#111;", "\\ud83d\\udd14 Version " + D.latest + " is available"));
  var close = el("button", "background:transparent;border:0;color:#555;font-size:18px;line-height:1;cursor:pointer;padding:0;", "\\u00d7");
  close.title = "Dismiss — shown again for the next version";
  close.setAttribute("aria-label", "Dismiss");
  head.appendChild(close);
  box.appendChild(head);
  box.appendChild(el("div", "font-size:12px;color:#555;margin:1px 0 6px;", "You have " + D.current + (D.released ? " \\u00b7 " + D.released : "")));

  if (D.highlights.length) {
    box.appendChild(el("div", "font-size:12px;color:#111;", "New:"));
    var ul = el("ul", "margin:2px 0 8px 18px;padding:0;font-size:12px;color:#111;");
    D.highlights.forEach(function (h) { ul.appendChild(el("li", "", h)); });
    box.appendChild(ul);
  }

  var row = el("div", "display:flex;gap:6px;flex-wrap:wrap;");
  var how = el("button", DARK, "How to update");
  var notes = el("button", LIGHT, "Full release notes \\u2197");
  row.appendChild(how); row.appendChild(notes);
  box.appendChild(row);

  var steps = el("div", "display:none;margin-top:10px;font-size:12px;color:#111;");
  steps.appendChild(el("div", "color:#555;margin-bottom:6px;",
    "Wait until no extraction is running. Your past extractions are kept."));
  steps.appendChild(el("div", "font-weight:600;", "If you installed with Git"));
  steps.appendChild(el("div", "", "In a terminal, inside your project folder (e.g. corpusiramuteq), run:"));
  steps.appendChild(el("div", CODE, D.commands));
  var copy = el("button", LIGHT, "Copy commands");
  steps.appendChild(copy);
  steps.appendChild(el("div", "color:#555;margin-top:4px;", "Drop the -v line if you didn't use it originally."));
  steps.appendChild(el("div", "font-weight:600;margin-top:10px;", "If you downloaded the ZIP"));
  var zip = el("div", "");
  zip.appendChild(document.createTextNode("Download the ZIP again from "));
  var a = el("a", "color:#111;text-decoration:underline;", "the GitHub page");
  a.href = D.repo; a.target = "_blank"; a.rel = "noopener";
  zip.appendChild(a);
  zip.appendChild(document.createTextNode(", extract it over (or alongside) the old folder, then run the same commands from inside it, without the first line (git pull)."));
  steps.appendChild(zip);
  steps.appendChild(el("div", "color:#555;margin-top:6px;", "Full guide: DOCKER.md, Part 5."));
  box.appendChild(steps);

  how.addEventListener("click", function () {
    var open = steps.style.display === "none";
    steps.style.display = open ? "block" : "none";
    how.textContent = open ? "Hide steps" : "How to update";
  });
  notes.addEventListener("click", function () { window.open(D.url, "_blank", "noopener"); });
  close.addEventListener("click", function () {
    try { localStorage.setItem(KEY, D.latest); } catch (e) {}
    box.remove();
  });
  function copied(ok) {
    copy.textContent = ok ? "\\u2713 Copied" : "Copy failed";
    setTimeout(function () { copy.textContent = "Copy commands"; }, 1800);
  }
  copy.addEventListener("click", function () {
    var text = D.commands;
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(function () { copied(true); }, function () { copied(false); });
    } else {
      var ta = document.createElement("textarea"); ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
      document.body.appendChild(ta); ta.select();
      var ok = false; try { ok = document.execCommand("copy"); } catch (e) {}
      document.body.removeChild(ta); copied(ok);
    }
  });
  document.body.appendChild(box);
})();
""" % json.dumps(data)
    components.html(
        "<script>(function(){try{var d=window.parent.document;var s=d.createElement('script');"
        f"s.textContent={json.dumps(parent_code)};d.body.appendChild(s);}}catch(e){{}}}})();</script>",
        height=0,
    )


def main():
    st.set_page_config(
        page_title="IRaMuTeQ corpus construction",
        page_icon="◈",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    if "toolkit_input_source" not in st.session_state:
        st.session_state["toolkit_input_source"] = None

    # ---- Landing page: choose the input source ----
    if st.session_state["toolkit_input_source"] is None:
        # Small credit, bottom-left of the landing page only.
        st.markdown(
            """
            <style>
            /* The landing page is intentionally dark-background/white-text,
               unlike the rest of the app. It must NOT rely on Streamlit's
               theme (system dark/light preference, or any pinned
               config.toml theme) for its background, or the hardcoded
               white text below becomes invisible whenever that background
               resolves to a light color. Pin it explicitly here instead.
               This only affects the landing screen: this block only
               renders while toolkit_input_source is None, and every other
               page already sets its own explicit .stApp background. */
            .stApp {
                background: #0e1117 !important;
            }
            .app-credit {
                position: fixed;
                bottom: 6px;
                left: 10px;
                font-size: 0.7rem;
                color: #fff;
                opacity: 0.75;
                z-index: 9999;
                pointer-events: none;
            }
            @keyframes iramuteq-live-pulse {
                0% { opacity: 1; }
                50% { opacity: .35; }
                100% { opacity: 1; }
            }
            .iramuteq-live-badge {
                display: inline-block;
                margin-top: .5rem;
                padding: .2rem .6rem;
                border-radius: 999px;
                background: #dc2626;
                color: #fff;
                font-size: .72rem;
                font-weight: 700;
                letter-spacing: .04em;
                animation: iramuteq-live-pulse 1.4s ease-in-out infinite;
            }
            </style>
            """,
            unsafe_allow_html=True,
        )
        st.markdown('<div style="font-size:.78rem; letter-spacing:.14em; text-transform:uppercase; color:#fff; font-weight:700; margin-bottom:.5rem;">Open research utility · corpus preparation</div>', unsafe_allow_html=True)
        st.markdown('<h1 style="font-family:Georgia,\'Times New Roman\',serif; font-size:clamp(2.2rem,4vw,3.65rem); line-height:1.05; color:#fff; margin:0; font-weight:600;">IRaMuTeQ corpus construction</h1>', unsafe_allow_html=True)
        st.markdown(
            '<div style="font-size:1.08rem; line-height:1.65; color:#fff; max-width:900px; margin-top:1rem;">Build a textual corpus for IRaMuTeQ from Meta Content Library exports (Facebook or Instagram), any CSV file, a MediaCloud export, or TikTok (Research API). Choose your input source to get started.</div>',
            unsafe_allow_html=True,
        )
        st.markdown('<div style="font-family:Georgia,\'Times New Roman\',serif; font-size:1.55rem; color:#fff; margin:2.2rem 0 1rem;">Choose your input source</div>', unsafe_allow_html=True)

        tiktok_mark_orphans()
        mediacloud_running = any(m.get("status") in _ACTIVE_RUN_STATUSES for m in list_runs(pipeline="mediacloud"))
        tiktok_running = any(m.get("status") in _ACTIVE_RUN_STATUSES for m in list_runs(pipeline="tiktok"))

        cols = st.columns(len(INPUT_SOURCES))
        for col, key in zip(cols, INPUT_SOURCES):
            info = INPUT_SOURCES[key]
            with col:
                live_badge = '<div class="iramuteq-live-badge">● Extraction running</div>' if ((key == "MediaCloud" and mediacloud_running) or (key == "TikTok Research API" and tiktok_running)) else ""
                st.markdown(
                    f'<div style="border:1px solid #d9dee8; border-radius:12px; padding:1.1rem 1.2rem; background:#fff; min-height:150px;">'
                    f'<div style="font-weight:700; color:#111; margin-bottom:.4rem;">{info["label"]}</div>'
                    f'<div style="color:#555; font-size:.92rem; line-height:1.45;">{info["description"]}</div>'
                    f'{live_badge}'
                    f'</div>',
                    unsafe_allow_html=True,
                )
                if st.button(f"Use {info['label']}", key=f"choose_source_{key}", use_container_width=True):
                    st.session_state["toolkit_input_source"] = key
                    st.rerun()

        st.markdown("<div style='margin-top:1.5rem;'></div>", unsafe_allow_html=True)
        if st.button("📂 Extractions manager", use_container_width=False):
            st.session_state["toolkit_input_source"] = "Extractions manager"
            st.rerun()
        render_credit_with_copy()
        render_update_notice()
        return

    # ---- Proceed into the matching pipeline ----
    chosen = st.session_state["toolkit_input_source"]
    st.markdown(CREDIT_HIDE_CSS, unsafe_allow_html=True)  # the credit belongs to the landing page only
    with st.sidebar:
        st.markdown(f"**Input source:** {chosen}")
        if st.button("← Change input source", use_container_width=True):
            st.session_state["toolkit_input_source"] = None
            st.rerun()
        st.caption(f"IRaMuTeQ corpus construction · version {APP_VERSION} · "
                   "[changelog](https://github.com/panagiotix/corpusiramuteq/blob/main/CHANGELOG.md)")
        st.markdown("---")

    if chosen in ("Meta Content Library", "Any CSV"):
        run_corpus_builder_app(forced_source_mode=chosen)
    elif chosen == "Extractions manager":
        render_recent_extractions()
    elif chosen == "TikTok Research API":
        run_tiktok_app()
    else:
        run_mediacloud_app()


if __name__ == "__main__":
    main()
