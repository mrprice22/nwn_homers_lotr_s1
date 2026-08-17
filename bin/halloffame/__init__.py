"""Hall of Fame — season-end player awards, mined from the server's persistent data.

The package is the data layer; ``bin/gen-halloffame.py`` is the renderer. Split that
way because the identity bridge (``identity.py``) and the character-file reader
(``bicreader.py``) are the parts that need debugging on their own, without a full
page build in the way.

Module map:

    twoda.py         minimal 2DA label reader (classes / races / feats)
    bicreader.py     servervault/*.bic -> parsed character dicts (cached)
    sources.py       read-only opens of the campaign SQLite DBs + module-index JSON
    identity.py      the CD-key <-> "playerid string" bridge, and the player roster
    categories.py    the CURATED tables you are meant to hand-tune
    awards.py        one function per award
    roadmapawards.py roadmap.yaml-derived awards (merit, reports, backlog)

Every award returns the same shape, so the renderer stays dumb::

    {"id", "title", "blurb", "metric", "winners": [{"player", "value", "detail"}],
     "ranked": [(player, value, detail), ...]}

``winners`` holds every player tied for first (the admin's rule: one winner, but ties
list all of them).
"""
