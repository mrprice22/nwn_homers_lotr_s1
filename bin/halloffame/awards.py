"""One function per award, all returning the same shape.

    {"id", "title", "blurb", "metric", "winners": [...], "ranked": [...]}

``winners`` holds every account tied for first place. The house rule is one winner
per award, ties listed in full — never a top-3 podium, and never any balancing:
if one player sweeps, that is the truth, and the concentration report on stderr is
the signal to add or cut *categories*, not to fudge a result.

The admin account is excluded from every award (see ``categories.ADMIN_ACCOUNTS``);
he builds the module, and his test characters carry impossible numbers.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from .categories import (
    ABILITY_AWARDS, ADMIN_ACCOUNTS, ARCANE_CLASSES, COLLECTIONS, DIVINE_CLASSES,
    SKILL_AWARDS, SLAYER_CATEGORIES,
)


# Awards that computed but found nobody, collected for the stderr report.
EMPTY_AWARDS: list[tuple[str, str]] = []


# --------------------------------------------------------------------------- #
# The generic ranking helper every award goes through
# --------------------------------------------------------------------------- #

def rank(aid, title, blurb, metric, scores, roster, *, lowest=False,
         minimum=1, fmt=None) -> dict | None:
    """Turn {cdkey: value} or {cdkey: (value, detail)} into a finished award.

    ``minimum`` drops accounts that did not really compete (a zero, a single
    stray kill) so a thin category does not crown someone on noise. Returns None
    when nobody clears the bar — the renderer skips the card entirely rather than
    printing an empty trophy.
    """
    rows = []
    for cdkey, raw in scores.items():
        value, detail = raw if isinstance(raw, tuple) else (raw, "")
        account = roster.account(cdkey)
        if account in ADMIN_ACCOUNTS:
            continue
        if value is None:
            continue
        if not lowest and value < minimum:
            continue
        rows.append({"player": account, "value": value, "detail": detail})

    if not rows:
        # Reported rather than silently skipped: an award with no entrants usually
        # means a category matched nothing (a curated regex that needs widening),
        # not that the award is a bad idea.
        EMPTY_AWARDS.append((aid, title))
        return None

    rows.sort(key=lambda r: r["value"], reverse=not lowest)
    best = rows[0]["value"]
    winners = [r for r in rows if r["value"] == best]

    render = fmt or (lambda v: f"{v:,}" if isinstance(v, int) else f"{v:,.1f}")
    for r in rows:
        r["display"] = render(r["value"])

    return {
        "id": aid, "title": title, "blurb": blurb, "metric": metric,
        "winners": winners, "ranked": rows[:10], "entrants": len(rows),
    }


def _per_account(chars, key_fn, roster):
    """Sum a per-character number up to the account that owns the character."""
    out: dict[str, int] = defaultdict(int)
    for c in chars:
        out[c["cdkey"]] += key_fn(c) or 0
    return out


def _best_char(chars, key_fn):
    """Per account, the single best character: {cdkey: (value, char name)}."""
    out: dict[str, tuple[int, str]] = {}
    for c in chars:
        v = key_fn(c) or 0
        if c["cdkey"] not in out or v > out[c["cdkey"]][0]:
            out[c["cdkey"]] = (v, c["name"])
    return out


# --------------------------------------------------------------------------- #
# Conquest — kills, bosses, server firsts
# --------------------------------------------------------------------------- #

def conquest(ctx) -> list[dict]:
    out = []
    R = ctx.roster

    firsts = Counter()
    for f in ctx.server_firsts:
        if f["cdkey"]:
            firsts[f["cdkey"]] += 1
    out.append(rank(
        "server_first", "Server-First Champion",
        "First to fell a creature nobody had ever killed on this server. "
        "The season's true pioneers.",
        "server firsts", dict(firsts), R,
    ))

    boss_solo, boss_party, boss_named = Counter(), Counter(), {}
    for k in ctx.kills:
        if k["resref"] not in ctx.bosses:
            continue
        boss_solo[k["cdkey"]] += k["solo"]
        boss_party[k["cdkey"]] += k["party"]
        if k["solo"]:
            boss_named.setdefault(k["cdkey"], ctx.bosses[k["resref"]]["name"])
    out.append(rank(
        "boss_solo", "Top Boss Slayer",
        "Named bosses from the Roll of the Fallen, killed alone. No party, no help.",
        "solo boss kills",
        {c: (v, boss_named.get(c, "")) for c, v in boss_solo.items()}, R,
    ))
    out.append(rank(
        "boss_party", "Party Person",
        "Bosses brought down alongside others. Fellowship is its own kind of strength.",
        "boss kills in a party", dict(boss_party), R,
    ))

    # Alignment of the boss decides which of these two you win.
    good_kills, evil_kills = Counter(), Counter()
    for k in ctx.kills:
        if k["resref"] not in ctx.bosses:
            continue
        ge = ctx.boss_alignment.get(k["resref"])
        if ge is None:
            continue
        total = k["solo"] + k["party"]
        if ge >= 60:
            good_kills[k["cdkey"]] += total
        elif ge <= 40:
            evil_kills[k["cdkey"]] += total
    out.append(rank(
        "guardian_light", "Guardian of Light",
        "Most evil bosses put down. The free peoples sleep easier.",
        "evil bosses slain", dict(evil_kills), R,
    ))
    out.append(rank(
        "servant_dark", "Servant of Darkness",
        "Most <em>good</em> bosses slain. Somebody had to ask why.",
        "good bosses slain", dict(good_kills), R,
    ))

    # Bestiary breadth and depth.
    species = defaultdict(set)
    per_species = defaultdict(Counter)
    for k in ctx.kills:
        species[k["cdkey"]].add(k["resref"])
        per_species[k["cdkey"]][k["resref"]] += k["solo"] + k["party"]

    total_species = max(len(ctx.catalogue), 1)
    out.append(rank(
        "bestiary", "Bestiary Completion",
        f"The widest slice of the {total_species:,}-creature bestiary recorded by any one player.",
        "% of the bestiary",
        {c: (round(100.0 * len(s) / total_species, 1), f"{len(s):,} species")
         for c, s in species.items()}, R,
        minimum=0.1, fmt=lambda v: f"{v}%",
    ))
    out.append(rank(
        "loremaster", "Loremaster",
        "Most <em>distinct</em> creatures killed &mdash; breadth of experience rather than raw volume.",
        "distinct species", {c: len(s) for c, s in species.items()}, R,
    ))

    nemesis = {}
    for c, counter in per_species.items():
        resref, n = counter.most_common(1)[0]
        nemesis[c] = (n, ctx.creature_name(resref))
    out.append(rank(
        "nemesis", "Nemesis",
        "The single creature a player killed more than any other. A grudge, measured.",
        "kills of one creature", nemesis, R, minimum=25,
    ))

    # Slayer categories, from the curated table.
    for aid, title, pattern in SLAYER_CATEGORIES:
        rx = re.compile(pattern, re.I)
        matched = {r for r in ctx.all_resrefs if rx.search(ctx.creature_name(r))}
        if not matched:
            continue
        tally = Counter()
        for k in ctx.kills:
            if k["resref"] in matched:
                tally[k["cdkey"]] += k["solo"] + k["party"]
        out.append(rank(
            f"slayer_{aid}", title,
            f"Most kills across every creature the bestiary counts as {title.split()[0].lower()}-kind "
            f"({len(matched)} species).",
            "kills", dict(tally), R, minimum=10,
        ))

    return [a for a in out if a]


# --------------------------------------------------------------------------- #
# Fortune — gold, XP, hoards
# --------------------------------------------------------------------------- #

def fortune(ctx) -> list[dict]:
    out = []
    R = ctx.roster

    carried = _per_account(ctx.chars, lambda c: c["gold"], R)
    gold = defaultdict(int, carried)
    for cdkey, amount in ctx.bank_personal.items():
        gold[cdkey] += amount
    for cdkey, amount in ctx.bank_family_gp.items():
        gold[cdkey] += amount
    out.append(rank(
        "gold", "Gold Hoarder",
        "Every coin a player owns: carried, personally banked, and sitting in the family vault.",
        "total gold", dict(gold), R, minimum=1000,
    ))

    xp = defaultdict(int, _per_account(ctx.chars, lambda c: c["xp"], R))
    for cdkey, amount in ctx.bank_family_xp.items():
        xp[cdkey] += amount
    out.append(rank(
        "xp", "Top XP Earner",
        "All experience earned across every character, plus whatever is banked in the family account.",
        "total XP", dict(xp), R, minimum=1000,
    ))

    family = defaultdict(int)
    for src in (ctx.bank_family_gp, ctx.bank_family_xp):
        for cdkey, amount in src.items():
            family[cdkey] += amount
    out.append(rank(
        "family_fortune", "Family Fortune",
        "The largest shared family vault &mdash; gold and experience pooled across a whole household of characters.",
        "banked in the family vault", dict(family), R, minimum=1000,
    ))

    # Comedy: rich in experience, poor in pocket. Rank by XP per gold piece.
    broke = {}
    for cdkey in set(xp) | set(gold):
        x, g = xp.get(cdkey, 0), gold.get(cdkey, 0)
        if x < 1_000_000:
            continue
        broke[cdkey] = (round(x / max(g, 1)), f"{x:,} XP on {g:,} gp")
    out.append(rank(
        "broke", "Spent It All",
        "The most experience per surviving gold piece. Adventure is expensive.",
        "XP per gold piece", broke, R,
    ))

    out.append(rank(
        "packrat", "Pack Rat",
        "Most items hauled around across every character. Nothing is ever left behind.",
        "items carried",
        _per_account(ctx.chars, lambda c: len(c["items"]), R), R, minimum=10,
    ))

    for aid, title, include, exclude in COLLECTIONS:
        inc, exc = re.compile(include, re.I), re.compile(exclude, re.I)

        def counts(c, inc=inc, exc=exc):
            n = 0
            for item in c["items"]:
                text = f"{item['name']} {item['tag']} {item['resref']}"
                if inc.search(text) and not exc.search(text):
                    n += max(item["stack"], 1)
            return n

        out.append(rank(
            f"collect_{aid}", title,
            "Most of them stashed away across a whole roster of characters.",
            "carried", _per_account(ctx.chars, counts, R), R,
        ))

    if ctx.houses:
        out.append(rank(
            "house", "Lord of the Manor",
            "The largest player home on the server, measured by the floor area of the granted estate.",
            "tiles", ctx.house_sizes, R,
        ))

    return [a for a in out if a]


# --------------------------------------------------------------------------- #
# Devotion — time, merit, ideas, meaningwave
# --------------------------------------------------------------------------- #

def devotion(ctx) -> list[dict]:
    out = []
    R = ctx.roster

    minutes, days = defaultdict(float), defaultdict(set)
    for s in ctx.sessions:
        cdkey = R.key_for_session(s)
        if not cdkey:
            continue
        minutes[cdkey] += s.get("duration_min") or 0
        if s.get("join"):
            days[cdkey].add(s["join"][:10])
    out.append(rank(
        "playtime", "Most Time Played",
        "Hours logged in over the whole season, reconstructed from every server log we still hold.",
        "hours played",
        {c: (round(m / 60.0, 1), f"{len(days[c])} days") for c, m in minutes.items()},
        R, minimum=1, fmt=lambda v: f"{v:,.1f} h",
    ))
    out.append(rank(
        "regular", "The Regular",
        "Most separate days spent in Middle-earth. Showing up beats marathoning.",
        "days played", {c: len(d) for c, d in days.items()}, R, minimum=2,
    ))

    # Meaningwave progress is stored per character, and the roster is what is being
    # ranked -- so the metric is quizzes passed across every character, with the
    # best single character (out of the seven philosophers) as the supporting detail.
    passes = Counter()
    per_char = defaultdict(set)
    finales = defaultdict(int)
    for varname, cdkey, playerid in ctx.meaningwave:
        if varname.startswith("u_"):
            passes[cdkey] += 1
            per_char[(cdkey, playerid)].add(varname)
        elif varname == "finale":
            finales[cdkey] += 1

    def detail(cdkey):
        best = max((len(v) for (ck, _), v in per_char.items() if ck == cdkey), default=0)
        bit = f"best character {best}/{ctx.mw_guide_count}"
        return bit + (f", {finales[cdkey]} finale(s)" if finales.get(cdkey) else "")

    out.append(rank(
        "mixtape", "Mixtape Chaser",
        "Meaningwave philosophers won over &mdash; one quiz, one mixtape at a time, "
        "counted across every character on the account.",
        "quizzes passed",
        {c: (v, detail(c)) for c, v in passes.items()}, R,
    ))

    return [a for a in out if a]


# --------------------------------------------------------------------------- #
# Character — classes, alignment, abilities, skills, oddities
# --------------------------------------------------------------------------- #

def character(ctx) -> list[dict]:
    out = []
    R, chars = ctx.roster, ctx.chars

    # Season 1 ran a level-40 engine cap, so "past 40" would award nobody -- the
    # milestone that actually meant something was *reaching* the cap.
    out.append(rank(
        "veterans", "Keeper of Veterans",
        "Most characters taken all the way to the level cap. Anyone can start one.",
        "characters at the cap",
        _per_account(chars, lambda c: 1 if c["level"] >= 40 else 0, R), R,
    ))
    out.append(rank(
        "altoholic", "Alt-oholic",
        "The largest stable of characters. Commitment issues, or thoroughness?",
        "characters", _per_account(chars, lambda c: 1, R), R, minimum=3,
    ))

    # Per-class mastery: total levels of that class across the roster.
    class_levels: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for c in chars:
        for cls, lvl in c["classes"]:
            class_levels[cls][c["cdkey"]] += lvl
    for cls, tally in sorted(class_levels.items()):
        name = ctx.class_name(cls)
        out.append(rank(
            f"class_{cls}", f"{name} Master",
            f"The most {name} levels amassed across a whole roster.",
            f"{name.lower()} levels", dict(tally), R, minimum=10,
        ))

    def caster_levels(group):
        return _per_account(
            chars, lambda c: sum(l for cls, l in c["classes"] if cls in group), R
        )

    out.append(rank(
        "arcane", "Master of the Arcane",
        "Most levels in the arcane casting classes, added together.",
        "arcane levels", caster_levels(ARCANE_CLASSES), R, minimum=10,
    ))
    out.append(rank(
        "divine", "Most Devout",
        "Most levels in the divine casting classes, added together.",
        "divine levels", caster_levels(DIVINE_CLASSES), R, minimum=10,
    ))
    out.append(rank(
        "devcrit", "Most Devastating",
        "Most Devastating Critical feats held across a roster. One blow, one ending.",
        "devastating critical feats",
        _per_account(chars, lambda c: sum(1 for f in c["feats"] if f in ctx.devcrit_feats), R),
        R,
    ))

    out.append(rank(
        "jack", "Jack of All Trades",
        "The widest spread of base classes played across one account.",
        "distinct classes",
        {ck: len({cls for c in cs for cls, _ in c["classes"]})
         for ck, cs in ctx.by_account.items()}, R, minimum=3,
    ))
    out.append(rank(
        "purist", "The Purist",
        "Most single-class characters taken to level 40. No dabbling.",
        "pure level-40 characters",
        _per_account(chars, lambda c: 1 if len(c["classes"]) == 1 and c["level"] >= 40 else 0, R),
        R,
    ))

    # Alignment: five lanes, each won by the roster that leans hardest that way.
    lanes = {
        "good":    ("Most Good",    lambda c: max(c["good_evil"] - 50, 0)),
        "evil":    ("Most Evil",    lambda c: max(50 - c["good_evil"], 0)),
        "lawful":  ("Most Lawful",  lambda c: max(c["law_chaos"] - 50, 0)),
        "chaotic": ("Most Chaotic", lambda c: max(50 - c["law_chaos"], 0)),
    }
    for aid, (title, fn) in lanes.items():
        out.append(rank(
            f"align_{aid}", title,
            "Summed across every character &mdash; conviction measured in aggregate.",
            "alignment points", _per_account(chars, fn, R), R, minimum=20,
        ))
    # A never-played character sits at exactly true neutral, so an unfiltered
    # "most neutral" is really an award for making characters and abandoning them.
    # Only rosters that were actually played qualify.
    played = {
        ck: [c for c in cs if c["level"] >= 20] for ck, cs in ctx.by_account.items()
    }
    neutral = {
        ck: (sum(abs(c["good_evil"] - 50) + abs(c["law_chaos"] - 50) for c in cs) // len(cs),
             f"{len(cs)} characters")
        for ck, cs in played.items() if len(cs) >= 2
    }
    out.append(rank(
        "align_neutral", "Most Neutral",
        "The played roster sitting closest to dead centre on both axes, on average. "
        "Committed to nothing.",
        "average distance from centre", neutral, R, lowest=True,
    ))

    for ability, (aid, high_title, low_title) in ABILITY_AWARDS.items():
        totals = _per_account(chars, lambda c, a=ability: c["abilities"][a], R)
        out.append(rank(
            f"ability_{aid}", high_title,
            f"Highest total {ability} across every character on the account.",
            f"total {ability}", totals, R, minimum=20,
        ))
        if low_title:
            averages = {
                ck: (round(sum(c["abilities"][ability] for c in cs) / len(cs), 1), f"{len(cs)} characters")
                for ck, cs in ctx.by_account.items() if len(cs) >= 3
            }
            out.append(rank(
                f"ability_{aid}_low", low_title,
                f"Lowest average {ability} across a roster of three or more. Somebody has to be.",
                f"average {ability}", averages, R, lowest=True, fmt=lambda v: f"{v}",
            ))

    for skill_id, (aid, title) in SKILL_AWARDS.items():
        totals = _per_account(
            chars,
            lambda c, i=skill_id: c["skills"][i] if i < len(c["skills"]) else 0,
            R,
        )
        out.append(rank(
            f"skill_{aid}", title,
            f"Most ranks of {ctx.skill_name(skill_id)} invested across a whole roster.",
            f"{ctx.skill_name(skill_id).lower()} ranks", totals, R, minimum=10,
        ))

    out.append(rank(
        "titan", "Titan",
        "The single toughest character on the server, by raw hit points.",
        "max hit points",
        _best_char(chars, lambda c: c["max_hp"]), R, minimum=100,
    ))
    out.append(rank(
        "wellrounded", "The Well-Rounded",
        "The character with the highest six ability scores added together.",
        "total ability scores",
        _best_char(chars, lambda c: sum(c["abilities"].values())), R, minimum=60,
    ))
    out.append(rank(
        "feats", "Feat Collector",
        "The single character carrying the most feats.",
        "feats", _best_char(chars, lambda c: len(c["feats"])), R, minimum=10,
    ))

    # Familiars and animal companions: which pet did the server actually pick?
    pets = Counter()
    per_account_pet = defaultdict(Counter)
    for c in chars:
        for kind, field in (("familiar", "familiar_type"), ("companion", "companion_type")):
            t = c.get(field)
            if t is None:
                continue
            label = ctx.pet_name(kind, int(t))
            pets[label] += 1
            per_account_pet[c["cdkey"]][label] += 1
    if pets:
        top_pet, _ = pets.most_common(1)[0]
        out.append(rank(
            "petfavourite", f"{top_pet} Collector",
            f"The server's favourite companion is the {top_pet.lower()}; this player kept the most of them.",
            f"{top_pet.lower()}s", {c: n[top_pet] for c, n in per_account_pet.items() if n[top_pet]}, R,
        ))
        out.append(rank(
            "petkeeper", "Beast Keeper",
            "Most familiars and animal companions bound across a whole roster.",
            "companions", {c: sum(n.values()) for c, n in per_account_pet.items()}, R, minimum=2,
        ))

    # Deities and races: the award names whichever the player leaned into.
    deity_pick, race_pick = {}, {}
    for ck, cs in ctx.by_account.items():
        deities = Counter(c["deity"] for c in cs if c["deity"].strip())
        if deities:
            name, n = deities.most_common(1)[0]
            deity_pick[ck] = (n, name)
        races = Counter(ctx.race_name(c["race"]) for c in cs if c["race"] >= 0)
        if races:
            name, n = races.most_common(1)[0]
            race_pick[ck] = (n, name)
    out.append(rank(
        "devout", "The Devout",
        "Most characters sworn to a single deity &mdash; the card names the god in question.",
        "characters sharing one deity", deity_pick, R, minimum=2,
    ))
    out.append(rank(
        "bloodline", "Truest Bloodline",
        "Most characters of one and the same race. The card names the people.",
        "characters of one race", race_pick, R, minimum=3,
    ))

    return [a for a in out if a]


# --------------------------------------------------------------------------- #
# The bestiary's revenge — awards for the monsters
# --------------------------------------------------------------------------- #

def npc_awards(ctx) -> list[dict]:
    """These are not player awards, so they bypass rank() entirely."""
    out = []

    killed = {k["resref"] for k in ctx.kills if k["solo"] or k["party"]}
    undefeated = sorted(
        ((info["name"], info["cr"], info["area"])
         for resref, info in ctx.bosses.items() if resref not in killed),
        key=lambda t: -t[1],
    )
    if undefeated:
        out.append({
            "id": "undefeated", "title": "The Undefeated",
            "blurb": "Bosses on the Roll of the Fallen that no player killed all season. "
                     "They are still down there.",
            "metric": "challenge rating",
            "rows": [{"name": n, "value": f"CR {cr:,.0f}", "detail": area}
                     for n, cr, area in undefeated[:15]],
            "total": len(undefeated),
        })

    boss_deaths = Counter()
    for k in ctx.kills:
        if k["resref"] in ctx.bosses:
            boss_deaths[k["resref"]] += k["solo"] + k["party"]
    if boss_deaths:
        out.append({
            "id": "mostfeared", "title": "Most Feared",
            "blurb": "The boss that died the most times. Familiarity breeds slaughter.",
            "metric": "deaths",
            "rows": [{"name": ctx.bosses[r]["name"], "value": f"{n:,} deaths",
                      "detail": ctx.bosses[r]["area"]}
                     for r, n in boss_deaths.most_common(10)],
            "total": len(boss_deaths),
        })

    all_deaths = Counter()
    for k in ctx.kills:
        all_deaths[k["resref"]] += k["solo"] + k["party"]
    if all_deaths:
        out.append({
            "id": "punchingbag", "title": "The Punching Bag",
            "blurb": "The most-killed creature in Middle-earth, boss or beast.",
            "metric": "deaths",
            "rows": [{"name": ctx.creature_name(r), "value": f"{n:,} deaths", "detail": ""}
                     for r, n in all_deaths.most_common(10)],
            "total": len(all_deaths),
        })

    hunters = defaultdict(set)
    for k in ctx.kills:
        if k["solo"] or k["party"]:
            hunters[k["resref"]].add(k["cdkey"])
    solitary = sorted(
        (ctx.creature_name(r), ctx.roster.account(next(iter(h))))
        for r, h in hunters.items() if len(h) == 1
    )
    if solitary:
        out.append({
            "id": "raresttrophy", "title": "Rarest Trophies",
            "blurb": "Creatures exactly one player ever killed. A private bestiary.",
            "metric": "sole hunter",
            "rows": [{"name": n, "value": who, "detail": ""} for n, who in solitary[:15]],
            "total": len(solitary),
        })

    return out


# --------------------------------------------------------------------------- #
# Boss alignment, read straight from the blueprints
# --------------------------------------------------------------------------- #

def load_boss_alignment(unpacked: Path, bosses: dict) -> dict[str, int]:
    """resref -> GoodEvil (0 evil .. 100 good).

    ``creature_index.json`` does not carry alignment, so this is the one place the
    awards read raw ``unpacked/`` JSON. Missing blueprints are simply skipped —
    a boss whose alignment we cannot read counts for neither side.
    """
    out = {}
    for resref in bosses:
        path = Path(unpacked) / f"{resref}.utc.json"
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        node = data.get("GoodEvil")
        if isinstance(node, dict) and node.get("value") is not None:
            out[resref] = int(node["value"])
    return out
