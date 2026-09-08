"""Seniority classifier: pure function, no DB access.

classify_seniority(title, description) -> "early" | "experienced" | "unknown"

Precedence order matters — checked top to bottom, first match wins:
  1. Title: recruiter/recruiting/talent acquisition        -> experienced
  2. Title contains BOTH an experienced marker (rule 3) and
     an early-careers keyword (rule 4)                      -> early
     e.g. "Junior Software Engineering Manager" — an early
     keyword next to a senior-sounding job function almost
     always means an entry-level role with that function,
     not a senior person.
  3. Title: senior/staff/principal/lead/head of/director/
     manager/VP/chief/sr./executive/owner                   -> experienced
  4. Title: graduate/intern/internship/junior/campus/
     placement/trainee/apprentice/new grad/summer analyst/
     early career/entry level/working student               -> early
  5. Description: an experience floor above zero
     ("3+ years", "N+ years in a ... role")                  -> experienced
  6. Description: positive early evidence ("0-N years",
     "new grads", "no prior experience", "final year",
     "penultimate year", "graduating in 20XX", CPT/OPT,
     "students eligible")                                    -> early

     CPT/OPT matching is deliberately narrower than the other rule-6
     phrases: it's case-sensitive on uppercase CPT/OPT only, and bare OPT
     requires context ("CPT/OPT", "CPT or OPT", "OPT status", "on OPT").
     Lowercase "opt" is ordinary English ("opt in", "opt out", "you may opt
     to ...") and matched constantly in unrelated boilerplate — Faculty's
     entire board (18 open UK postings, several senior) was misclassified
     as early-careers this way because every posting's footer had an
     "opt out of marketing communications" line. Bare CPT doesn't need
     context since it isn't an English word and collision risk is low; bare
     OPT does, since even all-caps "OPT OUT" shows up in all-caps footers.
  7. Otherwise                                                -> experienced
     (absence of evidence is not evidence of early)

Rules 1-4 look at the title only, so e.g. "Campus Recruiter" and "Head of
Early Career Recruiting" hit rule 1 before campus/early-career (rule 4) ever
gets a chance — recruiting the role is itself an experienced job, regardless
of which audience it recruits for. Rule 1 is deliberately NOT part of the
rule 2 conflict check: a recruiting title is experienced even when it also
contains an early-careers keyword.

"Specialist" is deliberately not an experienced marker — it's a role
descriptor ("Data Specialist", "Support Specialist"), not a seniority level,
and shows up at every level from junior to principal. Titles like "Junior
Platform Specialist" or "Trading Infrastructure Specialist – Graduate
Programme" used to be misread as experienced because "specialist" hit rule 3
before "junior"/"graduate" ever got a chance at rule 4.
"""

from __future__ import annotations

import re

RECRUITER_TITLE_PATTERN = re.compile(r"\b(recruiter|recruiting|talent acquisition)\b", re.IGNORECASE)

SENIOR_TITLE_PATTERN = re.compile(
    r"\b(senior|staff|principal|lead|head of|director|manager|vp|chief|executive|owner)\b"
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
    r"\b(new grads?|no prior experience|final year|penultimate year|students eligible)\b",
    re.IGNORECASE,
)

# "graduating in 2026", "graduating in Spring 2026"
GRADUATING_YEAR_PATTERN = re.compile(r"\bgraduating\s+in\s+(?:\w+\s+)?20\d{2}\b", re.IGNORECASE)

# CPT/OPT (Curricular/Optional Practical Training) — US student work-authorization
# terms that show up in eligibility text. Deliberately NOT re.IGNORECASE: lowercase
# "opt" is an ordinary English word ("opt in", "opt out", "you may opt to ..."),
# which matched on marketing-preference boilerplate that has nothing to do with
# seniority. CPT is safe to match bare (uppercase, but on its own — it isn't an
# English word). Bare OPT requires context, since even all-caps prose like
# "OPT OUT of emails" would otherwise still collide.
CPT_OPT_PATTERN = re.compile(
    r"\bCPT\s*/\s*OPT\b"
    r"|\bOPT\s*/\s*CPT\b"
    r"|\bCPT\s+or\s+OPT\b"
    r"|\bOPT\s+or\s+CPT\b"
    r"|\bOPT\s+status\b"
    r"|\bon\s+OPT\b"
    r"|\bCPT\b"
)


def _has_experience_floor(description: str) -> bool:
    return any(int(m.group(1)) > 0 for m in EXPERIENCE_FLOOR_PATTERN.finditer(description))


def _has_early_evidence(description: str) -> bool:
    return bool(
        ZERO_TO_N_YEARS_PATTERN.search(description)
        or EARLY_PHRASES_PATTERN.search(description)
        or GRADUATING_YEAR_PATTERN.search(description)
        or CPT_OPT_PATTERN.search(description)
    )


def classify_seniority(title: str | None, description: str | None) -> str:
    title_text = title or ""
    description_text = description or ""

    # Nothing at all to go on — genuinely unknown, not a guessed default.
    # The precedence rules below never produce "unknown" themselves; rule 7
    # is an unconditional catch-all once there's *some* text to check.
    if not title_text and not description_text:
        return "unknown"

    if RECRUITER_TITLE_PATTERN.search(title_text):
        return "experienced"

    has_experienced_marker = bool(SENIOR_TITLE_PATTERN.search(title_text))
    has_early_marker = bool(EARLY_TITLE_PATTERN.search(title_text))

    if has_experienced_marker and has_early_marker:
        return "early"
    if has_experienced_marker:
        return "experienced"
    if has_early_marker:
        return "early"

    if _has_experience_floor(description_text):
        return "experienced"
    if _has_early_evidence(description_text):
        return "early"
    return "experienced"
