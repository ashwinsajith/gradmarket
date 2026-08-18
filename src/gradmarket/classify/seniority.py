"""Seniority classifier: pure function, no DB access.

classify_seniority(title, description) -> "early" | "experienced" | "unknown"

Precedence order matters — checked top to bottom, first match wins:
  1. Title: recruiter/recruiting/talent acquisition        -> experienced
  2. Title: senior/staff/principal/lead/head of/director/
     manager/VP/chief/sr./executive/owner/specialist        -> experienced
  3. Title: graduate/intern/internship/junior/campus/
     placement/trainee/apprentice/new grad/summer analyst/
     early career/entry level/working student               -> early
  4. Description: an experience floor above zero
     ("3+ years", "N+ years in a ... role")                  -> experienced
  5. Description: positive early evidence ("0-N years",
     "new grads", "no prior experience", "final year",
     "penultimate year", "graduating in 20XX", CPT/OPT,
     "students eligible")                                    -> early
  6. Otherwise                                                -> experienced
     (absence of evidence is not evidence of early)

Rules 1-3 look at the title only, so e.g. "Campus Recruiter" and "Head of
Early Career Recruiting" hit rule 1 before campus/early-career (rule 3)
ever gets a chance — recruiting the role is itself an experienced job,
regardless of which audience it recruits for.
"""

from __future__ import annotations

import re

RECRUITER_TITLE_PATTERN = re.compile(r"\b(recruiter|recruiting|talent acquisition)\b", re.IGNORECASE)

SENIOR_TITLE_PATTERN = re.compile(
    r"\b(senior|staff|principal|lead|head of|director|manager|vp|chief|executive|owner|specialist)\b"
    r"|\bsr\b\.?",
    re.IGNORECASE,
)

EARLY_TITLE_PATTERN = re.compile(
    r"\b("
    r"graduate|intern|internship|junior|campus|placement|trainee|apprentice|"
    r"new grad|summer analyst|early career|entry level|working student"
    r")\b",
    re.IGNORECASE,
)

# "3+ years", "5+ years of experience", "2+ years in a similar role" — any
# positive integer floor. "0+ years" is deliberately excluded: zero is not a
# floor above zero, so it falls through to the early-evidence check instead.
EXPERIENCE_FLOOR_PATTERN = re.compile(r"\b(\d+)\+\s*years?\b", re.IGNORECASE)

# "0-2 years", "0-1 years of experience"
ZERO_TO_N_YEARS_PATTERN = re.compile(r"\b0\s*-\s*\d+\s*years?\b", re.IGNORECASE)

EARLY_PHRASES_PATTERN = re.compile(
    r"\b(new grads?|no prior experience|final year|penultimate year|cpt|opt|students eligible)\b",
    re.IGNORECASE,
)

# "graduating in 2026", "graduating in Spring 2026"
GRADUATING_YEAR_PATTERN = re.compile(r"\bgraduating\s+in\s+(?:\w+\s+)?20\d{2}\b", re.IGNORECASE)


def _has_experience_floor(description: str) -> bool:
    return any(int(m.group(1)) > 0 for m in EXPERIENCE_FLOOR_PATTERN.finditer(description))


def _has_early_evidence(description: str) -> bool:
    return bool(
        ZERO_TO_N_YEARS_PATTERN.search(description)
        or EARLY_PHRASES_PATTERN.search(description)
        or GRADUATING_YEAR_PATTERN.search(description)
    )


def classify_seniority(title: str | None, description: str | None) -> str:
    title_text = title or ""
    description_text = description or ""

    # Nothing at all to go on — genuinely unknown, not a guessed default.
    # The precedence rules below never produce "unknown" themselves; rule 6
    # is an unconditional catch-all once there's *some* text to check.
    if not title_text and not description_text:
        return "unknown"

    if RECRUITER_TITLE_PATTERN.search(title_text):
        return "experienced"
    if SENIOR_TITLE_PATTERN.search(title_text):
        return "experienced"
    if EARLY_TITLE_PATTERN.search(title_text):
        return "early"
    if _has_experience_floor(description_text):
        return "experienced"
    if _has_early_evidence(description_text):
        return "early"
    return "experienced"
