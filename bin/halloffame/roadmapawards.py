"""Awards derived from the roadmap backlog: merit, reports, and the Backlog Hero.

Two files feed this, and they are not interchangeable:

* **``roadmap.yaml`` in this (season 1) repo** — the *shipped* set: 154 ideas, all
  ``awarded``/``implemented``. This is what merit is paid on.
* **``roadmap.yaml`` in the dev repo** — the live forward backlog, which is the only
  place un-implemented ideas exist. That is where Backlog Hero comes from.

Two rules the admin set, both enforced here rather than left to the caller:

1. **Merit is only awarded for implemented ideas.** So merit and the
   defect/exploit/enhancement counts are computed from idea *status*, never from
   ``meritdb.players.bugs/exploits/features`` — those raw counters are all-time and
   count reports that never shipped.
2. **Season 2 went live 2026-08-13.** Anything dated on or after that belongs to
   season 2. ``meritdb`` is a symlink shared by every running season, so its rows
   must be date-filtered or season 2's spending leaks into season 1's trophies.
"""

from __future__ import annotations

import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import yaml

from .awards import rank
from .categories import ADMIN_ACCOUNTS, ROADMAP_ALIASES

SEASON1_END = date(2026, 8, 13)          # season 2 go-live; season 1 is strictly before

SHIPPED_STATUSES = {"awarded", "implemented"}
MERIT_POINTS = {"Defect": 1, "Enhancement": 2, "Exploit": 3}  # mirrors bin/gen-roadmap.py

TYPE_AWARDS = {
    "Defect":      ("defects",      "Chief Bug Hunter",
                    "Most reported defects that were actually fixed and shipped."),
    "Exploit":     ("exploits",     "White Hat",
                    "Most reported exploits that were closed. Finding them is one thing; reporting them is another."),
    "Enhancement": ("enhancements", "Chief Architect",
                    "Most suggested enhancements that made it into the module."),
}


def load_ideas(path: Path) -> list[dict]:
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        print(f"[warn] could not read {path}: {exc}", file=sys.stderr)
        return []
    return [i for i in (data.get("ideas") or []) if not i.get("hidden")]


def _as_date(value) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str) and len(value) >= 10:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _account(label: str, unmapped: set) -> str | None:
    """roadmap `player:` -> account name, recording anything the alias table misses."""
    hit = ROADMAP_ALIASES.get(label)
    if hit is None:
        unmapped.add(label)
    return hit


def build(ctx, s1_roadmap: Path, dev_roadmap: Path, cutoff: date = SEASON1_END) -> list[dict]:
    out = []
    R = ctx.roster
    unmapped: set[str] = set()

    # Account name -> cdkey, so roadmap credit lands on the same account key
    # everything else is ranked by.
    key_of = {R.account(ck): ck for ck in R.all_cdkeys()}

    def to_key(label: str) -> str | None:
        acct = _account(label, unmapped)
        if acct is None or acct in ADMIN_ACCOUNTS:
            return None
        return key_of.get(acct)

    # ---- shipped ideas: merit earned, and the per-type report awards --------- #
    shipped = [
        i for i in load_ideas(s1_roadmap)
        if i.get("status") in SHIPPED_STATUSES
        and (_as_date(i.get("date")) or date.min) < cutoff
    ]

    merit, by_type = Counter(), defaultdict(Counter)
    titles = defaultdict(list)
    for idea in shipped:
        key = to_key(idea.get("player") or "")
        if not key:
            continue
        kind = idea.get("type") or "Enhancement"
        merit[key] += MERIT_POINTS.get(kind, 0)
        by_type[kind][key] += 1
        titles[key].append(idea.get("title") or "")

    out.append(rank(
        "merit_earned", "Most Merit Earned",
        "Merit is only paid on ideas that actually shipped &mdash; a defect is worth 1, "
        "an enhancement 2, an exploit 3.",
        "merit earned",
        {k: (v, f"{len(titles[k])} ideas shipped") for k, v in merit.items()}, R,
    ))

    for kind, (aid, title, blurb) in TYPE_AWARDS.items():
        out.append(rank(f"reports_{aid}", title, blurb, "shipped", dict(by_type[kind]), R))

    # ---- merit spent, from the shared ledger, date-filtered ------------------ #
    spent = Counter()
    for row in ctx.merit_ledger:
        if row["delta"] >= 0:
            continue
        when = _as_date(row["at"])
        if when is None or when >= cutoff:
            continue
        if row["cdkey"]:
            spent[row["cdkey"]] += -row["delta"]
    out.append(rank(
        "merit_spent", "Biggest Spender",
        "Merit cashed in at the merit shop before the season closed. Points are for spending.",
        "merit spent", dict(spent), R,
    ))

    # ---- Backlog Hero: the forward backlog, not the shipped set -------------- #
    open_ideas = [
        i for i in load_ideas(dev_roadmap) if i.get("status") not in SHIPPED_STATUSES
    ]
    backlog = Counter()
    for idea in open_ideas:
        key = to_key(idea.get("player") or "")
        if key:
            backlog[key] += 1
    out.append(rank(
        "backlog_hero", "Backlog Hero",
        "Ideas still queued rather than shipped. This award looks <em>forward</em>: these "
        "suggestions have not earned merit yet, because merit is only paid on what ships.",
        "ideas awaiting implementation", dict(backlog), R,
    ))

    if unmapped:
        print(
            "[roadmap] no alias for these `player:` values (add them to "
            "bin/halloffame/categories.py ROADMAP_ALIASES):",
            file=sys.stderr,
        )
        for label in sorted(unmapped):
            print(f"          {label!r}", file=sys.stderr)

    return [a for a in out if a]
