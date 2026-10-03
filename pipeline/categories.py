"""How FBI NIBRS offense codes map to the dashboard's categories and severity tiers.

This table is a judgment call, and it is the one place to change it. The grouping is built around
what a resident would ask ("is it violent? is it my car? is it my home?") rather than the FBI's
Persons / Property / Society split.
"""
from __future__ import annotations

# (id, label), in display order.
CATEGORIES: list[tuple[str, str]] = [
    ("violent", "Violent & sex crimes"),
    ("assault", "Simple assault & threats"),
    ("burglary", "Burglary"),
    ("vehicle", "Vehicle theft & break-ins"),
    ("theft", "Other theft"),
    ("vandalism", "Vandalism & arson"),
    ("fraud", "Fraud & forgery"),
    ("order", "Drugs, weapons & public order"),
]

# Records that are not crime reports. They are left out of every count.
ADMIN = "admin"

# (id, label, what it covers), most serious first.
SEVERITIES: list[tuple[str, str, str]] = [
    ("high", "High", "Serious violence: homicide, rape and other forcible sex offenses, robbery, "
                     "aggravated assault, kidnapping."),
    ("medium", "Medium", "A direct threat to a person, home or car: simple assault, threats, burglary, "
                         "vehicle theft, arson, weapons offenses."),
    ("low", "Low", "Property loss and nuisance: theft, car break-ins, vandalism, fraud, drugs, "
                   "disorderly conduct."),
]

# NIBRS offense code -> (label, category, severity)
OFFENSES: dict[str, tuple[str, str, str]] = {
    "09A": ("Murder", "violent", "high"),
    "09B": ("Negligent manslaughter", "violent", "high"),
    "09C": ("Justifiable homicide", ADMIN, "low"),
    "100": ("Kidnapping", "violent", "high"),
    "11A": ("Rape", "violent", "high"),
    "11B": ("Forcible sodomy", "violent", "high"),
    "11C": ("Sexual assault with an object", "violent", "high"),
    "11D": ("Forcible fondling", "violent", "high"),
    "120": ("Robbery", "violent", "high"),
    "13A": ("Aggravated assault", "violent", "high"),
    "64A": ("Human trafficking (sex acts)", "violent", "high"),
    "64B": ("Human trafficking (servitude)", "violent", "high"),
    "36A": ("Incest", "violent", "medium"),
    "36B": ("Statutory rape", "violent", "medium"),

    "13B": ("Simple assault", "assault", "medium"),
    "13C": ("Intimidation / threats", "assault", "medium"),

    "220": ("Burglary", "burglary", "medium"),

    "240": ("Vehicle theft", "vehicle", "medium"),
    "23F": ("Theft from a vehicle", "vehicle", "low"),
    "23G": ("Theft of vehicle parts", "vehicle", "low"),

    "23A": ("Pocket-picking", "theft", "low"),
    "23B": ("Purse-snatching", "theft", "medium"),
    "23C": ("Shoplifting", "theft", "low"),
    "23D": ("Theft from a building", "theft", "low"),
    "23E": ("Theft from a coin machine", "theft", "low"),
    "23H": ("Other theft", "theft", "low"),
    "280": ("Stolen property", "theft", "low"),

    "290": ("Vandalism", "vandalism", "low"),
    "200": ("Arson", "vandalism", "medium"),

    "210": ("Extortion / blackmail", "fraud", "medium"),
    "250": ("Counterfeiting / forgery", "fraud", "low"),
    "26A": ("Swindle / false pretenses", "fraud", "low"),
    "26B": ("Credit card / ATM fraud", "fraud", "low"),
    "26C": ("Impersonation", "fraud", "low"),
    "26D": ("Welfare fraud", "fraud", "low"),
    "26E": ("Wire fraud", "fraud", "low"),
    "26F": ("Identity theft", "fraud", "low"),
    "26G": ("Hacking / computer invasion", "fraud", "low"),
    "270": ("Embezzlement", "fraud", "low"),
    "510": ("Bribery", "fraud", "low"),
    "90A": ("Bad checks", "fraud", "low"),

    "520": ("Weapons violation", "order", "medium"),
    "370": ("Pornography / obscene material", "order", "medium"),
    "90H": ("Peeping tom", "order", "medium"),
    "35A": ("Drug violation", "order", "low"),
    "35B": ("Drug equipment violation", "order", "low"),
    "39A": ("Betting / wagering", "order", "low"),
    "39B": ("Promoting gambling", "order", "low"),
    "39C": ("Gambling equipment violation", "order", "low"),
    "39D": ("Sports tampering", "order", "low"),
    "40A": ("Prostitution", "order", "low"),
    "40B": ("Promoting prostitution", "order", "low"),
    "40C": ("Purchasing prostitution", "order", "low"),
    "720": ("Animal cruelty", "order", "low"),
    "90B": ("Curfew / loitering", "order", "low"),
    "90C": ("Disorderly conduct", "order", "low"),
    "90D": ("DUI", "order", "low"),
    "90E": ("Drunkenness", "order", "low"),
    "90F": ("Family offense (nonviolent)", "order", "low"),
    "90G": ("Liquor law violation", "order", "low"),
    "90J": ("Trespassing", "order", "low"),
    "90I": ("Runaway", ADMIN, "low"),
    # 90Z is a catch-all. See ADMIN_SECTION below for the part of it that is not counted.
    "90Z": ("Other offense", "order", "low"),
}

# "All Other Offenses" (90Z) is the single largest code in the data, and most of it is not crime:
# 72-hour mental-health holds, warrant arrests, parole and probation violations, and SDPD's internal
# "ZZ" report types (suicide attempts, missing juveniles, recovered vehicles, miscellaneous reports).
# A 90Z record is treated as administrative when *every* code section on it matches this pattern;
# one real charge alongside (say, a warrant plus resisting arrest) keeps it in the counts.
# The pattern is matched against the upper-cased code section text.
ADMIN_SECTION = (
    r"\bZZ\b"
    r"|MENTAL DISORDER"
    r"|WARRANT"
    r"|VIOLATION PAROLE|PAROLE VIOLATION|PRCS VIOLATION|PROBATION VIOLATION|FLASH INCARCERATION"
    r"|FUGITIVE FROM JUSTICE"
    r"|ORDER OF JUVENILE COURT"
)

CATEGORY_IDS = [c for c, _ in CATEGORIES]
SEVERITY_IDS = [s for s, _, _ in SEVERITIES]
