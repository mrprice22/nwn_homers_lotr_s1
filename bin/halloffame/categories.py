"""The curated tables — this is the file you hand-tune.

Everything else in the package is mechanical. These tables encode judgement calls
that no amount of data mining can make for you: which monsters count as "spiders",
which items count as "rocks", and which hand-typed roadmap name belongs to which
account. Edit here, re-run, look at the concentration report, repeat.

Why the slayer categories cannot be derived: ``bestiarydb.kills`` records a resref
and nothing else, and the ``race_id`` in ``creature_index.json`` is the NWN racial
type — which lumps spiders, bats and stirges into one "Animal"/"Vermin" bucket and
would give every slayer award to the same handful of players. Matching on the
resolved creature *name* is cruder but produces the categories a player would
actually recognise.
"""

from __future__ import annotations

import re

# --------------------------------------------------------------------------- #
# Slayer categories: award title -> regex matched against the creature name
# (falling back to its resref). Order matters only for the "uncategorised"
# report; a creature may legitimately match more than one category.
# --------------------------------------------------------------------------- #

SLAYER_CATEGORIES: list[tuple[str, str, str]] = [
    # (award id,        display title,      name regex)
    ("spider",  "Spider Slayer",  r"\bspider(s|lings?)?\b|shelob|arachn"),
    ("orc",     "Orc Slayer",     r"\borcs?\b|uruk|\borcish\b"),
    ("goblin",  "Goblin Slayer",  r"goblin"),
    ("bat",     "Bat Slayer",     r"\bbats?\b|stirge"),
    ("wizard",  "Wizard Slayer",  r"\b(wizard|mage|magi|sorcere?r|warlock|necromancer|enchanter)s?\b"),
    ("hobbit",  "Hobbit Slayer",  r"\b(hobbits?|halflings?)\b"),
    # "Green Dragon Inn Keeper" is a barman, not a dragon -- hence the negative
    # lookahead. Nobody killed a real dragon in season 1, so this award is expected
    # to come up empty; the dragons show up under The Undefeated instead.
    ("dragon",  "Dragon Slayer",  r"\b(dragons?|wyrms?|drakes?)\b(?!\s+inn)"),
    ("troll",   "Troll Slayer",   r"\btroll"),
    ("golem",   "Golem Slayer",   r"\bgolems?\b|\bconstruct"),
    ("wolf",    "Wolf Slayer",    r"\b(worgs?|wargs?|wolf|wolves|hounds?)\b"),
    ("undead",  "Bane of the Undead",
     r"\b(zombie|skeleton|skeletal|wight|ghoul|ghast|wraith|lich|ghost|spectre|specter|revenant|mummy|barrow)"),
]

# --------------------------------------------------------------------------- #
# Collector categories. Matched against an inventory item's name, then tag, then
# resref. The exclusions exist because "Ruby Crossbow" is a weapon and "Scale
# Mail" is not a drink -- both were real false positives on the first pass.
# --------------------------------------------------------------------------- #

_GEAR = r"bow|crossbow|hammer|sword|blade|axe|mail|armou?r|shield|helm|dagger|spear|staff|robe|cloak|boots|gloves"

COLLECTIONS: list[tuple[str, str, str, str]] = [
    # (award id,   display title,      include regex,   exclude regex)
    ("gems", "Rock Collector",
     r"\b(gems?|diamond|ruby|emerald|sapphire|topaz|opal|garnet|amethyst|jade|pearl"
     r"|quartz|beljuril|agate|aquamarine|greenstone|malachite|obsidian|alexandrite"
     r"|ingot|nugget|ore)\b",
     r"gem of|gem pouch|\bdye\b|scroll|potion|wand|of power|" + _GEAR),
    ("booze", "Alcohol Collector",
     r"\b(wine|ales?|beer|mead|brandy|whisky|whiskey|rum|grog|liquor|cider|lager"
     r"|booze|brew|vintage|tankard|flagon|keg|stein|spirits)\b",
     r"empty|life drinker|adjath|" + _GEAR),
]

# --------------------------------------------------------------------------- #
# roadmap.yaml `player:` -> account name as it appears in the session logs.
#
# These are hand-typed display strings and match nothing automatically. An entry
# missing from this table is REPORTED, not guessed -- an unmapped suggester would
# otherwise silently lose their merit. `HomelessSon (Server Admin)` maps to the
# admin account and is excluded from every roadmap-derived award (that's you).
# --------------------------------------------------------------------------- #

ROADMAP_ALIASES: dict[str, str] = {
    "HomelessSon (Server Admin)": "HomelessSon",
    "-Methonash-":                "-Methonash-",
    "Tukwut":                     "Tukwut",
    "Piskan (Alek Cain)":         "Alek Cain",
    "Piskan (Alec Cain)":         "Alek Cain",   # spelling variant in the s1 repo
    "dc0960 (Dungeon_Crawler)":   "Dungeon_Crawler",
    "Y a z k i r":                "y a z k i r",
    "Rajmund (Ray)":              "ray",
    "Fugdish (Try_this)":         "Try_This",
    "Zambro (Xil)":               "Xil",
    "Szescian82":                 "szescian82",
    "Llikanthus":                 "Llikanthus",
    "McGondy":                    "McGondy",
    "FLYING HITCHER":             "flyinghitcher",
    "Magyk":                      "Magyk",
}

# The admin is excluded from every award: he builds the module.
ADMIN_ACCOUNTS = {"HomelessSon"}

# --------------------------------------------------------------------------- #
# Accounts the same human played under more than one CD key. Merging is a
# judgement call (two keys could be two people), so nothing is merged unless it
# is listed here -- keyed by the CD key to fold away, valued by the one to keep.
# --------------------------------------------------------------------------- #

ACCOUNT_MERGES: dict[str, str] = {
    # "UXRXYPWP": "UPWUAWVG",  # yazkir3.0 -> y a z k i r  (unconfirmed; ask first)
}

# --------------------------------------------------------------------------- #
# Class groupings for the caster awards. Ids are classes.2da rows.
# --------------------------------------------------------------------------- #

ARCANE_CLASSES = {1, 9, 10, 41}      # Bard, Sorcerer, Wizard, Palemaster
DIVINE_CLASSES = {2, 3, 6, 27}       # Cleric, Druid, Paladin, Blackguard

# Skills that get their own award: skill id -> (award id, display title).
SKILL_AWARDS: dict[int, tuple[str, str]] = {
    13: ("kleptomaniac", "Kleptomaniac"),        # Pick Pocket
    3:  ("disciplined", "The Disciplined"),      # Discipline
    4:  ("medic", "Field Medic"),                # Heal
    6:  ("listener", "Great Listener"),          # Listen
    7:  ("loremaster_skill", "Keeper of Lore"),  # Lore
    19: ("umd", "Meddler with Magic"),           # Use Magic Device
    21: ("tumbler", "The Acrobat"),              # Tumble
    12: ("persuader", "Silver Tongue"),          # Persuade
}

# Ability awards: field -> (award id, title for the highest, title for the lowest
# average, or None when there is no "worst" award for that ability).
ABILITY_AWARDS: dict[str, tuple[str, str, str | None]] = {
    "Str": ("strongest",  "The Mightiest",   None),
    "Dex": ("flexible",   "Most Flexible",   None),
    "Con": ("toughest",   "The Hardiest",    None),
    "Int": ("smartest",   "The Most Learned", None),
    "Wis": ("wisest",     "The Wisest",      None),
    "Cha": ("charming",   "The Most Beloved", "Ugliest"),
}


def compile_all() -> None:
    """Fail fast on a typo in any regex above, at import time rather than mid-run."""
    for _, _, pat in SLAYER_CATEGORIES:
        re.compile(pat, re.I)
    for _, _, inc, exc in COLLECTIONS:
        re.compile(inc, re.I)
        re.compile(exc, re.I)


compile_all()
