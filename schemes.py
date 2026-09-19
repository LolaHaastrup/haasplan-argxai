"""
schemes.py
==========

Plain-language descriptions of the nine argument schemes, and a builder for
the argumentation framework diagram.

The descriptions are presentation only. Nothing in the framework is derived
from this file; it exists so a participant who has never met argumentation
theory can read the Framework tab without a glossary beside them.
"""

from typing import Dict, List

#: Scheme id to (short name, what it claims).
SCHEMES: Dict[str, tuple] = {
    "S0": ("Replayed State",
           "Reconstructs what is true at every moment, from the starting "
           "situation and the effects of every action that has run so far."),
    "S1": ("Action Applicability",
           "Claims an action could legally run over its scheduled window, "
           "because its start conditions held and nothing removed what it "
           "needed while it ran."),
    "S2": ("Causal Goal Support",
           "Claims an action earns its place, because something it produces "
           "is either required by a later action or is part of the goal."),
    "S3": ("Temporal Feasibility",
           "Claims an action's timing works, because whatever enables it "
           "finishes first and its own window respects any deadline."),
    "S4": ("Concurrent Executability",
           "Claims two overlapping actions could each legally begin at the "
           "moment their windows start to overlap."),
    "S5": ("Resource and Concurrency Feasibility",
           "Claims two overlapping actions are safe together, because they "
           "hold different exclusive resources and neither destroys what the "
           "other needs."),
    "S6": ("Temporal Ordering Justification",
           "Claims an ordering between two actions is necessary, because "
           "reversing it would remove something the later action requires."),
    "S7": ("Invariant Maintenance Justification",
           "Claims a condition that must stay true throughout an action does "
           "stay true, and is not removed by anything running alongside it."),
    "S8": ("Plan Summary Argument",
           "The top-level claim that the plan as a whole is valid. It rests "
           "on eight premises, one for each of the checks above."),
}

PSA_EXPLANATION = (
    "The system's verdict comes from a single top-level argument called the "
    "**Plan Summary Argument**, written PSA(P). It claims the plan is valid, "
    "and it rests on eight premises, each supplied by one of the argument "
    "schemes below.\n\n"
    "Each premise can be attacked by a **critical question**. A critical "
    "question is a standard challenge to the kind of reasoning a scheme uses, "
    "so it applies wherever that scheme is used rather than only to this "
    "plan. Where a critical question can itself be answered by another "
    "scheme, it is **defeated** and the premise survives. Where it cannot, "
    "the critical question **succeeds** and the plan fails on that point.\n\n"
    "The verdict is therefore not a score or a judgement call. It is whatever "
    "survives once every applicable challenge has been put."
)


def scheme_label(scheme_id: str) -> str:
    if scheme_id in SCHEMES:
        return f"{scheme_id} — {SCHEMES[scheme_id][0]}"
    return scheme_id


def first_scheme_id(text: str) -> str:
    """Pull the leading scheme id out of a CQ_META string like 'S1 - Action…'."""
    text = (text or "").strip()
    for candidate in SCHEMES:
        if text.startswith(candidate):
            return candidate
    return ""


def build_dot(cq_rows: List[dict], cq_meta: Dict[str, dict]) -> str:
    """Return a Graphviz DOT string for the framework, aggregated by CQ type.

    One node per critical question rather than per instance. A plan with
    forty CQ instances would be unreadable as a graph; aggregating to the
    nine question types shows the STRUCTURE, which is what the diagram is
    for, and the table underneath carries the per-instance counts.

    Rendered client-side by Streamlit, so no Graphviz binary is needed on the
    server.
    """
    outcomes: Dict[str, Dict[str, int]] = {}
    for row in cq_rows:
        bucket = outcomes.setdefault(row["CQ"], {})
        bucket[row["Outcome"]] = bucket.get(row["Outcome"], 0) + 1

    lines = [
        "digraph AF {",
        "  rankdir=BT;",
        "  bgcolor=transparent;",
        '  node [fontname="Helvetica", fontsize=10, style="filled,rounded", '
        'shape=box, penwidth=0.8];',
        '  edge [fontname="Helvetica", fontsize=8, penwidth=0.9];',
        '  PSA [label="PSA(P)\\nPlan Summary Argument", fillcolor="#0B2D8F", '
        'fontcolor="white", shape=box, penwidth=0];',
    ]

    attacked_schemes = set()
    defeating_schemes = set()

    for cq_id in sorted(cq_meta, key=lambda c: int(c[2:])):
        if cq_id not in outcomes:
            continue
        meta = cq_meta[cq_id]
        attacked = first_scheme_id(meta.get("attacks", ""))
        defeater = first_scheme_id(meta.get("defeated_by", ""))
        counts = outcomes[cq_id]
        succeeds = counts.get("succeeds", 0)

        # A critical question that succeeds is an unanswered attack, so it is
        # IN under the grounded labelling and the premise it attacks is OUT.
        if succeeds:
            fill, font = "#C0392B", "white"
            status = f"{succeeds} succeed"
        elif counts.get("defeated", 0):
            fill, font = "#E8F6EE", "#1E8449"
            status = f"{counts['defeated']} defeated"
        else:
            fill, font = "#F1F2F4", "#6B7280"
            status = "not applicable"

        lines.append(f'  {cq_id} [label="{cq_id}\\n{status}", '
                     f'fillcolor="{fill}", fontcolor="{font}"];')

        if attacked:
            attacked_schemes.add(attacked)
            lines.append(f'  {cq_id} -> {attacked} '
                         f'[label="attacks", color="#C0392B", '
                         f'fontcolor="#C0392B", arrowhead=vee];')
        if defeater and not succeeds:
            defeating_schemes.add(defeater)
            lines.append(f'  {defeater} -> {cq_id} '
                         f'[label="defeats", color="#1E8449", '
                         f'fontcolor="#1E8449", arrowhead=vee];')

    for scheme_id in sorted(attacked_schemes | defeating_schemes):
        short = SCHEMES.get(scheme_id, (scheme_id, ""))[0]
        lines.append(f'  {scheme_id} [label="{scheme_id}\\n{short}", '
                     f'fillcolor="#EAF0FA", fontcolor="#0B2D8F"];')

    for scheme_id in sorted(attacked_schemes):
        lines.append(f'  {scheme_id} -> PSA '
                     f'[label="supports", color="#6B7280", '
                     f'fontcolor="#6B7280", arrowhead=normal];')

    lines.append("}")
    return "\n".join(lines)
