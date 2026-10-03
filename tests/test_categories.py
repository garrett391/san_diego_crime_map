import re

from pipeline import categories as c


def test_every_offense_maps_to_a_known_category_and_severity():
    for code, (label, category, severity) in c.OFFENSES.items():
        assert label, code
        assert category in c.CATEGORY_IDS or category == c.ADMIN, code
        assert severity in c.SEVERITY_IDS, code


def test_every_category_is_used():
    used = {category for _, category, _ in c.OFFENSES.values()}
    assert set(c.CATEGORY_IDS) <= used


def test_fbi_violent_crimes_are_high_severity():
    for code in ("09A", "11A", "11B", "11C", "120", "13A"):
        assert c.OFFENSES[code][2] == "high", code


def test_admin_pattern_separates_paperwork_from_charges():
    admin = re.compile(c.ADMIN_SECTION)
    paperwork = [
        "5150 WI MENTAL DISORDER 72 HR OBSERVATION",
        "BW-M ZZ MISDEMEANOR BENCH WARRANT (OUR AGENCY)",
        "OW-F ZZ FELONY OTHER AGENCY'S WARRANT",
        "3056 PC VIOLATION PAROLE:FELONY",
        "3455 PC PRCS VIOLATION",
        "1203.2 (A) PC PROBATION VIOLATION:REARREST/REVOKE",
        "930000 ZZ SUICIDE ATTEMPT",
        "911000 ZZ MISSING JUVENILE / RUNAWAY",
        "1551 (A) PC FUGITIVE FROM JUSTICE:WARRANT ARREST (F)",
    ]
    charges = [
        "148 (A)(1) PC OBSTRUCT/RESIST PEACE OFCR/EMER MED TECH (M)",
        "273.6 (A) PC VIOLATE DOMESTIC VIOLENCE COURT ORDER (M)",
        "20002 (A) VC HIT AND RUN:PROP DAMAGE (M)",
        "466 PC POSS BURGLARY TOOLS (M)",
        "54.0110 SDMC UNAUTHORIZED ENCROACHMENTS PROHIBITED (M)",
        "417.4 PC BRANDISHING FIREARM REPLICA (M)",
    ]
    for section in paperwork:
        assert admin.search(section.upper()), section
    for section in charges:
        assert not admin.search(section.upper()), section
