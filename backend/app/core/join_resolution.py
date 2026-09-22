"""
Resolving a control's join requirement against a client's actual schema.

The control says: api_access.user_id must line up with user.user_id. The
client's tables say what they say. This module decides which physical column
on each side satisfies each requirement, and how much to trust the answer, by
weighing the six things a person would check:

  1. schema relationship  - a declared foreign key between the two columns
  2. data relationship    - the values actually match (measured containment)
  3. semantic meaning     - the name AND the role fit: `created_by` holds user
                            ids too, but it names who created, not whose access
  4. cardinality          - the parent side is a key, the child side isn't empty
  5. constraints          - unique / nullable / indexed where the catalog says
  6. control requirement  - the pair is the one the rule actually needs

Verdicts (what the platform DOES with them is in the callers):
  valid        the relationship is declared, or the data proves it
  ambiguous    more than one pair fits, or the evidence is partial: a person picks
  contradicted the data or the roles say this pair is wrong
  unverified   nothing to check against (e.g. Gateway-only): NO opinion, not a demotion
  unresolved   no column on one side plays the required part yet

Pure logic over plain inputs; join_resolution_service loads them.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from app.core.canonical_model import suggest_canonical_field
from app.core.join_requirements import JoinRequirement, reference_roles_agree
from app.core.relationship_inference import ColumnRef

VALID = "valid"
AMBIGUOUS = "ambiguous"
CONTRADICTED = "contradicted"
UNVERIFIED = "unverified"
UNRESOLVED = "unresolved"

# Data evidence thresholds (percent of the child's distinct values found on the parent).
STRONG_CONTAINMENT = 90.0
# Only near-zero overlap says "these are not the same key". A PARTIAL overlap
# is not a contradiction: for a missing_match rule the unmatched rows are the
# very exceptions the control exists to find, so they must never block the test.
CONTRADICTION_CONTAINMENT = 10.0
# Below this many distinct child values a low containment proves nothing.
MIN_DISTINCT_FOR_CONTRADICTION = 5
# A second pair within this many points of the best one is a genuine alternative.
AMBIGUITY_MARGIN = 10.0
# ...and its columns must be about as good a NAME match as the best pair's: an exact
# `entity_id` is not rivalled by `company_id` just because both end in _id.
NAME_RIVAL_WINDOW = 15.0
# Edges this strong count as a usable link when tracing a path through bridge tables.
PATH_MIN_CONTAINMENT = 70.0
MAX_PATH_HOPS = 3

_MIN_CANDIDATE_NAME_SCORE = 60.0


@dataclass(frozen=True)
class Edge:
    child_field_id: str
    parent_field_id: str
    kind: str  # declared_fk | inferred
    status: str = "detected"  # detected | confirmed | rejected
    containment: float | None = None
    child_distinct: int | None = None
    parent_distinct: int | None = None
    parent_unique: bool | None = None
    relationship_id: str | None = None

    @property
    def strong(self) -> bool:
        if self.status == "rejected":
            return False
        if self.kind == "declared_fk" or self.status == "confirmed":
            return True
        return (self.containment or 0.0) >= STRONG_CONTAINMENT

    @property
    def contradicting(self) -> bool:
        if self.status == "rejected":
            return True
        return (
            self.kind == "inferred"
            and self.status != "confirmed"
            and self.containment is not None
            and self.containment < CONTRADICTION_CONTAINMENT
            and (self.child_distinct or 0) >= MIN_DISTINCT_FOR_CONTRADICTION
            and self.child_fits_in_parent
        )

    @property
    def child_fits_in_parent(self) -> bool:
        """A bigger set is not expected to fit inside a smaller one (all 15 users
        inside a 2-row access table), so low containment in THAT direction is
        no evidence at all."""
        if self.child_distinct is None or self.parent_distinct is None:
            return True
        return self.child_distinct <= self.parent_distinct

    @property
    def usable(self) -> bool:
        if self.status == "rejected" or self.kind == "declared_fk" or self.status == "confirmed":
            return True
        return not (self.containment is not None and self.containment < STRONG_CONTAINMENT and not self.child_fits_in_parent)


@dataclass
class PairScore:
    left: ColumnRef
    right: ColumnRef
    total: float
    edge: Edge | None
    role_mismatch: bool
    notes: list[str] = field(default_factory=list)
    left_name: float = 0.0
    right_name: float = 0.0

    @property
    def strong(self) -> bool:
        return self.edge is not None and self.edge.strong and not self.role_mismatch


@dataclass
class JoinResolution:
    requirement: JoinRequirement
    verdict: str
    left: ColumnRef | None = None
    right: ColumnRef | None = None
    evidence: list[str] = field(default_factory=list)
    alternatives: list[PairScore] = field(default_factory=list)
    relationship: str = "none"  # declared_fk | inferred | none
    relationship_id: str | None = None
    containment: float | None = None
    cardinality: str | None = None
    reason: str = ""
    # An auditor's own ruling on this relationship, distinct from `verdict`
    # (verdict is what the EVIDENCE currently shows; ruling is whether a
    # person has actually confirmed or rejected it) — None when there is no
    # edge to rule on at all, "detected" when one exists but nobody has
    # ruled on it yet. Without this the UI had no way to tell "never ruled
    # on" apart from "already confirmed," so its Confirm/Reject actions
    # looked identical, and thus looked broken, before and after using them.
    ruling: str | None = None  # detected | confirmed | rejected | None


def _pretty(col: ColumnRef) -> str:
    return f"{col.entity_name}.{col.name}"


def candidate_columns(obj: str, field_name: str, columns: list[ColumnRef]) -> list[tuple[ColumnRef, float]]:
    """Columns of one table that could serve as canonical `obj.field_name`,
    with a 0-100 name score. Synthetic row ids (`_id`) never serve as a
    business key."""
    wanted = f"{obj}.{field_name}"
    wanted_tokens = {t for t in field_name.lower().split("_") if t}
    out: list[tuple[ColumnRef, float]] = []
    for col in columns:
        if col.name == "_id":
            continue
        best, score = suggest_canonical_field(
            col.name, is_primary_key=col.is_primary_key, preferred_object=obj, in_object_only=True
        )
        if best == wanted and score >= _MIN_CANDIDATE_NAME_SCORE:
            out.append((col, score))
            continue
        col_tokens = {t for t in col.name.lower().replace("-", "_").split("_") if t}
        # owner_user_id contains the tokens of user_id: not the same column, but
        # exactly the lookalike the role check exists to catch.
        if wanted_tokens and wanted_tokens < col_tokens:
            out.append((col, 55.0))
    return out


def _edge_between(edges: dict[frozenset, Edge], a: ColumnRef, b: ColumnRef) -> Edge | None:
    return edges.get(frozenset((a.field_id, b.field_id)))


def index_edges(edges: list[Edge]) -> dict[frozenset, Edge]:
    """One best edge per column pair, whichever way it was measured. Edges that
    only say "the bigger side doesn't fit in the smaller one" carry no
    information and are dropped; then declared/confirmed beats inferred, then
    higher containment."""
    best: dict[frozenset, Edge] = {}

    def rank(e: Edge):
        return (e.kind == "declared_fk" or e.status == "confirmed", e.containment or 0.0)

    for e in edges:
        if not e.usable:
            continue
        key = frozenset((e.child_field_id, e.parent_field_id))
        current = best.get(key)
        if current is None or rank(e) > rank(current):
            best[key] = e
    return best


def _score_pair(
    req: JoinRequirement, left: ColumnRef, left_name: float, right: ColumnRef, right_name: float, edges: dict[frozenset, Edge]
) -> PairScore:
    roles = (reference_roles_agree(req.left_field, left.name), reference_roles_agree(req.right_field, right.name))
    mismatch = "mismatch" in roles
    role_factor = 0.0 if mismatch else (1.0 if roles == ("match", "match") else 0.85)
    semantic = ((left_name + right_name) / 200.0) * role_factor

    edge = _edge_between(edges, left, right)
    notes: list[str] = []
    relationship = 0.0
    if edge is not None and not edge.contradicting:
        if edge.kind == "declared_fk" or edge.status == "confirmed":
            relationship = 1.0
        else:
            relationship = ((edge.containment or 0.0) / 100.0) * (1.0 if edge.parent_unique else 0.85)
    elif edge is not None:
        notes.append("the values do not match")

    # Cardinality/constraints: the referenced side should be a key; the referencing side shouldn't be empty.
    keyed = any(c.is_primary_key or c.is_unique for c in (left, right)) or bool(edge is not None and edge.parent_unique)
    cardinality = 1.0 if keyed else 0.5
    for col in (left, right):
        if col.null_ratio is not None and col.null_ratio > 0.5:
            cardinality *= 0.4
            notes.append(f"{_pretty(col)} is {round(col.null_ratio * 100)}% empty")

    total = 35.0 * semantic + 45.0 * relationship + 10.0 * cardinality + 10.0 * (1.0 if not mismatch else 0.0)
    if edge is None:
        total = min(total, 60.0)  # nothing proves these two relate: never enough for 'valid'
    return PairScore(
        left=left, right=right, total=round(total, 2), edge=edge, role_mismatch=mismatch, notes=notes,
        left_name=left_name, right_name=right_name,
    )


def resolve_join(
    req: JoinRequirement,
    left_columns: list[ColumnRef],
    right_columns: list[ColumnRef],
    edges: list[Edge],
) -> JoinResolution:
    left_cands = candidate_columns(req.left_object, req.left_field, left_columns)
    right_cands = candidate_columns(req.right_object, req.right_field, right_columns)
    if not left_cands or not right_cands:
        missing = req.left if not left_cands else req.right
        return JoinResolution(
            requirement=req, verdict=UNRESOLVED,
            reason=f"No column in the bound table plays the part of {missing} yet.",
            evidence=[f"Nothing looks like {missing}."],
        )

    indexed = index_edges(edges)
    pairs = [
        _score_pair(req, lc, ln, rc, rn, indexed)
        for lc, ln in left_cands
        for rc, rn in right_cands
    ]
    pairs.sort(key=lambda p: p.total, reverse=True)
    best = pairs[0]
    rivals = [
        p for p in pairs[1:]
        if (p.left.field_id, p.right.field_id) != (best.left.field_id, best.right.field_id)
        and p.total >= best.total - AMBIGUITY_MARGIN
        and not p.role_mismatch
        and p.left_name >= best.left_name - NAME_RIVAL_WINDOW
        and p.right_name >= best.right_name - NAME_RIVAL_WINDOW
    ]

    result = JoinResolution(requirement=req, verdict=UNVERIFIED, left=best.left, right=best.right, alternatives=rivals)
    edge = best.edge
    if edge is not None:
        result.relationship = "declared_fk" if edge.kind == "declared_fk" else "inferred"
        result.relationship_id = edge.relationship_id
        result.containment = edge.containment
        result.cardinality = None
        result.ruling = edge.status
    evidence = result.evidence

    if best.role_mismatch:
        result.verdict = CONTRADICTED
        result.reason = (
            f"{_pretty(best.left)} / {_pretty(best.right)} hold the right kind of value but play a different part "
            f"than {req.left} ↔ {req.right} (e.g. who created or approved a record, not the subject)."
        )
        evidence.append(result.reason)
        return result

    if edge is not None:
        if edge.kind == "declared_fk":
            evidence.append(f"{_pretty(best.left)} → {_pretty(best.right)} is a declared foreign key.")
        elif edge.status == "confirmed":
            evidence.append("An auditor confirmed this relationship.")
        if edge.containment is not None:
            evidence.append(
                f"{round(edge.containment)}% of {edge.child_distinct} distinct values were found on the other side"
                + (" (a key on that side)." if edge.parent_unique else ".")
            )
        if edge.contradicting:
            result.verdict = CONTRADICTED
            result.reason = (
                "An auditor rejected this relationship."
                if edge.status == "rejected"
                else f"Only {round(edge.containment or 0)}% of the values in {_pretty(best.left)} exist in {_pretty(best.right)}: these are not the same key."
            )
            evidence.append(result.reason)
            return result
        strong_pairs = [p for p in pairs if p.strong]
        different_strong = {(p.left.field_id, p.right.field_id) for p in strong_pairs}
        if best.strong and len(different_strong) <= 1:
            result.verdict = VALID
            result.reason = "The relationship is proven."
            return result
        if best.strong and len(different_strong) > 1:
            result.verdict = AMBIGUOUS
            result.reason = "More than one pair of columns is proven; pick the one this control means."
            return result
        result.verdict = AMBIGUOUS
        if not edge.containment:
            result.reason = (
                f"None of the {edge.child_distinct} distinct values in {_pretty(best.left)} were found in {_pretty(best.right)}. "
                "Too few values to call it wrong, but these do not look like the same identifier: check before trusting this join."
            )
        else:
            result.reason = f"Only {round(edge.containment)}% of the values match: partial evidence, a person should confirm."
        return result

    # No edge between the best pair.
    evidence.extend(best.notes)
    if rivals:
        result.verdict = AMBIGUOUS
        result.reason = "More than one pair of columns could satisfy this and nothing proves which one relates."
        return result
    result.verdict = UNVERIFIED
    result.reason = "No relationship evidence is available for these tables (they may be reachable only through a Gateway)."
    evidence.append(result.reason)
    return result


# ---- paths through bridge tables ----------------------------------------------
@dataclass(frozen=True)
class PathStep:
    from_column: str  # entity.column
    to_column: str


@dataclass
class JoinPath:
    from_entity: str
    to_entity: str
    steps: list[PathStep]
    executed: bool = False  # tests join two tables directly; a bridge path is shown, not run


def find_paths(
    entity_ids: list[str], columns: list[ColumnRef], edges: list[Edge], *, max_hops: int = MAX_PATH_HOPS
) -> list[JoinPath]:
    """For every pair of the control's tables, the shortest chain of strong
    relationships between them (through bridge tables if need be). A pair with
    no chain simply has no entry."""
    by_field = {c.field_id: c for c in columns}
    adjacency: dict[str, list[tuple[str, ColumnRef, ColumnRef]]] = {}
    for e in edges:
        if not e.usable or (not e.strong and (e.containment or 0.0) < PATH_MIN_CONTAINMENT):
            continue
        if e.status == "rejected":
            continue
        child, parent = by_field.get(e.child_field_id), by_field.get(e.parent_field_id)
        if child is None or parent is None or child.entity_id == parent.entity_id:
            continue
        adjacency.setdefault(child.entity_id, []).append((parent.entity_id, child, parent))
        adjacency.setdefault(parent.entity_id, []).append((child.entity_id, parent, child))

    names = {c.entity_id: c.entity_name for c in columns}
    paths: list[JoinPath] = []
    for i, start in enumerate(entity_ids):
        for goal in entity_ids[i + 1:]:
            queue = deque([(start, [])])
            seen = {start}
            found = None
            while queue and found is None:
                node, steps = queue.popleft()
                if len(steps) >= max_hops:
                    continue
                for nxt, here_col, there_col in adjacency.get(node, []):
                    if nxt in seen:
                        continue
                    new_steps = steps + [PathStep(_pretty(here_col), _pretty(there_col))]
                    if nxt == goal:
                        found = new_steps
                        break
                    seen.add(nxt)
                    queue.append((nxt, new_steps))
            if found:
                paths.append(JoinPath(names.get(start, start), names.get(goal, goal), found))
    return paths
