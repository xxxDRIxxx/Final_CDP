# ✅ ONE-GO PATCHED VERSION (Fix 1 + Fix 2 + ADVANCED TYPO HANDLING)
# - Fix 1: Guard BEFORE handle_resources_intents() for ask_news/ask_about/ask_contact/ask_resources
# - Fix 2: Guard FIRST in mission_vision/core_values/mandate block BEFORE fetching DB
# - NEW: Advanced typo tolerance (handles exaggerated typos) + incomprehensible detection

from __future__ import annotations

from rasa_sdk import Action, Tracker
from rasa_sdk.executor import CollectingDispatcher
from rasa_sdk.events import SlotSet
from langdetect import detect
from typing import Any, Text, Dict, List

import mysql.connector
import requests
import os
import re
import random
from datetime import datetime
import difflib
import base64
import uuid
import cv2
import easyocr


INTENT_TO_CONTEXT = {
    "ask_replace_lost_id": ("id", "utter_replace_lost_id"),
    "ask_replace_report_card": ("report_card", "utter_replace_report_card"),
    "ask_enroll": ("enrollment", "utter_enroll"),
    "ask_request_school_forms": ("documents", "utter_ask_document_type"),
    "ask_release_after_request": ("documents", "utter_release_after_request"),
    "ask_process_documents_time": ("documents", "utter_process_documents_time"),
    "ask_certificate_fee": ("documents", "utter_certificate_fee"),
    "ask_authorized_claim": ("documents", "utter_authorized_claim"),
    "ask_uniform_cost": ("uniform", "utter_uniform_cost"),
    "ask_enrollment_requirements": ("enrollment", "utter_enrollment_requirements"),
    "ask_change_strand": ("strand", "utter_change_strand"),
    "ask_failing_marks": ("grades", "utter_failing_marks"),
    "ask_class_standing": ("grades", "utter_class_standing"),
    "ask_absence_policy": ("absences", "utter_absence_policy"),
    "ask_absence_limit": ("absences", "utter_absence_limit"),
    "ask_facility_request": ("facilities", "utter_facility_request"),
    "ask_contact_information": ("contact_information", "utter_contact_information"),
    "ask_school_address": ("school_address", "utter_school_address"),
}

# =========================================================
# OCR READER (load once)
# =========================================================
OCR_READER = easyocr.Reader(['en'], gpu=False)

API_BASE = os.getenv("UNIWISE_API_BASE", "http://127.0.0.1:8000/api")

STOPWORDS = {
    "a","an","the","is","are","was","were","to","of","and","or","in","on","at","for","with",
    "ako","ikaw","siya","po","ba","ng","na","sa","ang","mga","ito","iyan","iyon","kayo","kami",
    "please","pls","paki","help","about","info","information"
}

SYNONYMS = {
    "suspension": {
        "suspend", "suspended", "suspension",
        "walang", "pasok", "no", "classes",
        "class", "cancelled", "canceled",
        "cancel", "pasok"
    },
    "announcement": {"announcement", "announcements", "news", "update", "updates", "anunsyo", "balita"},
    "uniform": {"uniform", "uniforms", "dress", "dresscode", "dress-code", "attire", "policy", "uniporme"},
    "contact": {"email", "phone", "number", "address", "location", "kontak", "contact"},

    "release": {"release", "released", "marelease", "ma-release", "ilalabas", "lalabas", "ibibigay", "claim", "pickup"},
    "record": {"record", "records", "document", "documents", "papeles", "dokumento", "certificate", "certificates"},
    "form": {"form", "forms", "enrollment form", "school forms", "certificate", "good moral", "coe", "sf10", "f137", "sf9", "f138"},
    "process": {"process", "steps", "procedure", "paano", "proseso"},
    "days": {"days", "working days", "araw", "ilang araw", "katagal", "gaano katagal"},
}

# =========================================================
# SAVE BASE64 IMAGE
# =========================================================
def save_base64_image(base64_string: str) -> str:
    try:
        if "," in base64_string:
            base64_string = base64_string.split(",")[1]

        image_bytes = base64.b64decode(base64_string)

        os.makedirs("temp_ocr", exist_ok=True)
        filename = f"temp_ocr/ocr_{uuid.uuid4().hex}.png"

        with open(filename, "wb") as f:
            f.write(image_bytes)

        return filename

    except Exception as e:
        print(f"Image decode error: {e}")
        return None

# =========================
# Text utilities (base)
# =========================

def normalize(text: str) -> str:
    text = (text or "").lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text

def _squeeze_repeats(token: str, max_repeat: int = 2) -> str:
    """
    Collapse repeated characters: goooood -> good (max_repeat=1) or goood (max_repeat=2).
    We generate multiple variants later.
    """
    if not token:
        return token
    # Replace any run of 3+ with max_repeat occurrences
    return re.sub(r"(.)\1{2,}", lambda m: m.group(1) * max_repeat, token)

def _dedupe_consecutive(token: str) -> str:
    """
    Remove consecutive duplicates: gooood -> god, coool -> col
    (Used as a last-resort variant; can be too aggressive.)
    """
    if not token:
        return token
    out = [token[0]]
    for ch in token[1:]:
        if ch != out[-1]:
            out.append(ch)
    return "".join(out)

# =========================
# Typo correction utilities (advanced)
# =========================

def build_typo_vocab() -> set[str]:
    """
    Domain vocabulary used for typo correction + confidence scoring.
    """
    vocab = set()

    for w in STOPWORDS:
        if isinstance(w, str):
            ww = normalize(w)
            if ww:
                vocab.add(ww)

    for k, syns in SYNONYMS.items():
        if isinstance(k, str):
            kk = normalize(k)
            if kk:
                vocab.add(kk)
        if isinstance(syns, (set, list, tuple)):
            for s in syns:
                if isinstance(s, str):
                    for part in normalize(s).split():
                        if part:
                            vocab.add(part)

    admin_seed = [
        "mission","misyon","vision","bisyon","bisyo","core","values","mandate","about",
        "announcement","announcements","news","update","updates","advisory","anunsyo","balita",
        "enroll","enrollment","registration","tuition","fee","fees","payment",
        "requirement","requirements","document","documents","certificate","records",
        "good","moral","coe","sf10","f137","sf9","f138",
        "id","validation","uniform","uniporme",
        "schedule","start","date","deadline",
        "contact","email","phone","address","facebook","messenger",
        "release","claim","pickup","process","procedure","steps",
        "national","university","nu","registrar","bacoor",
        # common “short forms”
        "goodmoral","gmrc","reqs","enrol", "enrollmnt"
    ]
    for w in admin_seed:
        ww = normalize(w)
        if ww:
            vocab.add(ww)

    vocab = {w for w in vocab if len(w) >= 2}
    return vocab

TYPO_VOCAB: set[str] | None = None  # built lazily

def _best_vocab_match(token: str, vocab: set[str], cutoff: float) -> tuple[str, float] | tuple[None, float]:
    """
    Return (best_word, similarity_ratio) if found; otherwise (None, best_ratio_found).
    """
    if not token:
        return (None, 0.0)
    if token in vocab:
        return (token, 1.0)

    # Try difflib for candidate shortlist
    cand = difflib.get_close_matches(token, vocab, n=1, cutoff=cutoff)
    if not cand:
        return (None, 0.0)

    best = cand[0]
    ratio = difflib.SequenceMatcher(None, token, best).ratio()
    return (best, ratio)

def robust_correct_token(token: str, vocab: set[str]) -> str:
    """
    Multi-pass correction designed for exaggerated typos.
    - Generates variants (de-elongated, deduped)
    - Uses adaptive cutoffs (more lenient for longer tokens)
    - Picks the highest-confidence correction
    """
    if not token:
        return token

    # Keep very short tokens untouched (reduces overcorrection)
    if len(token) < 3:
        return token

    # If already valid
    if token in vocab:
        return token

    # Generate variants to handle exaggerated typos
    variants = [
        token,
        _squeeze_repeats(token, max_repeat=2),
        _squeeze_repeats(token, max_repeat=1),
        _dedupe_consecutive(token),
    ]
    # Unique, preserve order
    seen = set()
    variants = [v for v in variants if v and not (v in seen or seen.add(v))]

    # Adaptive cutoffs: try strict to lenient
    # Long tokens can tolerate lower cutoff without becoming nonsense.
    if len(token) >= 10:
        cutoffs = [0.86, 0.80, 0.74, 0.68]
    elif len(token) >= 7:
        cutoffs = [0.88, 0.82, 0.76, 0.70]
    else:
        cutoffs = [0.90, 0.84, 0.78, 0.72]

    best_word = None
    best_ratio = 0.0

    for v in variants:
        for c in cutoffs:
            w, r = _best_vocab_match(v, vocab, cutoff=c)
            if w and r > best_ratio:
                best_word, best_ratio = w, r

    # Only accept if confidence is reasonable
    # This prevents aggressive corrections on garbage input.
    if best_word and best_ratio >= 0.74:
        return best_word

    # If token was heavily elongated, allow slightly lower threshold
    # Example: "goooood" -> "good"
    if best_word and best_ratio >= 0.68 and (token != _squeeze_repeats(token, 2) or token != _squeeze_repeats(token, 1)):
        return best_word

    return token

def normalize_with_typo_fix(text: str) -> str:
    global TYPO_VOCAB

    base = normalize(text)
    if not base:
        return base

    if TYPO_VOCAB is None:
        TYPO_VOCAB = build_typo_vocab()

    toks = base.split()
    fixed = [robust_correct_token(t, TYPO_VOCAB) for t in toks]
    return " ".join(fixed).strip()

def tokenize(text: str) -> list[str]:
    # ✅ typo-tolerant tokenization
    t = normalize_with_typo_fix(text)
    return [w for w in t.split() if w and w not in STOPWORDS]

def typo_understanding_score(text: str) -> float:
    """
    0.0 to 1.0 score approximating how “understandable” the message is,
    based on how well tokens map into domain vocabulary after robust matching.
    """
    global TYPO_VOCAB
    if TYPO_VOCAB is None:
        TYPO_VOCAB = build_typo_vocab()

    raw = normalize(text)
    if not raw:
        return 0.0

    # Use raw tokens but score by best-match ratio
    toks = [t for t in raw.split() if t and t not in STOPWORDS]
    if not toks:
        return 0.0

    ratios = []
    for t in toks:
        if len(t) < 3:
            # short tokens don’t contribute much either way
            continue

        # Try variants + lenient cutoff to estimate best ratio
        variants = [
            t,
            _squeeze_repeats(t, 2),
            _squeeze_repeats(t, 1),
            _dedupe_consecutive(t),
        ]
        best_ratio = 0.0
        for v in variants:
            # lenient candidate search for scoring (not for correcting)
            cand = difflib.get_close_matches(v, TYPO_VOCAB, n=1, cutoff=0.60)
            if cand:
                r = difflib.SequenceMatcher(None, v, cand[0]).ratio()
                if r > best_ratio:
                    best_ratio = r

        ratios.append(best_ratio)

    if not ratios:
        return 0.0

    # Average match quality
    avg = sum(ratios) / len(ratios)
    return avg

def is_incomprehensible(text: str) -> bool:
    """
    Decide when to prompt user to retype:
    - If message has enough content but too little vocabulary overlap / match confidence.
    """
    raw = normalize(text)
    if not raw:
        return True

    toks = [t for t in raw.split() if t and t not in STOPWORDS]
    # very short inputs should NOT be auto-rejected
    if len(toks) <= 1:
        return False

    score = typo_understanding_score(text)

    # Also consider proportion of “very low match” tokens
    global TYPO_VOCAB
    if TYPO_VOCAB is None:
        TYPO_VOCAB = build_typo_vocab()

    low = 0
    checked = 0
    for t in toks:
        if len(t) < 3:
            continue
        checked += 1
        cand = difflib.get_close_matches(_squeeze_repeats(t, 1), TYPO_VOCAB, n=1, cutoff=0.60)
        if not cand:
            low += 1
            continue
        r = difflib.SequenceMatcher(None, _squeeze_repeats(t, 1), cand[0]).ratio()
        if r < 0.58:
            low += 1

    low_frac = (low / checked) if checked > 0 else 1.0

    # Thresholds tuned for your use-case:
    # - Accept exaggerated typos if they still map to vocab moderately.
    # - Reject mostly-noise messages.
    # Allow longer messages (OCR / pasted text)
    if len(text) > 120:
        return False

    if score < 0.45 and low_frac > 0.75:
        return True

    return False

def retype_prompt(lang: str = "en") -> str:
    if lang == "tl":
        return (
            "Medyo hirap akong maintindihan ang message dahil sa sobrang typo. "
            "Paki-type ulit (kahit keywords lang tulad ng: “good moral”, “enrollment requirements”, “latest announcements”)."
        )
    return (
        "I’m having trouble understanding your message because the typos are too heavy. "
        "Please retype it (even just keywords like “good moral”, “enrollment requirements”, “latest announcements”)."
    )

# =========================
# Matching / scoring helpers
# =========================

def expand_query_tokens(tokens):
    expanded = set(tokens)
    for t in list(tokens):
        for key, syns in SYNONYMS.items():
            if t == key or t in syns:
                expanded.add(key)
                expanded.update(syns)
    return list(expanded)

def score_item(query_tokens, title, content):
    title_toks = set(tokenize(title))
    cont_toks = set(tokenize(content))
    q = set(query_tokens)
    if not q:
        return 0
    title_hits = len(q & title_toks)
    body_hits = len(q & cont_toks)
    return (3 * title_hits) + (1 * body_hits)

def is_admin_query(text: str) -> bool:
    if not text:
        return False

    # ✅ typo-tolerant admin gate
    q = normalize_with_typo_fix(text)

    admin_keywords = [
        "mission","vision","core values","mandate","about",
        "announcement","announcements","news","update","advisory",
        "enroll","enrollment","registration","term","ay",
        "tuition","fee","fees","payment",
        "requirement","requirements","document","documents",
        "good moral","coe","sf10","f137","sf9","f138",
        "id","validation","uniform","uniporme",
        "schedule","start","date","deadline",
        "contact","email","phone","address","facebook","messenger",
        "release","claim","pickup","process","procedure","steps",
        "national university", "nu", "nationalians",
        "registrar", "sdao", "nazareth", "bacoor"
    ]

    return any(k in q for k in admin_keywords)

# =========================
# ✅ Image helpers for your /resources schema
# =========================

def extract_image_urls(post: dict) -> list[str]:
    urls = []

    img = (post.get("image") or "").strip()
    if img:
        urls.append(img)

    imgs = post.get("images") or []
    if isinstance(imgs, list):
        for u in imgs:
            if isinstance(u, str) and u.strip():
                urls.append(u.strip())

    seen = set()
    out = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out

def format_post(post: dict) -> dict:
    title = (post.get("title") or "").strip()
    content = (post.get("content") or "").strip()

    if title:
        text = f"{title}\n\n{content}".strip()
    else:
        text = content or "(No details provided.)"

    images = extract_image_urls(post)
    return {"text": text, "images": images}

def safe_detect_language(text: str) -> str:
    try:
        if not text or len(text.strip()) < 2:
            return "en"
        lang = detect(text)
        return "tl" if lang == "tl" else "en"
    except Exception:
        return "en"

def contains_any(haystack: str, needles):
    # For DB matching, keep conservative normalization (titles/content are usually clean)
    h = normalize(haystack)
    return any(n in h for n in needles)

# =========================================================
# OCR HELPER
# =========================================================
def extract_text_from_image(image_path: str) -> str:
    try:
        img = cv2.imread(image_path)

        if img is None:
            return ""

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        # Improve OCR accuracy
        thresh = cv2.adaptiveThreshold(
            gray,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            11,
            2
        )

        result = OCR_READER.readtext(thresh)

        extracted = " ".join([r[1] for r in result])

        # remove weird spacing
        extracted = re.sub(r"\s+", " ", extracted)

        # remove stray symbols OCR sometimes creates
        extracted = re.sub(r"[|_]{2,}", " ", extracted)

        # remove OCR garbage characters
        extracted = re.sub(r"[^\w\s.,:/()-]", " ", extracted)

        extracted = extracted.strip()

        # =================================================
        # LIMIT EXTREMELY LARGE OCR INPUTS
        # =================================================
        if len(extracted) > 2000:
            extracted = extracted[:2000]

        return extracted

    except Exception as e:
        print(f"OCR error: {e}")
        return ""

# =========================================================
# FAQ KNOWLEDGE BASE (your full FAQ_KB kept as-is)
# =========================================================

FAQ_KB = {
    "lost_id": {
        "q": "How do I replace a lost ID?",
        "offices": ["Class Adviser", "Office of the Admin", "School Cashier", "School Head"],
        "steps": [
            "Immediately report the loss of the school ID to the Class Adviser.",
            "Secure and accomplish an ID Replacement Request Form.",
            "The Class Adviser endorses the request to the Admin for record verification.",
            "The request is forwarded to the School Head for approval.",
            "Upon approval, the Admin or Class Adviser issues a new school ID or a temporary gate pass.",
        ],
        "refs": ["DepEd Order No. 41, s. 2012", "School-based policy under the authority of the School Head"],
    },
    "lost_report_card": {
        "q": "How do I replace a lost report card?",
        "offices": ["Class Adviser", "Office of the Registrar", "School Head"],
        "steps": [
            "Submit a written request for report card replacement to the Class Adviser.",
            "The Class Adviser evaluates and endorses the request to the Registrar.",
            "The Registrar verifies the learner’s academic records.",
            "The request is elevated to the School Head for approval.",
            "The Registrar releases a certified true copy of the report card.",
        ],
        "refs": ["DepEd Order No. 8, s. 2015", "DepEd Order No. 41, s. 2012"],
    },
    "enroll": {
        "q": "How do I enroll?",
        "offices": ["Enrollment Committee", "Office of the Registrar", "Guidance Office"],
        "steps": [
            "Secure an enrollment form from the Office of the Registrar.",
            "Submit the accomplished form with complete requirements (e.g., SF9, Birth Certificate, JHS Certificate of Completion, etc.).",
            "The Registrar evaluates and validates the submitted records.",
            "The Guidance Office confirms strand placement, when applicable.",
            "You are officially enrolled after confirmation and encoding of records.",
        ],
        "refs": ["DepEd Order No. 3, s. 2018", "Annual DepEd Enrollment Guidelines Memorandum"],
    },
    "request_school_forms": {
        "q": "How do I request school forms (Good Moral, Certificate of Enrollment, SF10, F137, etc.)?",
        "offices": ["Office of the Registrar", "School Head"],
        "steps": [
            "Secure a request form from the Office of the Registrar.",
            "Accomplish and submit the request form for processing.",
            "The Registrar verifies the learner’s records and the purpose of the request.",
            "The request is endorsed to the School Head for approval, when required.",
            "The requested document is released by the Registrar.",
        ],
        "refs": ["DepEd Order No. 41, s. 2012", "DepEd Order No. 58, s. 2017"],
    },
    "release_after_request": {
        "q": "When will the records/documents be released after requesting them?",
        "offices": ["Office of the Registrar"],
        "steps": [
            "The request is logged and acknowledged by the Registrar.",
            "Records are verified and prepared for release.",
            "Approved documents are released within the prescribed processing period.",
        ],
        "extra": {"processing_time": "3–7 working days"},
        "refs": ["DepEd Citizen’s Charter", "Republic Act No. 11032"],
    },
    "uniform_cost": {
        "q": "How much is the uniform?",
        "offices": ["School Administration", "Authorized School Supplier"],
        "steps": [
            "The school announces the approved uniform design and price at the start of the school year.",
            "Learners inquire from the school or authorized supplier.",
            "Uniforms are purchased directly from the authorized supplier.",
        ],
        "notes": ["Wearing of school uniform is encouraged but shall not be a basis for exclusion from classes."],
        "refs": ["DepEd Order No. 46, s. 2008", "DepEd Order No. 88, s. 2010"],
    },
    "transferees_late_enrollees": {
        "q": "What is the process for transferees/late enrollees?",
        "offices": ["Office of the Registrar", "Guidance Office", "School Head"],
        "steps": [
            "Submit complete transfer credentials to the Office of the Registrar.",
            "The Registrar evaluates the authenticity and completeness of records.",
            "The applicant undergoes assessment or interview with the Guidance Office.",
            "The School Head reviews and approves the enrollment, subject to availability of slots.",
            "Upon approval, the learner is officially enrolled.",
        ],
        "refs": ["DepEd Order No. 3, s. 2018", "DepEd Order No. 54, s. 2016"],
    },
    "change_strand": {
        "q": "How do I request for a change of strand?",
        "offices": ["Guidance Office", "Office of the Registrar", "School Head"],
        "steps": [
            "Submit a written request for strand change to the Guidance Office.",
            "The Guidance Counselor conducts an assessment and evaluates suitability.",
            "Academic records are reviewed by the Registrar.",
            "The request is forwarded to the School Head for final approval.",
            "Approved changes are reflected in official school records (Form 201 Learner’s File).",
        ],
        "refs": ["DepEd Order No. 51, s. 2014", "DepEd Order No. 54, s. 2016"],
    },
    "failing_marks": {
        "q": "What is the process if I have failing marks?",
        "offices": ["Subject Teacher", "Class Adviser", "Guidance Office", "School Head"],
        "steps": [
            "The subject teacher informs the learner of failing or at-risk performance.",
            "The learner undergoes academic counseling and intervention.",
            "Remedial classes or support programs are provided, as applicable.",
            "Learner performance is reassessed after intervention.",
            "Final grades are recorded following DepEd assessment guidelines.",
        ],
        "refs": ["DepEd Order No. 8, s. 2015", "DepEd Order No. 73, s. 2012"],
    },
    "graduation_requirements": {
        "q": "What are the graduation requirements?",
        "offices": ["Subject Teachers", "Class Adviser", "Office of the Registrar", "School Head"],
        "steps": [
            "Complete all required SHS subjects.",
            "Secure academic clearances, school forms, and documents.",
            "Records are verified by the Registrar.",
            "The School Head confirms eligibility for graduation.",
        ],
        "refs": ["DepEd Order No. 36, s. 2016", "DepEd Order No. 8, s. 2015"],
    },
    "drop_add_subjects": {
        "q": "What is the process of dropping and adding subjects?",
        "offices": ["Class Adviser", "Office of the Registrar", "School Head"],
        "steps": [
            "Submit a written request within the allowed adjustment period.",
            "The Class Adviser evaluates and endorses the request.",
            "The Registrar reviews subject load and records.",
            "Approval is secured from the School Head.",
            "Official records are updated accordingly.",
        ],
        "refs": ["DepEd Order No. 54, s. 2016"],
    },
    "dropping_students": {
        "q": "What is the process of dropping students?",
        "offices": ["Class Adviser", "Guidance Office", "School Head", "Office of the Registrar"],
        "steps": [
            "The learner is identified due to excessive absences, academic deficiency, or disciplinary concerns.",
            "Counseling and parent/guardian conference are conducted.",
            "Due process and documentation are completed.",
            "The School Head issues the decision.",
            "Dropping is recorded officially by the Registrar.",
        ],
        "refs": ["DepEd Order No. 92, s. 2012", "DepEd Order No. 8, s. 2015"],
    },
    "absence_policy": {
        "q": "What is the excuse letter or absence policy?",
        "offices": ["Class Adviser"],
        "steps": [
            "Submit a written excuse letter signed by the parent/guardian upon return.",
            "Attach supporting documents, if applicable.",
            "The Class Adviser validates the excuse.",
            "Attendance records are updated accordingly.",
        ],
        "refs": ["DepEd Order No. 8, s. 2015"],
    },
    "request_facilities": {
        "q": "How do I request the use of school facilities?",
        "offices": ["Subject Teacher/Class Adviser", "Property Custodian", "School Head"],
        "steps": [
            "Submit a written request stating purpose and schedule.",
            "The request is endorsed by the adviser or subject teacher.",
            "Availability and safety checks are conducted by the Property Custodian.",
            "Final approval is given by the School Head.",
        ],
        "refs": ["DepEd Order No. 40, s. 2015", "DepEd Order No. 13, s. 2018"],
    },
    "get_enrollment_form": {
        "q": "Where can we get the enrollment form?",
        "offices": ["Office of the Registrar"],
        "steps": [
            "Inquire at the Registrar’s Office.",
            "The enrollment form is issued and explained.",
        ],
        "refs": ["DepEd Order No. 3, s. 2018"],
    },
    "enrollment_requirements": {
        "q": "What are the requirements for enrollment?",
        "offices": ["Office of the Registrar"],
        "steps": [
            "Prepare the required documents.",
            "Submit documents for evaluation.",
            "Compliance is confirmed prior to enrollment.",
        ],
        "refs": ["DepEd Order No. 58, s. 2017", "DepEd Order No. 3, s. 2018"],
    },
    "authorized_claim": {
        "q": "Can someone else claim my documents on my behalf?",
        "offices": ["Office of the Registrar"],
        "steps": [
            "The authorized representative presents an authorization letter.",
            "Valid identification cards of both parties are submitted.",
            "The Registrar verifies records.",
            "Documents are released to the authorized representative.",
        ],
        "refs": ["DepEd Order No. 41, s. 2012"],
    },
    "fee_for_certificates": {
        "q": "Is there a fee for requesting certificates or forms?",
        "offices": ["Office of the Registrar"],
        "steps": [
            "Submit a duly accomplished request form to the Office of the Registrar.",
            "The Registrar verifies the request and the learner’s records.",
            "The requested certificate or school form is processed without any payment.",
            "The document is released upon completion of processing.",
        ],
        "notes": ["As a public school, the school does not collect any fees for issuance of school records, certificates, or forms."],
        "refs": ["DepEd Order No. 46, s. 2008 – Non-Compulsory Collection of Fees"],
    },
    "processing_time_docs": {
        "q": "How long does it take to process documents?",
        "offices": ["Office of the Registrar"],
        "steps": [
            "The request is officially recorded.",
            "Documents are processed and verified.",
            "Release is done within the prescribed timeline.",
        ],
        "refs": ["DepEd Citizen’s Charter", "Republic Act No. 11032"],
    },
    "class_standing": {
        "q": "How is class standing computed?",
        "offices": ["Subject Teacher"],
        "steps": [
            "Written works are assessed.",
            "Performance tasks are evaluated using rubrics.",
            "Quarterly assessments are administered.",
            "Final grades are computed following DepEd policy.",
        ],
        "refs": ["DepEd Order No. 8, s. 2015"],
    },
    "absences_allowed": {
        "q": "How many absences are allowed before failing?",
        "offices": ["Class Adviser", "Guidance Office"],
        "steps": [
            "Attendance is monitored regularly by the Class Adviser.",
            "Learners approaching the allowable absence limit are notified.",
            "Counseling and intervention are provided.",
            "Learners exceeding 20% of total school days may be dropped or failed, following due process.",
        ],
        "refs": ["DepEd Order No. 8, s. 2015", "DepEd Order No. 3, s. 2018"],
    },
}

FAQ_INTROS = [
    "Here’s the process you can follow:",
    "This is the usual step-by-step process:",
    "You can do it through these steps:",
    "Here is the standard procedure:",
]

FAQ_CLOSERS = [
    "If you want, tell me what document you need and I’ll point you to the right office.",
    "If you share what you’re requesting, I can help you confirm the steps.",
    "If you’d like, I can summarize it in a shorter version.",
]

def render_faq_answer(key: str) -> str:
    item = FAQ_KB.get(key)
    if not item:
        return "I can’t find that FAQ yet. Please try a different keyword."

    intro = random.choice(FAQ_INTROS)
    closer = random.choice(FAQ_CLOSERS)

    offices = item.get("offices", [])
    steps = item.get("steps", [])
    refs = item.get("refs", [])
    notes = item.get("notes", [])
    extra = item.get("extra", {})

    style = random.choice(["offices_first", "steps_first", "compact"])
    parts = []

    if style == "offices_first" and offices:
        parts.append("Office involved: " + "; ".join(offices))
        parts.append(intro)
    else:
        parts.append(intro)

    if style == "compact":
        parts.append(" ".join([f"{i+1}) {s}" for i, s in enumerate(steps)]))
    else:
        for i, s in enumerate(steps, start=1):
            parts.append(f"{i}) {s}")

    if style == "steps_first" and offices:
        parts.append("")
        parts.append("Office involved: " + "; ".join(offices))

    if extra.get("processing_time"):
        parts.append(f"Standard processing time: {extra['processing_time']}")

    if notes:
        parts.append("Note: " + " ".join(notes))

    if refs:
        parts.append("Reference: " + "; ".join(refs))

    parts.append(closer)
    return "\n".join([p for p in parts if p is not None]).strip()

# =========================================================
# Ambiguity detection + FAQ key guessing
# =========================================================

def detect_ambiguity(user_text: str):
    u = normalize_with_typo_fix(user_text)
    toks = set(tokenize(u))
    expanded = set(expand_query_tokens(list(toks)))

    release_terms = {"release", "released", "marelease", "ma-release", "claim", "pickup"}
    time_terms = {"days", "araw", "katagal", "ilang"}
    doc_terms = {"document", "documents", "records", "papeles", "dokumento"}

    has_release = any(t in expanded for t in release_terms) or ("release" in u)
    has_time = any(t in expanded for t in time_terms) or ("working days" in u)
    has_doc = any(t in expanded for t in doc_terms)

    if has_release and (len(toks) <= 5) and not (has_time or has_doc):
        return (
            "When you say “released”, which one do you mean?\n"
            "A) Processing time (how many working days)\n"
            "B) Document release procedure (steps after you request)"
        )

    if "form" in expanded and ("enroll" not in u) and ("enrollment" not in u) and len(toks) <= 6:
        return (
            "Which form are you asking about?\n"
            "A) Enrollment form (where to get it)\n"
            "B) Requesting school forms/certificates (Good Moral, SF10/F137, etc.)"
        )

    return None

def guess_faq_key(user_text: str):
    u = normalize_with_typo_fix(user_text)
    if not u:
        return None

    if "lost" in u and "id" in u:
        return "lost_id"
    if ("lost" in u or "nawala" in u) and ("card" in u or "report" in u or "sf9" in u):
        return "lost_report_card"
    if "enroll" in u or "enrollment" in u or "mag enroll" in u:
        if ("when" in u or "kailan" in u or "kelan" in u or "date" in u or "schedule" in u or "start" in u):
            return None
        if "requirements" in u or "need" in u or "req" in u:
            return "enrollment_requirements"
        if "form" in u:
            return "get_enrollment_form"
        return "enroll"

    if "uniform" in u or "uniporme" in u:
        return "uniform_cost"
    if "good moral" in u or "sf10" in u or "f137" in u or "coe" in u or ("school form" in u) or ("certificate" in u and "enrollment" in u):
        return "request_school_forms"
    if "release" in u or "marelease" in u or "ma release" in u or "claim" in u or "pickup" in u:
        if "day" in u or "days" in u or "araw" in u or "katagal" in u or "ilang" in u:
            return "processing_time_docs"
        return "release_after_request"
    if "processing" in u and ("time" in u or "days" in u or "ilang" in u or "araw" in u):
        return "processing_time_docs"
    if "fee" in u or "bayad" in u or "payment" in u:
        return "fee_for_certificates"
    if "proxy" in u or "on my behalf" in u or "authorized" in u or "representative" in u or "authorization" in u:
        return "authorized_claim"
    if "absence" in u or "absent" in u or "excuse" in u:
        if "how many" in u or "ilang" in u or "allowed" in u or "limit" in u:
            return "absences_allowed"
        return "absence_policy"
    if "facilities" in u or "facility" in u or "room" in u or "venue" in u:
        return "request_facilities"
    if "transfer" in u or "transferee" in u or "late enroll" in u or "late enrol" in u:
        return "transferees_late_enrollees"
    if "change strand" in u or "switch strand" in u or "palit strand" in u or "lipat strand" in u:
        return "change_strand"
    if "drop add" in u or ("drop" in u and "subject" in u) or "add subject" in u:
        return "drop_add_subjects"
    if "drop" in u and ("student" in u or "ma-drop" in u or "dropping" in u):
        return "dropping_students"
    if "graduation" in u or "graduate" in u or "grumaduate" in u:
        return "graduation_requirements"
    if "failing" in u or "bagsak" in u or "failed" in u:
        return "failing_marks"
    if "class standing" in u or "grading" in u or ("compute" in u and "grade" in u):
        return "class_standing"

    return None

# =========================================================
# Continuous learning mechanism (logs unclear queries to MySQL)
# =========================================================

def get_mysql_conn():
    return mysql.connector.connect(
        host=os.getenv("UNIWISE_DB_HOST", "127.0.0.1"),
        user=os.getenv("UNIWISE_DB_USER", "root"),
        password=os.getenv("UNIWISE_DB_PASSWORD", "chatbot_cavite@1234"),
        database=os.getenv("UNIWISE_DB_NAME", "uniwise_db"),
    )

def log_unknown_query(user_text: str, intent_name: str = ""):
    try:
        conn = get_mysql_conn()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS uniwise_unknown_queries (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_text TEXT,
                detected_intent VARCHAR(255),
                created_at DATETIME
            )
        """)
        cur.execute(
            "INSERT INTO uniwise_unknown_queries (user_text, detected_intent, created_at) VALUES (%s, %s, %s)",
            (user_text, intent_name, datetime.now())
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception:
        pass

# =========================================================
# MySQL retrieval for About topic (Mission/Vision/Core Values/Mandate)
# =========================================================

def fetch_about_topic_from_db(topic: str, user_text: str = ""):
    u = normalize_with_typo_fix(user_text)

    wants_mission = ("mission" in u) or ("misyon" in u)
    wants_vision  = ("vision" in u)  or ("bisyo" in u) or ("bisyon" in u)

    if topic == "mission_vision":
        if wants_mission and not wants_vision:
            keywords = ["mission", "misyon"]
        elif wants_vision and not wants_mission:
            keywords = ["vision", "bisyon", "bisyo"]
        else:
            keywords = ["mission", "misyon", "vision", "bisyon", "bisyo"]

    elif topic == "core_values":
        keywords = ["core values", "values", "value", "pinahahalagahan", "pagpapahalaga"]

    elif topic == "mandate":
        keywords = ["mandate", "mandato"]
    else:
        keywords = []

    if not keywords:
        return None

    conn = get_mysql_conn()
    cur = conn.cursor()
    cur.execute("SELECT title, content, updated_at FROM about ORDER BY updated_at DESC")
    rows = cur.fetchall()
    cur.close()
    conn.close()

    for title, content, _updated_at in rows:
        if contains_any(title or "", keywords):
            text = (content or "").strip()
            if text:
                return text

    for title, content, _updated_at in rows:
        if contains_any(content or "", keywords):
            text = (content or "").strip()
            if text:
                return text

    return None

# =========================================================
# ✅ Resources fetch + handler (fixed to return UI-safe dicts)
# =========================================================

def fetch_resources():
    try:
        resp = requests.get(f"{API_BASE}/resources", timeout=5)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None

def handle_resources_intents(intent: str, user_text: str) -> dict | str | None:
    db = fetch_resources()
    if not db:
        return None

    news = db.get("newsData", []) or []
    about = db.get("aboutData", []) or []
    contact = db.get("contactData") or {}

    q_basic = set(tokenize(user_text))
    q_tokens = expand_query_tokens(list(q_basic))

    def sort_latest(items):
        return sorted(items, key=lambda p: (p.get("updated_at") or ""), reverse=True)

    if intent == "ask_contact":
        email = (contact.get("email") or "").strip()
        phone = (contact.get("phone") or "").strip()
        address = (contact.get("address") or "").strip()
        return (
            "Contact details:\n"
            f"Email: {email or '(not posted)'}\n"
            f"Phone: {phone or '(not posted)'}\n"
            f"Address: {address or '(not posted)'}"
        )

    if intent == "ask_news":
        if not news:
            return "There are no announcements at the moment."

        latest_triggers = {
            "latest", "recent", "new", "news", "update", "updates",
            "announcement", "announcements", "anunsyo", "balita"
        }

        scored = [(p, score_item(q_tokens, p.get("title", ""), p.get("content", ""))) for p in news]
        scored.sort(key=lambda x: x[1], reverse=True)

        wants_latest = any(t in q_basic for t in latest_triggers)

        if scored and scored[0][1] > 0 and not wants_latest:
            return format_post(scored[0][0])

        latest_news = sort_latest(news)[:3]
        return {"text": "Latest announcements:", "items": [format_post(p) for p in latest_news]}

    if intent == "ask_about":
        if not about:
            return "About information is not available right now."

        scored = [(p, score_item(q_tokens, p.get("title", ""), p.get("content", ""))) for p in about]
        scored.sort(key=lambda x: x[1], reverse=True)

        if scored and scored[0][1] > 0:
            return format_post(scored[0][0])

        titles = [(p.get("title") or "").strip() for p in about if (p.get("title") or "").strip()]
        if titles:
            return "Available About topics:\n- " + "\n- ".join(titles[:10])

        first = about[:3]
        return {"text": "Here is the available About information:", "items": [format_post(p) for p in first]}

    if intent == "ask_resources":
        candidates = []
        for p in news:
            candidates.append(("NEWS", p, score_item(q_tokens, p.get("title", ""), p.get("content", ""))))
        for p in about:
            candidates.append(("ABOUT", p, score_item(q_tokens, p.get("title", ""), p.get("content", ""))))

        candidates.sort(key=lambda x: x[2], reverse=True)
        best = [c for c in candidates if c[2] > 0][:2]

        if best:
            items = []
            for section, post, _ in best:
                fp = format_post(post)
                items.append({"text": f"[{section}]\n{fp['text']}", "images": fp.get("images", [])})
            return {"text": "", "items": items}

        if news:
            latest_news = sort_latest(news)[:2]
            return {"text": "Here are the latest announcements:", "items": [format_post(p) for p in latest_news]}

        if about:
            titles = [(p.get("title") or "").strip() for p in about if (p.get("title") or "").strip()]
            if titles:
                return "Available About topics:\n- " + "\n- ".join(titles[:10])
            return "About info is available but untitled."

        return "No resources are available right now."

    return None

# =========================================================
# ACTION: Dynamic FAQ
# =========================================================

class ActionDynamicFAQ(Action):
    def name(self):
        return "action_dynamic_faq"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict):
        latest = tracker.latest_message or {}
        intent_name = ((latest.get("intent") or {}).get("name") or "").strip()
        user_text = (latest.get("text") or "").strip()
        lang_code = safe_detect_language(user_text)

        images = tracker.latest_message.get("images") or []
        single_image = tracker.latest_message.get("image")

        if not images and not single_image and is_incomprehensible(user_text):
            dispatcher.utter_message(text=retype_prompt(lang_code))
            log_unknown_query(user_text, intent_name=intent_name or "faq_garbled")
            return []

        # 🚫 If not admin-related, DO NOT answer FAQ
        if not is_admin_query(user_text):
            dispatcher.utter_message(
                text="I can help with school-related questions (enrollment, announcements, contact info, etc.). What would you like to ask?"
            )
            return []

        rs_key = (
            (latest.get("response_selector") or {})
            .get("faq", {})
            .get("response", {})
            .get("intent_response_key")
        )
        key = ""
        if rs_key and isinstance(rs_key, str) and rs_key.startswith("faq/"):
            key = rs_key.split("/", 1)[1].strip()

        if not key and intent_name.startswith("faq/"):
            key = intent_name.split("/", 1)[1].strip()

        if not key:
            key = guess_faq_key(user_text) or ""

        if key and key in FAQ_KB:
            dispatcher.utter_message(text=render_faq_answer(key))
            return [SlotSet("faq_clarify_asked", False), SlotSet("last_faq_key", key)]

        clarifier = detect_ambiguity(user_text)
        if clarifier:
            dispatcher.utter_message(text=clarifier)
            return [SlotSet("faq_clarify_asked", True)]

        already_asked = bool(tracker.get_slot("faq_clarify_asked"))
        if not already_asked:
            dispatcher.utter_message(
                text=(
                    "Which FAQ do you mean? You can reply with a keyword or short phrase like:\n"
                    "- lost id\n"
                    "- enroll / enrollment\n"
                    "- enrollment requirements\n"
                    "- enrollment form\n"
                    "- document release\n"
                    "- processing time\n"
                    "- uniform\n"
                    "- excuse letter / absence\n"
                    "- absences allowed\n"
                    "- request forms (Good Moral / SF10 / F137)\n\n"
                    "You can also reply with just 1–2 words."
                )
            )
            return [SlotSet("faq_clarify_asked", True)]

        dispatcher.utter_message(
            text="I still couldn’t match that. Please reply with a clearer keyword (e.g., “lost id”, “document release”, “uniform”, “enroll”)."
        )
        log_unknown_query(user_text, intent_name=intent_name or "faq")
        return []

# =========================================================
# Helper: is this a "good" model answer?
# =========================================================

def is_good_model_answer(text: str) -> bool:
    if not text:
        return False
    bad_markers = [
        "I didn’t quite get that. Can you rephrase your question?",
        "I’m not sure I follow. Can you give more details?",
        "Sorry, I missed that. Could you try asking another way?"
    ]
    t = text.strip()
    return not any(m.lower() in t.lower() for m in bad_markers)

class ActionSetTopic(Action):

    def name(self) -> Text:
        return "action_set_topic"

    def run(self, dispatcher, tracker, domain):

        intent = (tracker.latest_message.get("intent") or {}).get("name")

        ctx = INTENT_TO_CONTEXT.get(intent)

        if not ctx:
            return []

        topic, utter_name = ctx

        return [
            SlotSet("topic", topic),
            SlotSet("last_utterance", utter_name)
        ]

# =========================================================
# ACTION: Dynamic response (MODEL first -> DB -> RESOURCES -> fallback)
# =========================================================
class ActionHandlePronoun(Action):
    def name(self) -> Text:
        return "action_handle_pronoun"

    def run(
        self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]
    ) -> List[Dict[Text, Any]]:
        topic = tracker.get_slot("topic")
        last_utt = tracker.get_slot("last_utterance")
        document_type = tracker.get_slot("document_type")

        if document_type:
            doc = str(document_type).lower().strip()

            if doc == "good moral":
                dispatcher.utter_message(response="utter_doc_good_moral")
                dispatcher.utter_message(
                    text="You can also ask about its **processing time**, **release schedule**, or **authorized claiming**."
                )
                return []

            elif doc == "certificate of enrollment":
                dispatcher.utter_message(response="utter_doc_certificate_enrollment")
                dispatcher.utter_message(
                    text="You can also ask about its **processing time**, **release schedule**, or **authorized claiming**."
                )
                return []

            elif doc == "sf9":
                dispatcher.utter_message(response="utter_doc_sf9")
                dispatcher.utter_message(
                    text="You can also ask about its **processing time**, **release schedule**, or **authorized claiming**."
                )
                return []

            elif doc == "report card":
                dispatcher.utter_message(response="utter_doc_report_card")
                dispatcher.utter_message(
                    text="You can also ask about its **processing time**, **release schedule**, or **authorized claiming**."
                )
                return []

            elif doc == "sf10":
                dispatcher.utter_message(response="utter_doc_sf10")
                dispatcher.utter_message(
                    text="You can also ask about its **processing time**, **release schedule**, or **authorized claiming**."
                )
                return []

            elif doc == "f137":
                dispatcher.utter_message(response="utter_doc_f137")
                dispatcher.utter_message(
                    text="You can also ask about its **processing time**, **release schedule**, or **authorized claiming**."
                )
                return []

        if last_utt:
            dispatcher.utter_message(response=last_utt)

            if topic:
                dispatcher.utter_message(
                    text=f"We were discussing **{topic}**. You can ask for the **requirements**, **steps**, **timeline**, or **related concerns**."
                )
            return []

        dispatcher.utter_message(
            text="Can you clarify what topic you mean? You can mention **enrollment**, **school address**, **documents**, **uniforms**, **report card**, or **absences**."
        )
        return []

class ActionContextFallback(Action):
    def name(self) -> Text:
        return "action_context_fallback"

    def run(
        self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]
    ) -> List[Dict[Text, Any]]:
        topic = tracker.get_slot("topic")
        document_type = tracker.get_slot("document_type")
        last_utt = tracker.get_slot("last_utterance")
        user_text = (tracker.latest_message.get("text") or "").strip().lower()

        pronouns = {"it", "that", "this", "them", "those", "there"}

        if any(word in user_text.split() for word in pronouns):
            if document_type:
                dispatcher.utter_message(
                    text=f"Are you referring to **{document_type}**?"
                )
                return []

            if last_utt:
                dispatcher.utter_message(
                    text="Do you mean the **last topic** we discussed? I can repeat that answer."
                )
                dispatcher.utter_message(response=last_utt)
                return []

        if topic:
            dispatcher.utter_message(
                text=f"I’m not fully sure. Are you asking about **{topic}**?"
            )
            return []

        dispatcher.utter_message(response="utter_fallback")
        return []


class ActionRouteDocumentType(Action):
    def name(self) -> Text:
        return "action_route_document_type"

    def _normalize(self, text: Any) -> Text:
        s = str(text).lower().strip()
        s = s.replace("_", " ").replace("-", " ")
        s = " ".join(s.split())
        return s

    def _canonicalize(self, doc_norm: Text) -> Text:
        alias = {
            "gm": "good moral",
            "good moral certificate": "good moral",
            "goodmoral": "good moral",

            "coe": "certificate of enrollment",
            "cert of enrollment": "certificate of enrollment",
            "enrollment certificate": "certificate of enrollment",
            "certificate enrollment": "certificate of enrollment",

            "sf 9": "sf9",
            "sf9": "sf9",
            "form 9": "sf9",
            "school form 9": "sf9",

            "reportcard": "report card",
            "grade card": "report card",
            "sf9 report card": "report card",

            "sf 10": "sf10",
            "sf10": "sf10",
            "form 10": "sf10",
            "student permanent record": "sf10",

            "f 137": "f137",
            "f137": "f137",
            "form 137": "f137",
            "permanent record f137": "f137",
        }

        if doc_norm in alias:
            return alias[doc_norm]

        if "good moral" in doc_norm:
            return "good moral"

        if "certificate" in doc_norm and "enroll" in doc_norm:
            return "certificate of enrollment"

        if ("sf" in doc_norm and "9" in doc_norm) or ("form" in doc_norm and "9" in doc_norm):
            return "sf9"

        if "report card" in doc_norm or "grade card" in doc_norm:
            return "report card"

        if ("sf" in doc_norm and "10" in doc_norm) or ("form" in doc_norm and "10" in doc_norm):
            return "sf10"

        if "137" in doc_norm:
            return "f137"

        return doc_norm

    def run(
        self,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: Dict[Text, Any],
    ) -> List[Dict[Text, Any]]:

        doc = tracker.get_slot("document_type")

        if not doc:
            ents = tracker.latest_message.get("entities", [])
            for e in ents:
                if e.get("entity") == "document_type":
                    doc = e.get("value")
                    break

        if not doc:
            dispatcher.utter_message(response="utter_doc_unknown")
            return [
                SlotSet("topic", "documents"),
                SlotSet("last_utterance", "utter_doc_unknown")
            ]

        doc_norm = self._normalize(doc)
        doc_final = self._canonicalize(doc_norm)

        if doc_final == "good moral":
            dispatcher.utter_message(response="utter_doc_good_moral")
            return [
                SlotSet("document_type", "good moral"),
                SlotSet("topic", "documents"),
                SlotSet("last_utterance", "utter_doc_good_moral")
            ]

        elif doc_final == "certificate of enrollment":
            dispatcher.utter_message(response="utter_doc_certificate_enrollment")
            return [
                SlotSet("document_type", "certificate of enrollment"),
                SlotSet("topic", "documents"),
                SlotSet("last_utterance", "utter_doc_certificate_enrollment")
            ]

        elif doc_final == "sf9":
            dispatcher.utter_message(response="utter_doc_sf9")
            return [
                SlotSet("document_type", "sf9"),
                SlotSet("topic", "documents"),
                SlotSet("last_utterance", "utter_doc_sf9")
            ]

        elif doc_final == "report card":
            dispatcher.utter_message(response="utter_doc_report_card")
            return [
                SlotSet("document_type", "report card"),
                SlotSet("topic", "documents"),
                SlotSet("last_utterance", "utter_doc_report_card")
            ]

        elif doc_final == "sf10":
            dispatcher.utter_message(response="utter_doc_sf10")
            return [
                SlotSet("document_type", "sf10"),
                SlotSet("topic", "documents"),
                SlotSet("last_utterance", "utter_doc_sf10")
            ]

        elif doc_final == "f137":
            dispatcher.utter_message(response="utter_doc_f137")
            return [
                SlotSet("document_type", "f137"),
                SlotSet("topic", "documents"),
                SlotSet("last_utterance", "utter_doc_f137")
            ]

        else:
            dispatcher.utter_message(response="utter_doc_unknown")
            return [
                SlotSet("document_type", doc_norm),
                SlotSet("topic", "documents"),
                SlotSet("last_utterance", "utter_doc_unknown")
            ]


class ActionSuggestFollowups(Action):
    def name(self) -> Text:
        return "action_suggest_followups"

    def run(
        self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]
    ) -> List[Dict[Text, Any]]:
        topic = tracker.get_slot("topic")

        suggestions = {
            "school_address": [
                "contact information",
                "who can apply",
                "academic strands offered"
            ],
            "contact_information": [
                "school address",
                "how do I enroll",
                "enrollment requirements"
            ],
            "about_school": [
                "school address",
                "who can apply",
                "academic strands offered",
                "learning modes",
                "scholarships"
            ],
            "enrollment": [
                "enrollment requirements",
                "where can I get the enrollment form",
                "process for transferees"
            ],
            "documents": [
                "processing time",
                "release after request",
                "authorized claiming"
            ],
            "uniform": [
                "where can I buy the uniform",
                "school contact information",
                "learning modes"
            ],
            "academic_strands": [
                "eligibility requirements",
                "can I switch strands in between semesters",
                "class size"
            ],
            "grades": [
                "class standing",
                "absence limit",
                "what happens if I have failing marks"
            ],
            "absences": [
                "absence policy",
                "absence limit",
                "failing marks"
            ]
        }

        if topic in suggestions:
            suggestion_text = "\n".join([f"• {item}" for item in suggestions[topic]])
            dispatcher.utter_message(
                text=f"You may also ask:\n{suggestion_text}"
            )

        return []

class ActionDynamicResponse(Action):
    def name(self):
        return "action_dynamic_response"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict):

        latest_message = tracker.latest_message or {}

        print("latest_message:", tracker.latest_message)

        user_text = latest_message.get("text") or ""
        intent = (latest_message.get("intent") or {}).get("name") or ""

        # =========================================================
        # AUTO OCR DETECTION (MULTIPLE IMAGES)
        # =========================================================

        metadata = latest_message.get("metadata") or {}

        images = metadata.get("images") or []
        single_image = metadata.get("image")

        ocr_detected = False
        skip_typo_check = False
        all_extracted_text = []

        # MULTIPLE IMAGES
        if images:
            print(f"{len(images)} images received from frontend")

            for img_base64 in images:
                image_path = save_base64_image(img_base64)

                if image_path and os.path.exists(image_path):
                    extracted_text = extract_text_from_image(image_path)

                    if extracted_text:
                        print(f"OCR extracted: {extracted_text}")
                        all_extracted_text.append(extracted_text)

                    try:
                        os.remove(image_path)
                    except:
                        pass

        # SINGLE IMAGE
        elif single_image:
            print("Single image received")

            image_path = save_base64_image(single_image)

            if image_path and os.path.exists(image_path):
                extracted_text = extract_text_from_image(image_path)

                if extracted_text:
                    print(f"OCR extracted: {extracted_text}")
                    all_extracted_text.append(extracted_text)

                try:
                    os.remove(image_path)
                except:
                    pass

        # COMBINE OCR TEXT
        if all_extracted_text:
            # merge OCR outputs
            user_text = " ".join(all_extracted_text)

            # remove duplicated OCR words
            tokens = user_text.split()
            clean_tokens = []

            for t in tokens:
                if not clean_tokens or clean_tokens[-1] != t:
                    clean_tokens.append(t)

            user_text = " ".join(clean_tokens)

            skip_typo_check = True
            ocr_detected = True

            # reset intent so the system re-evaluates the text
            intent = ""

        lang_code = safe_detect_language(user_text)

        # ✅ if too garbled, ask retype early
        if not ocr_detected and not skip_typo_check and is_incomprehensible(user_text):
            dispatcher.utter_message(text=retype_prompt(lang_code))
            log_unknown_query(user_text, intent_name=intent or "garbled")
            return []

        # 1) nlu_fallback
        if intent == "nlu_fallback":
            clarifier = detect_ambiguity(user_text)

            if clarifier:
                dispatcher.utter_message(text=clarifier)
                log_unknown_query(user_text, intent_name=intent)
                return []

            fallback_responses = [
                "I didn’t quite get that. Can you rephrase your question?",
                "I'm not sure I follow. Could you give more details?",
                "Sorry, I missed that. Could you ask it another way?"
            ]

            dispatcher.utter_message(text=random.choice(fallback_responses))
            log_unknown_query(user_text, intent_name=intent)
            return []

        # 2) MODEL/RULES FIRST (static responses)
        responses = {
            "greet": {
                "en": "Hi there! How can I assist you today?",
                "tl": "Kumusta! Paano kita matutulungan?"
            },
            "ask_help": {
                "en": "Sure! I’m here to help. What do you need assistance with?",
                "tl": "Siyempre! Nandito ako para tumulong. Ano ang kailangan mo ng tulong?"
            },
            "ask_address": {
                "en": "Our school is located at Tincoco St. Campo Santo, Brgy. Poblacion, City of Bacoor, Cavite 4102. Do you need directions as well?",
                "tl": "Ang paaralan ay matatagpuan sa Tincoco St. Campo Santo, Brgy. Poblacion, Bacoor, Cavite 4102. Gusto mo ba ng direksyon papunta roon?"
            },
        }

        text_to_send = responses.get(intent, {}).get(lang_code)

        if is_good_model_answer(text_to_send):
            dispatcher.utter_message(text=text_to_send)
            return []

        # 3) MySQL about topics (mission/vision/core/mandate)
        intent_to_topic = {
            "ask_mission_vision": "mission_vision",
            "ask_core_values": "core_values",
            "ask_mandate": "mandate",
        }
        topic = intent_to_topic.get(intent)

        if topic in ("mission_vision", "core_values", "mandate"):
            # ✅ FIX 2: guard FIRST (before DB fetch)
            if not is_admin_query(user_text):
                dispatcher.utter_message(
                    text="I can help with school-related questions (enrollment, announcements, contact info, etc.). What would you like to ask?"
                )
                return []

            try:
                content = fetch_about_topic_from_db(topic, user_text=user_text)
                if content:
                    dispatcher.utter_message(text=content)
                    return []

                fallback_payload = handle_resources_intents("ask_about", user_text)

                if isinstance(fallback_payload, dict):
                    if "items" in fallback_payload:
                        header = fallback_payload.get("text", "")
                        if header:
                            dispatcher.utter_message(text=header)
                        for item in fallback_payload["items"]:
                            dispatcher.utter_message(text=item["text"])
                            for img in (item.get("images") or [])[:1]:
                                dispatcher.utter_message(image=img)
                        return []
                    dispatcher.utter_message(text=fallback_payload.get("text", ""))
                    for img in (fallback_payload.get("images") or [])[:1]:
                        dispatcher.utter_message(image=img)
                elif isinstance(fallback_payload, str) and fallback_payload:
                    dispatcher.utter_message(text=fallback_payload)
                else:
                    dispatcher.utter_message(text="I couldn’t find that information yet.")
                return []
            except Exception:
                dispatcher.utter_message(
                    text="I can’t access the school information right now. Please try again later."
                )
                return []

        # 4) RESOURCES (only for admin-content intents)
        resource_intents = {"ask_news", "ask_about", "ask_contact", "ask_resources"}
        if intent in resource_intents:
            # ✅ FIX 1: guard BEFORE resources fetch
            if not is_admin_query(user_text):
                dispatcher.utter_message(
                    text="I can help with school-related questions (enrollment, announcements, contact info, etc.). What would you like to ask?"
                )
                return []

            resource_payload = handle_resources_intents(intent, user_text)

            if isinstance(resource_payload, dict):
                if "items" in resource_payload:
                    header = resource_payload.get("text", "")
                    if header:
                        dispatcher.utter_message(text=header)
                    for item in resource_payload["items"]:
                        dispatcher.utter_message(text=item["text"])
                        for img in (item.get("images") or [])[:1]:
                            dispatcher.utter_message(image=img)
                    return []

                dispatcher.utter_message(text=resource_payload.get("text", ""))
                for img in (resource_payload.get("images") or [])[:1]:
                    dispatcher.utter_message(image=img)
                return []

            elif isinstance(resource_payload, str) and resource_payload:
                dispatcher.utter_message(text=resource_payload)
                return []

            dispatcher.utter_message(text="I can’t access the resources service right now. Please try again later.")
            return []

        # 5) Ambiguity -> generic fallback
        clarifier = detect_ambiguity(user_text)
        if clarifier:
            dispatcher.utter_message(text=clarifier)
            log_unknown_query(user_text, intent_name=intent)
            return []

        fallback_responses = {
            "en": [
                "I didn’t quite get that. Can you give me a bit more detail?",
                "I’m not sure I follow. Can you rephrase your question?",
                "Sorry, I missed that. Can you rephrase your question?"
            ],
            "tl": [
                "Medyo hindi ko naintindihan. Puwede mo bang dagdagan ng kaunting detalye?",
                "Parang hindi ko masundan. Puwede mo bang sabihin sa ibang paraan?"
            ]
        }

        text_to_send = random.choice(fallback_responses.get(lang_code, fallback_responses["en"]))
        dispatcher.utter_message(text=text_to_send)
        log_unknown_query(user_text, intent_name=intent)
        return []