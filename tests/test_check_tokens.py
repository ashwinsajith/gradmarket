"""scripts/check_tokens.py isn't part of the installed package, so it's
imported here by adding scripts/ to sys.path rather than via a normal
package import — mirrors how the script itself is run directly.
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import check_tokens


def test_case_only_name_difference_is_not_evidence_of_real():
    # "differs from the token by more than case" — same string modulo case
    # doesn't count as evidence on its own.
    assert check_tokens.workable_likely_shell("acme", "ACME", None) is True


def test_name_differing_by_more_than_case_clears_it():
    assert check_tokens.workable_likely_shell("acme", "Acme Corporation", None) is False


def test_missing_or_empty_description_is_not_evidence_of_real():
    assert check_tokens.workable_likely_shell("acme", None, None) is True
    assert check_tokens.workable_likely_shell("acme", None, "") is True
    assert check_tokens.workable_likely_shell("acme", None, "   ") is True


def test_present_description_clears_it_even_with_case_only_name():
    assert check_tokens.workable_likely_shell("acme", "ACME", "<p>We build things.</p>") is False


def test_both_fail_is_likely_shell():
    # e.g. buffer/intercom/zapier/gitlab — token-identical name, no description.
    assert check_tokens.workable_likely_shell("buffer", "buffer", "") is True
    assert check_tokens.workable_likely_shell("buffer", "Buffer", None) is True


def test_both_pass_is_not_likely_shell():
    assert check_tokens.workable_likely_shell("buffer", "Buffer Inc", "<p>Real company.</p>") is False
