"""
domains.py
==========

The domain registry. Adding a third domain means adding one entry here and
dropping its four artefact files into `data/`. No other file changes.

Each domain's artefacts are produced by the Colab notebook (Modules 9 and 9b)
from a single extraction implementation, Module 3b, which reads conditions and
effects off the Unified Planning model rather than matching on action names.
That is why the same four files describe both domains.
"""

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass(frozen=True)
class Domain:
    key: str                    # artefact filename prefix
    name: str                   # shown in the selector
    short: str                  # shown in badges
    blurb: str                  # one line under the selector
    plan_caption: str           # heading above the plan table
    subject: str                # what the plan is about, used in prose
    family: str = ""            # which real-world workflow this belongs to
    condition: str = "valid"    # valid or faulty
    columns: Dict[str, str] = field(default_factory=dict)


REGISTRY: List[Domain] = [
    Domain(
        key="bus_train",
        name="Transport — bus and train journey",
        short="Transport",
        blurb="A passenger travels by bus to a station, waits on the platform, "
              "and boards a train to their destination. Two vehicles share the "
              "schedule.",
        plan_caption="The journey plan",
        subject="journey",
        family="transport",
        condition="valid",
        columns={"resource": "Vehicle"},
    ),
    Domain(
        key="bus_train_late",
        name="Transport — bus and train journey",
        short="Transport",
        blurb="A passenger travels by bus to a station, waits on the platform, "
              "and boards a train to their destination. Two vehicles share the "
              "schedule.",
        plan_caption="The journey plan",
        subject="journey",
        family="transport",
        condition="faulty",
        columns={"resource": "Vehicle"},
    ),
    Domain(
        key="cash_transfer",
        name="Social protection — cash transfer with grievance resolution",
        short="Social protection",
        blurb="A beneficiary is verified, approved and paid. The payment fails, "
              "a grievance is lodged and resolved, and a corrected payment is "
              "made before the programme deadline.",
        plan_caption="The payment workflow",
        subject="payment workflow",
        family="social_protection",
        condition="valid",
        columns={"resource": "Resource"},
    ),
    Domain(
        key="cash_transfer_late",
        name="Social protection — cash transfer with grievance resolution",
        short="Social protection",
        blurb="A beneficiary is verified, approved and paid. The payment fails, "
              "a grievance is lodged and resolved, and a corrected payment is "
              "made before the programme deadline.",
        plan_caption="The payment workflow",
        subject="payment workflow",
        family="social_protection",
        condition="faulty",
        columns={"resource": "Resource"},
    ),
]

#: The two workflows a participant chooses between. Each has a valid and a
#: faulty variant, and the app assigns which one a participant sees.
FAMILIES = ["transport", "social_protection"]


def variant(family: str, condition: str) -> "Domain":
    """The key for one workflow in one condition."""
    for domain in REGISTRY:
        if domain.family == family and domain.condition == condition:
            return domain
    raise KeyError(f"{family}/{condition}")


def family_of(key: str) -> str:
    return BY_KEY[key].family

BY_KEY = {d.key: d for d in REGISTRY}


def get(key: str) -> Domain:
    return BY_KEY[key]


def default_key() -> str:
    return REGISTRY[0].key


def families_present(available_keys) -> list:
    """Workflow families that have at least one variant on disk."""
    out = []
    for family in FAMILIES:
        if any(BY_KEY[k].family == family for k in available_keys):
            out.append(family)
    return out


def family_label(family: str) -> str:
    for domain in REGISTRY:
        if domain.family == family:
            return domain.name
    return family
