"""Derive each person's relationship to a root person from father/mother/spouse
keys (SPEC §3.3 — relationships are derived, never hand-labelled). Used to group
the People filter relative to whoever's logged in.

The root defaults to 'steve' (the archive's canonical owner). When a logged-in
user is linked to a person, the People tree roots from THEM ("Self" = them). An
unlinked viewer roots from the default but with mark_self=False, so they never
see "Self = Steve".
"""
from app import models as m

DEFAULT_ROOT = "steve"


def derive_relationships(
    persons: list[m.Person], root: str = DEFAULT_ROOT, mark_self: bool = True
) -> dict[str, str | None]:
    by_id = {p.id: p for p in persons}

    def parents(pid):
        p = by_id.get(pid)
        return [x for x in (p.father_id, p.mother_id) if x] if p else []

    def spouse(pid):
        p = by_id.get(pid)
        return p.spouse_id if p else None

    root_parents = set(parents(root))
    grandparents = {gp for par in root_parents for gp in parents(par)}

    out: dict[str, str | None] = {}
    for p in persons:
        pid = p.id
        # Non-family (friends/others) have no tree position — group them separately
        # rather than letting them fall through to "Extended family" (SPEC §12.7).
        if not getattr(p, "is_family", True):
            out[pid] = "Friends & others"
            continue
        if pid == root:
            out[pid] = "Self" if mark_self else None
            continue
        if pid in root_parents:
            out[pid] = "Parent"
        elif pid in grandparents:
            out[pid] = "Grandparent"
        elif spouse(root) == pid:
            out[pid] = "Spouse"
        # sibling: shares a parent with the root
        elif root_parents and root_parents.intersection(parents(pid)):
            out[pid] = "Sibling"
        # child: the root is a parent
        elif root in parents(pid):
            out[pid] = "Child"
        # aunt/uncle: sibling of a parent (shares a grandparent), or married to one
        elif grandparents and grandparents.intersection(parents(pid)):
            out[pid] = "Aunt / Uncle"
        elif spouse(pid) and grandparents.intersection(parents(spouse(pid))):
            out[pid] = "Aunt / Uncle"
        # cousin: child of an aunt/uncle (a parent shares a grandparent)
        elif any(grandparents.intersection(parents(par)) for par in parents(pid)):
            out[pid] = "Cousin"
        else:
            out[pid] = "Extended family"
    return out
