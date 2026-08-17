"""Minimal 2DA reader — just enough to turn a row index into a readable label.

We deliberately read the Label column rather than the Name column: Name is a TLK
StrRef, which would drag the whole custom-TLK resolution chain into a script that
otherwise needs nothing but the repo. Labels are ASCII and stable
("Barbarian", "DEVASTATING_CRITICAL_LANCE"), which is all the Hall of Fame prints.
"""

from __future__ import annotations

import re
from pathlib import Path


def read_labels(path: Path) -> dict[int, str]:
    """Map row id -> Label for a 2DA file.

    Row 0 of the file is "2DA V2.0", row 1 is blank, row 2 is the header. Data rows
    start at index 3 and lead with their own row id, which is what we key on rather
    than the enumeration order — a 2DA may legally skip ids.
    """
    labels: dict[int, str] = {}
    try:
        lines = path.read_text(encoding="latin-1").splitlines()
    except OSError:
        return labels

    for line in lines[3:]:
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            row_id = int(parts[0])
        except ValueError:
            continue
        label = parts[1]
        if label == "****":
            continue
        labels[row_id] = label
    return labels


# Labels whose mechanical prettification reads wrong ("Champion Torm",
# "Weaponmaster"). Keyed by the raw 2DA label.
OVERRIDES = {
    "Champion_Torm": "Champion of Torm",
    "WeaponMaster": "Weapon Master",
    "Pale_Master": "Pale Master",
    "Dwarven_Defender": "Dwarven Defender",
    "Dragon_Disciple": "Dragon Disciple",
    "Arcane_Archer": "Arcane Archer",
    "Purple_Dragon_Knight": "Purple Dragon Knight",
    "HalfElf": "Half-Elf",
    "HalfOrc": "Half-Orc",
}


def prettify(label: str) -> str:
    """A 2DA label as a human would write it.

    Handles the three shapes the tables actually use: already-readable
    ("Barbarian"), underscore-separated ("Pale_Master") and camelCase
    ("WeaponMaster"), with an override table for the handful that still read wrong.
    """
    if label in OVERRIDES:
        return OVERRIDES[label]
    if "_" in label:
        return " ".join(w.capitalize() for w in label.split("_"))
    if label.isupper():
        return label.capitalize()
    # camelCase -> spaced, leaving single-word labels untouched.
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", label)
