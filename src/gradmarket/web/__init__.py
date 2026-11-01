"""Public read-only frontend: one page of open UK early-careers roles, plus
the JSON behind it.

Nothing in this package writes to the database, and nothing here ever
selects a description column — job description text is the companies'
copyright (see CLAUDE.md Constraints). queries.PUBLIC_COLUMNS is the
allowlist; widen it only for fields that are ours to publish.
"""
