from __future__ import annotations

from gradmarket.classify.seniority import classify_seniority

# --- rule 1: recruiter/recruiting/talent acquisition titles, checked first ---


def test_rule1_recruiter():
    assert classify_seniority("Recruiter", "") == "experienced"


def test_rule1_talent_acquisition():
    assert classify_seniority("Talent Acquisition Partner", "") == "experienced"


def test_rule1_campus_recruiter_beats_rule3_campus():
    # Contains both a rule-1 signal (recruiter) and what would be a rule-3
    # early signal (campus) — rule 1 is checked first and wins.
    assert classify_seniority("Campus Recruiter", "") == "experienced"


def test_rule1_head_of_early_career_recruiting_beats_rule3_early_career():
    assert classify_seniority("Head of Early Career Recruiting", "") == "experienced"


# --- rule 2: senior-type titles ---


def test_rule2_senior():
    assert classify_seniority("Senior Software Engineer", "") == "experienced"


def test_rule2_staff():
    assert classify_seniority("Staff Engineer", "") == "experienced"


def test_rule2_principal():
    assert classify_seniority("Principal Engineer", "") == "experienced"


def test_rule2_lead():
    assert classify_seniority("Lead Developer", "") == "experienced"


def test_rule2_head_of():
    assert classify_seniority("Head of Engineering", "") == "experienced"


def test_rule2_director():
    assert classify_seniority("Director of Product", "") == "experienced"


def test_rule2_manager():
    assert classify_seniority("Account Manager", "") == "experienced"


def test_rule2_vp():
    assert classify_seniority("VP of Sales", "") == "experienced"


def test_rule2_chief():
    assert classify_seniority("Chief of Staff", "") == "experienced"


def test_rule2_sr_abbreviation_with_period():
    assert classify_seniority("Sr. Software Engineer", "") == "experienced"


def test_rule2_sr_abbreviation_without_period():
    assert classify_seniority("Sr Software Engineer", "") == "experienced"


def test_rule2_executive():
    assert classify_seniority("Account Executive", "") == "experienced"


def test_rule2_owner():
    assert classify_seniority("Product Owner", "") == "experienced"


def test_rule2_specialist():
    assert classify_seniority("Payroll Specialist", "") == "experienced"


# --- rule 3: early-career titles ---


def test_rule3_graduate():
    assert classify_seniority("Graduate Software Engineer", "") == "early"


def test_rule3_intern():
    assert classify_seniority("Summer Intern", "") == "early"


def test_rule3_internship():
    assert classify_seniority("Marketing Internship", "") == "early"


def test_rule3_junior():
    assert classify_seniority("Junior Developer", "") == "early"


def test_rule3_campus():
    assert classify_seniority("Campus Hire", "") == "early"


def test_rule3_placement():
    assert classify_seniority("Industrial Placement", "") == "early"


def test_rule3_trainee():
    assert classify_seniority("Graduate Trainee", "") == "early"


def test_rule3_apprentice():
    assert classify_seniority("Software Apprentice", "") == "early"


def test_rule3_new_grad():
    assert classify_seniority("New Grad Software Engineer", "") == "early"


def test_rule3_summer_analyst():
    assert classify_seniority("Summer Analyst", "") == "early"


def test_rule3_early_career():
    assert classify_seniority("Early Career Software Engineer", "") == "early"


def test_rule3_entry_level():
    assert classify_seniority("Entry Level Analyst", "") == "early"


def test_rule3_working_student():
    assert classify_seniority("Working Student, Data", "") == "early"


def test_early_title_beats_description_experience_floor():
    # Title rules always take precedence over description rules, regardless
    # of which direction the conflict goes.
    assert classify_seniority("Graduate Software Engineer", "5+ years of experience required") == "early"


# --- rule 4: description states an experience floor above zero ---


def test_rule4_three_plus_years():
    assert classify_seniority("Software Engineer", "Requires 3+ years of experience") == "experienced"


def test_rule4_five_plus_years():
    assert classify_seniority("Software Engineer", "5+ years experience") == "experienced"


def test_rule4_two_plus_years():
    assert classify_seniority("Software Engineer", "2+ years") == "experienced"


def test_rule4_years_in_a_role_phrasing():
    assert classify_seniority("Software Engineer", "5+ years in a similar role required") == "experienced"


def test_rule4_zero_plus_years_is_not_a_floor():
    # 0 is not a floor "above zero" — falls through past rule 4. Also
    # doesn't match rule 5's "0-N years" (hyphen) pattern, since this is
    # "0+" (plus), so it lands on the rule 6 catch-all.
    assert classify_seniority("Software Engineer", "0+ years of experience welcome") == "experienced"


# --- rule 5: description gives positive early evidence ---


def test_rule5_zero_to_n_years():
    assert classify_seniority("Software Engineer", "0-2 years of experience") == "early"


def test_rule5_new_grads():
    assert classify_seniority("Software Engineer", "Open to new grads") == "early"


def test_rule5_no_prior_experience():
    assert classify_seniority("Software Engineer", "No prior experience required") == "early"


def test_rule5_final_year():
    assert classify_seniority("Software Engineer", "For final year students") == "early"


def test_rule5_penultimate_year():
    assert classify_seniority("Software Engineer", "Open to penultimate year students") == "early"


def test_rule5_graduating_in_year():
    assert classify_seniority("Software Engineer", "Graduating in 2026") == "early"


def test_rule5_graduating_in_month_year():
    assert classify_seniority("Software Engineer", "Graduating in Spring 2026") == "early"


def test_rule5_cpt():
    assert classify_seniority("Software Engineer", "CPT accepted") == "early"


def test_rule5_opt():
    assert classify_seniority("Software Engineer", "OPT accepted") == "early"


def test_rule5_students_eligible():
    assert classify_seniority("Software Engineer", "Students eligible to apply") == "early"


# --- rule 6: catch-all ---


def test_rule6_no_evidence_defaults_to_experienced():
    assert classify_seniority("Software Engineer", "Join our team and build great things.") == "experienced"


def test_rule6_title_only_no_signal_defaults_to_experienced():
    assert classify_seniority("Software Engineer", "") == "experienced"


# --- unknown: nothing at all to go on ---


def test_no_title_or_description_is_unknown():
    assert classify_seniority(None, None) == "unknown"
    assert classify_seniority("", "") == "unknown"


# --- precedence: title rules beat description rules in either direction ---


def test_senior_title_beats_early_description_evidence():
    assert classify_seniority("Senior Engineer", "0-2 years of experience") == "experienced"
