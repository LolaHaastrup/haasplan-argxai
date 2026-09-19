"""
plain.py
========

Turns the framework's internal wording into something a participant can read.

WHY THIS IS A SEPARATE LAYER
----------------------------
The premise labels produced by the extractor are deliberately terse and
formal, because Module 4 parses them and the agreement test compares them
string for string. Changing them to read nicely would break both.

So the translation happens here, at display time only. Nothing the framework
computes depends on this file, and the stored transcript keeps the original
wording, so analysis is unaffected by how the text is phrased on screen.

WHAT IT DOES
------------
Three passes, in order.

1. Fluent expressions. `at(bus,bus_stop)` becomes "the bus is at the bus stop".
2. Structural prefixes. "Start: X" becomes "Before this step began, X".
3. Action identifiers. `review_grievance` becomes "Review and resolve
   grievance" where the domain supplies a display name.
"""

import re
from typing import Dict, Optional

#: Predicate to a sentence template. {0}, {1} are the arguments.
#: Anything not listed falls back to a generic readable form, so a new domain
#: works without being added here.
PREDICATES: Dict[str, str] = {
    # transport
    "at": "the {0} is at the {1}",
    "passenger_at": "the passenger is at the {0}",
    "passenger_on_bus": "the passenger is on the bus",
    "passenger_on_train": "the passenger is on the train",
    "train_at_station": "the train is at the {0}",
    "is_train_station": "the {0} is a train station",
    "ready_to_board": "the passenger is ready to board",
    # social protection
    "registered": "the beneficiary is registered",
    "record_active": "the beneficiary record is active",
    "account_valid": "the beneficiary's account details are valid",
    "identity_verified": "the beneficiary's identity has been verified",
    "eligible": "the beneficiary is eligible for the programme",
    "approved": "the beneficiary has been approved",
    "instruction_ready": "the payment instruction has been generated",
    "payment_submitted": "the payment has been submitted",
    "payment_failed": "the payment has failed",
    "grievance_open": "a grievance has been lodged",
    "grievance_upheld": "the grievance has been upheld",
    "record_corrected": "the beneficiary record has been corrected",
    "payment_reauthorised": "the payment has been reauthorised",
    "payment_completed": "the payment has been completed",
    "beneficiary_notified": "the beneficiary has been notified",
}

#: Structural prefixes the extractor uses, and how to say them.
PREFIXES = [
    ("Start: ", "Before this step began, "),
    ("End: ", "When this step finished, "),
    ("Invariant: ", "Throughout this step, "),
    ("Enabling: ", "Timing check. "),
    ("Deadline: ", "Deadline check. "),
    ("Ordering: ", "Order check. "),
    ("No over-all invariants (vacuous)",
     "Nothing had to stay true throughout this step."),
    ("No end conditions (vacuous)",
     "Nothing had to be true at the moment this step finished."),
    ("No deadline constraint (vacuous)",
     "This step has no deadline to meet."),
    ("Internal applicability (S1)",
     "The step itself could legally run."),
    ("Action applicable (S1)",
     "The step itself could legally run."),
    ("Effect consumed or satisfies G",
     "What this step produces is either needed later or is part of the goal."),
    ("No concurrent actions (vacuous)",
     "Nothing else runs at the same time as this step."),
]


def _tidy(token: str) -> str:
    """Underscores to spaces, so `bus_stop` reads as `bus stop`."""
    return token.replace("_", " ").strip()


def fluent(text: str) -> str:
    """Render one fluent expression in words."""
    text = text.strip()
    match = re.fullmatch(r"([a-zA-Z_][\w]*)\(([^()]*)\)", text)
    if match:
        name, args = match.group(1), [a.strip() for a in
                                      match.group(2).split(",") if a.strip()]
        template = PREDICATES.get(name)
        if template:
            try:
                return template.format(*[_tidy(a) for a in args])
            except IndexError:
                pass
        return "%s %s" % (_tidy(name), " ".join(_tidy(a) for a in args))
    return PREDICATES.get(text, _tidy(text))


def _replace_fluents(text: str) -> str:
    """Rewrite every fluent-looking token in a longer string."""
    text = re.sub(r"\b([a-zA-Z_][\w]*)\(([^()]*)\)",
                  lambda m: fluent(m.group(0)), text)

    # Bare zero-argument fluents, longest first so `payment_failed` is not
    # matched as `payment` plus leftovers.
    for name in sorted(PREDICATES, key=len, reverse=True):
        if "(" in PREDICATES[name] or "{" in PREDICATES[name]:
            continue
        text = re.sub(r"(?<![\w(])%s(?![\w)])" % re.escape(name),
                      PREDICATES[name], text)
    return text


def render(text: str, display_names: Optional[Dict[str, str]] = None) -> str:
    """Rewrite one line of explainer output for a reader.

    Safe on any input. Anything it does not recognise passes through
    unchanged rather than being mangled, so a new domain degrades to the
    original wording instead of breaking.
    """
    if not text:
        return text
    out = text

    for marker, replacement in PREFIXES:
        if marker.endswith(" "):
            out = out.replace(marker, replacement)
        else:
            out = out.replace(marker, replacement)

    # Patterns the generic passes would leave clumsy. Handled before the
    # fluent pass so their inner fluents are still rewritten afterwards.
    out = re.sub(r"Throughout this step, (.+?) holds over \(([^)]*)\)",
                 r"Throughout this step, \1 had to stay true, and it did",
                 out)
    out = re.sub(r"Timing check\. e\(([^)]*)\)=(\S+) <= s\(([^)]*)\)=(\S+)",
                 r"Timing check. \1 finished at time \2, and \3 began at "
                 r"time \4, so it finished in time", out)
    out = re.sub(r"Order check\. e\(([^)]*)\)=(\S+) <= s\(([^)]*)\)=(\S+)",
                 r"Order check. \1 finished at time \2 before \3 began at "
                 r"time \4", out)
    out = re.sub(r"Deadline check\. e\(([^)]*)\)=(\S+) <= (\S+)",
                 r"Deadline check. \1 finished at time \2, within its "
                 r"deadline of \3", out)
    out = re.sub(r"Effect '([^']*)' produced",
                 r"This step produces \1", out)
    out = re.sub(r"Plan completes at makespan t=(\S+)",
                 r"The whole plan finishes at time \1", out)

    out = _replace_fluents(out)

    if display_names:
        for action_name in sorted(display_names, key=len, reverse=True):
            out = re.sub(r"(?<![\w])%s(?![\w])" % re.escape(action_name),
                         display_names[action_name], out)

    out = out.replace("[holds]", "confirmed")
    out = out.replace("[FAILS]", "NOT met")
    out = out.replace("(vacuous)", "(nothing to check)")
    out = out.replace("PSA(P)", "the overall plan argument")
    return out
