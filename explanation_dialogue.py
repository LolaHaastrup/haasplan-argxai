# =============================================================================
# MODULE 10b — EXPLANATION DIALOGUE PROTOCOL ENGINE
# =============================================================================
# A formal EXPLANATION dialogue protocol over the computed framework AF(P).
#
# DIALOGUE TYPE.  This is an explanation dialogue, not a persuasion dialogue.
# The explainer E has privileged access to the ground truth, because AF(P)
# and its grounded extension are computed by Modules 1-5 before the dialogue
# begins.  The explainee U is not advancing a competing claim.  The protocol
# therefore governs TRAVERSAL of a fixed argumentation structure, and it
# terminates on transfer of understanding rather than on the acceptance or
# retraction of a claim.
#
# PROTOCOL COMPONENTS
#   1. Communication language   Locution
#   2. Legal moves function     ExplanationDialogue.legal_moves
#   3. Effect rules             ExplanationDialogue._apply
#   4. Turn taking              strict alternation, see .turn
#   5. Termination rules        ExplanationDialogue.is_terminated
#   6. Outcome rules            ExplanationDialogue.outcome
#
# LEGALITY RULES
#   R1 Opening    The dialogue opens with U:OPEN.  E replies ASSERT(S8).
#   R2 Relevance  U may CHALLENGE(cq, action) only where a CQ instance for
#                 that action exists in CQ_RESULTS.
#   R3 Obligation E must answer every CHALLENGE, with JUSTIFY, CONCEDE or
#                 DECLARE_NA, selected by the recorded Outcome.
#   R4 Drill-down After JUSTIFY, U may WHY any premise of the defeating
#                 scheme.  E answers GROUND.
#   R5 Foundation Grounding bottoms out at S0, the replayed state.
#   R6 No repeat  An answered CHALLENGE or WHY may not be repeated, unless
#                 preceded by NOT_UNDERSTAND, which obliges E to REFORMULATE.
#
# This cell has no UI dependency.  It is the same engine the Streamlit app
# should import, so that both interfaces provably run one protocol.
# =============================================================================

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


# --- Critical question metadata (carried over, unchanged in content) --------

CQ_META = {
    "CQ1": {
        "challenge":       "Do all start conditions hold in the replayed state at the action's scheduled start time?",
        "attacks":         "S1 - Action Applicability",
        "attacks_premise": "P1: start-condition satisfaction",
        "s8_premise":      "P2: every action applicable",
        "defeated_by":     "S3 - Temporal Feasibility",
        "defeating_logic": "S3's Premise 2 (enabling timing) guarantees that every action producing a required start condition finishes before the dependent action begins. Since the enabling effect fires at or before the action's scheduled start time, every required start condition is present in the replayed state at that moment.",
    },
    "CQ2": {
        "challenge":       "Does every over-all invariant hold continuously throughout the action's execution interval?",
        "attacks":         "S1 - Action Applicability",
        "attacks_premise": "P2: invariant persistence",
        "s8_premise":      "P2: every action applicable",
        "defeated_by":     "S7 - Invariant Maintenance Justification",
        "defeating_logic": "S7 confirms that the required invariant condition holds in the replayed state at the moment the action starts, and that no action running at the same time removes it at any point during the open execution window.",
    },
    "CQ3": {
        "challenge":       "Does this action produce something the plan genuinely needs, a required condition for a later action or a goal fluent?",
        "attacks":         "S2 - Causal Goal Support",
        "attacks_premise": "P2-P3: effect required and genuinely consumed",
        "s8_premise":      "P3: every action contributes causally",
        "defeated_by":     "S1 - Action Applicability",
        "defeating_logic": "S1 confirms that the action is legally executable over its scheduled interval and that its end effects are genuinely produced at the moment the action finishes, so whatever it produces is real and available for any later action or goal that depends on it.",
    },
    "CQ4": {
        "challenge":       "Did every action that produces a condition this action needs finish before this action was scheduled to start?",
        "attacks":         "S3 - Temporal Feasibility",
        "attacks_premise": "P2: enabling timing",
        "s8_premise":      "P4: every action is temporally feasible",
        "defeated_by":     "S6 - Temporal Ordering Justification",
        "defeating_logic": "S6 directly certifies, as its first premise, that the enabling action finishes no later than the dependent action begins. This finish-to-start timing certificate provides the most direct answer to whether enabling actions complete in time.",
    },
    "CQ5": {
        "challenge":       "Does this action's own scheduled window obey all external timing rules, release times, deadlines and minimum gaps?",
        "attacks":         "S3 - Temporal Feasibility",
        "attacks_premise": "P3: temporal constraints satisfied",
        "s8_premise":      "P4: every action is temporally feasible",
        "defeated_by":     "S6 - Temporal Ordering Justification",
        "defeating_logic": "S6's Premise 3 establishes that an ordering is necessary in part because removing it would violate a timing constraint, so it carries evidence that the current schedule already respects those constraints. S6 defeats CQ4 via Premise 1 and CQ5 via Premise 3, different premises answering different questions.",
    },
    "CQ6": {
        "challenge":       "Can each concurrent action legally begin at the scheduled overlap start time?",
        "attacks":         "S4 - Concurrent Executability",
        "attacks_premise": "P2: individual applicability of each concurrent action",
        "s8_premise":      "P5: concurrent actions individually executable",
        "defeated_by":     "S5 - Resource and Concurrency Feasibility",
        "defeating_logic": "S5 builds on S4 as its first premise. S4 assembles the individual S1 applicability certificates for both actions, confirming each holds all required start conditions at the overlap start time. S5 then additionally certifies disjoint resource locks and mutual invariant compatibility.",
    },
    "CQ7": {
        "challenge":       "Do the two concurrent actions truly hold disjoint resource locks and avoid interfering with each other's continuously required conditions?",
        "attacks":         "S5 - Resource and Concurrency Feasibility",
        "attacks_premise": "P2-P3: disjoint resource locks and mutual invariant compatibility",
        "s8_premise":      "P6: concurrent actions resource-safe",
        "defeated_by":     "S4 - Concurrent Executability",
        "defeating_logic": "S4 assembles the individual S1 applicability certificates for both actions. If any resource conflict or invariant interference existed, S1 would not hold for the affected action and S4 would fail. S4 holding therefore certifies no such conflict is present.",
    },
    "CQ8": {
        "challenge":       "Would reversing or removing this ordering leave a required start condition absent from the replayed state, or make the goal unreachable?",
        "attacks":         "S6 - Temporal Ordering Justification",
        "attacks_premise": "P3: reversal harms the plan",
        "s8_premise":      "P7: all orderings are necessary",
        "defeated_by":     "S2 - Causal Goal Support",
        "defeating_logic": "S2 identifies the causal dependency the ordering protects, which effect the predecessor produces and which later action requires it as a start condition. That chain is precisely what reversal would break.",
    },
    "CQ9": {
        "challenge":       "Does any concurrent action remove a condition that must remain continuously true during this action's execution?",
        "attacks":         "S7 - Invariant Maintenance Justification",
        "attacks_premise": "P2: invariant not disrupted by a concurrent action",
        "s8_premise":      "P8: all invariants are maintained",
        "defeated_by":     "S5 - Resource and Concurrency Feasibility",
        "defeating_logic": "S5's Premise 3 directly certifies mutual invariant compatibility: no invariant condition required by one concurrent action is removed by any effect of the other during their shared window.",
    },
}


# --- Communication language -------------------------------------------------

class Locution(str, Enum):
    # Explainee moves
    OPEN            = "open"
    CHALLENGE       = "challenge"
    WHY             = "why"
    UNDERSTAND      = "understand"
    NOT_UNDERSTAND  = "not_understand"
    CLOSE           = "close"
    # Explainer moves
    ASSERT          = "assert"
    JUSTIFY         = "justify"
    CONCEDE         = "concede"
    DECLARE_NA      = "declare_na"
    GROUND          = "ground"
    REFORMULATE     = "reformulate"


EXPLAINEE_LOCUTIONS = {
    Locution.OPEN, Locution.CHALLENGE, Locution.WHY,
    Locution.UNDERSTAND, Locution.NOT_UNDERSTAND, Locution.CLOSE,
}

EXPLAINER_LOCUTIONS = {
    Locution.ASSERT, Locution.JUSTIFY, Locution.CONCEDE,
    Locution.DECLARE_NA, Locution.GROUND, Locution.REFORMULATE, Locution.CLOSE,
}


@dataclass(frozen=True)
class Move:
    """A single dialogue move.

    speaker   "U" for the explainee, "E" for the explainer.
    locution  the speech act performed.
    target    the node identifier the move concerns, or None.
    content   structured payload, for rendering by an interface.
    text      a plain language rendering, so every interface agrees.
    """
    speaker:  str
    locution: Locution
    target:   Optional[str] = None
    content:  Dict[str, Any] = field(default_factory=dict, compare=False)
    text:     str = field(default="", compare=False)

    def label(self) -> str:
        """Short label suitable for a button."""
        if self.locution is Locution.CHALLENGE:
            return "%s . action %s" % (self.content["cq"], self.content["action"])
        if self.locution is Locution.WHY:
            return "Why premise %d?" % (self.content["premise_index"] + 1)
        if self.locution is Locution.UNDERSTAND:
            return "I follow that"
        if self.locution is Locution.NOT_UNDERSTAND:
            return "I do not follow"
        if self.locution is Locution.OPEN:
            return "Explain this plan"
        if self.locution is Locution.CLOSE:
            return "End dialogue"
        return self.locution.value


class IllegalMove(Exception):
    """Raised when a move is played that the legal moves function forbids."""


# --- Helpers over the Module 1-4 data structures ----------------------------

def _step_index(step):
    """Read a step's index, tolerating both notebook field names.

    Module 1 writes "step_index".  Some downstream code uses "action_index".
    """
    if "action_index" in step:
        return step["action_index"]
    return step.get("step_index")


def _parse_action_key(key):
    """Turn a CQ record's Action(s) field into a list of action indices.

    Module 4 stores this as a string.  Single actions appear as "3".
    Concurrent and ordering pairs appear as "(3, 4)".
    """
    if isinstance(key, int):
        return [key]
    text = str(key).strip()
    if text.startswith("(") and "," in text:
        out = []
        for part in text.strip("()").split(","):
            part = part.strip()
            if part.lstrip("-").isdigit():
                out.append(int(part))
        return out
    if text.lstrip("-").isdigit():
        return [int(text)]
    return []


def _cq_node_id(record):
    """Stable identifier for a CQ instance."""
    return "%s@%s" % (record["CQ"], record["Action(s)"])


def _premise_line(premise):
    mark   = "holds" if premise.get("holds") else "FAILS"
    detail = premise.get("detail", "")
    body   = premise.get("label", "")
    return "[%s] %s. %s" % (mark, body, detail) if detail else "[%s] %s" % (mark, body)


# --- Dialogue state ---------------------------------------------------------

@dataclass
class DialogueState:
    """The complete state of a dialogue.

    answered   CQ node ids already answered by E, enforcing R6.
    grounded   (node id, premise index) pairs already grounded, R6.
    understood node ids on which U has played UNDERSTAND.
    focus      the CQ node id currently under discussion, or None.
    """
    answered:              set = field(default_factory=set)
    grounded:              set = field(default_factory=set)
    understood:            set = field(default_factory=set)
    focus:                 Optional[str] = None
    opened:                bool = False
    closed:                bool = False
    pending_reformulation: Optional[str] = None
    history:               List[Move] = field(default_factory=list)


# --- The protocol -----------------------------------------------------------

class ExplanationDialogue:
    """An explanation dialogue over the computed framework AF(P).

    The dialogue is driven entirely by the explainee.  Each explainee move is
    answered immediately by the explainer, so a caller only ever selects from
    legal_moves() and passes the choice to play().
    """

    def __init__(self, steps, s8_result, cq_results, labels, cq_meta=None):
        self.steps      = steps
        self.s8_result  = s8_result
        self.cq_results = cq_results
        self.labels     = labels
        self.cq_meta    = cq_meta if cq_meta is not None else CQ_META

        self.state = DialogueState()

        # Index CQ records by node id and by action, deduplicating so that
        # each (CQ, action key) pair is challengeable exactly once.
        self._by_node   = {}
        self._by_action = {}
        for record in cq_results:
            node = _cq_node_id(record)
            if node in self._by_node:
                continue
            self._by_node[node] = record
            for index in _parse_action_key(record["Action(s)"]):
                self._by_action.setdefault(index, []).append(node)

        # Prefer a human-readable display name where the domain supplies one,
        # so participants see programme language rather than identifiers.
        self._step_names = dict(
            (_step_index(s), s.get("display_name") or s.get("action_name", "unknown"))
            for s in self.steps
        )

    # -- verdict -------------------------------------------------------------

    @property
    def verdict(self):
        """The PSA(P) verdict under the grounded extension."""
        return "ACCEPTED" if self.labels.get("PSA_P") == "IN" else "REJECTED"

    @property
    def turn(self):
        """Whose turn it is.  Alternation is strict, and play() discharges
        E's obligation before returning, so it is always U's turn between
        calls."""
        return "E" if self.state.closed else "U"

    # -- legal moves ---------------------------------------------------------

    def legal_moves(self):
        """Every move the explainee may legally play right now.

        An interface must render only these.  This is the protocol's legal
        moves component and the single place legality is decided.
        """
        state = self.state
        if state.closed:
            return []

        # R1, the dialogue opens with OPEN and nothing else.
        if not state.opened:
            return [Move("U", Locution.OPEN, target="PSA_P",
                         text="Explain why this plan is or is not valid.")]

        moves = []

        # R6, NOT_UNDERSTAND obliges E to reformulate before anything else.
        if state.pending_reformulation is not None:
            return [
                Move("U", Locution.UNDERSTAND,
                     target=state.pending_reformulation,
                     content={"node": state.pending_reformulation},
                     text="I follow that now."),
                Move("U", Locution.CLOSE, text="End the dialogue."),
            ]

        # R4, drill into the premises of the scheme justifying the focused CQ.
        if state.focus is not None:
            record   = self._by_node[state.focus]
            premises = record.get("Defeating premises") or []
            for i, premise in enumerate(premises):
                if (state.focus, i) in state.grounded:
                    continue
                moves.append(Move(
                    "U", Locution.WHY, target=state.focus,
                    content={"node": state.focus, "premise_index": i,
                             "premise": premise},
                    text="Why does this hold? %s" % premise.get("label", ""),
                ))
            if state.focus not in state.understood:
                moves.append(Move("U", Locution.UNDERSTAND, target=state.focus,
                                  content={"node": state.focus},
                                  text="I follow that."))
                moves.append(Move("U", Locution.NOT_UNDERSTAND, target=state.focus,
                                  content={"node": state.focus},
                                  text="I do not follow that."))

        # R2 and R6, challenge any CQ instance not already answered.
        for index in sorted(self._by_action):
            for node in self._by_action[index]:
                if node in state.answered:
                    continue
                record = self._by_node[node]
                meta   = self.cq_meta.get(record["CQ"], {})
                moves.append(Move(
                    "U", Locution.CHALLENGE, target=node,
                    content={"cq": record["CQ"], "action": index,
                             "action_name": self._step_names.get(index, ""),
                             "node": node, "outcome": record["Outcome"]},
                    text=meta.get("challenge", record["CQ"]),
                ))

        moves.append(Move("U", Locution.CLOSE, text="End the dialogue."))
        return moves

    # -- playing a move ------------------------------------------------------

    def play(self, move):
        """Play an explainee move and return the explainer's reply moves.

        Raises IllegalMove if the move is not in legal_moves().  Enforcing
        legality here rather than trusting the interface is what makes the
        protocol real rather than decorative.
        """
        legal = self.legal_moves()
        if not any(self._same_move(move, candidate) for candidate in legal):
            raise IllegalMove(
                "%s on %s is not legal in the current dialogue state."
                % (move.locution.value, move.target)
            )
        self.state.history.append(move)
        replies = self._apply(move)
        self.state.history.extend(replies)
        return replies

    @staticmethod
    def _same_move(a, b):
        if a.locution is not b.locution or a.target != b.target:
            return False
        if a.locution is Locution.WHY:
            return a.content.get("premise_index") == b.content.get("premise_index")
        return True

    def _apply(self, move):
        """Effect rules.  Update state and produce the explainer's response."""
        state = self.state

        if move.locution is Locution.OPEN:
            state.opened = True
            return [self._assert_s8()]

        if move.locution is Locution.CLOSE:
            state.closed = True
            return [Move("E", Locution.CLOSE,
                         text="Dialogue closed. " + self._closing_summary())]

        if move.locution is Locution.CHALLENGE:
            node = move.content["node"]
            state.answered.add(node)
            record = self._by_node[node]
            reply  = self._answer_challenge(record, node)
            # Focus is retained only where a defeating scheme was supplied,
            # since only then is drill-down meaningful (R4).
            state.focus = node if reply.locution is Locution.JUSTIFY else None
            return [reply]

        if move.locution is Locution.WHY:
            node  = move.content["node"]
            index = move.content["premise_index"]
            state.grounded.add((node, index))
            return [self._ground_premise(node, index, move.content["premise"])]

        if move.locution is Locution.UNDERSTAND:
            if move.target is not None:
                state.understood.add(move.target)
            state.pending_reformulation = None
            state.focus = None
            return []

        if move.locution is Locution.NOT_UNDERSTAND:
            state.pending_reformulation = move.target
            return [self._reformulate(move.target)]

        raise IllegalMove("Unhandled locution %s." % move.locution)

    # -- explainer move constructors ----------------------------------------

    def _assert_s8(self):
        premises = self.s8_result.get("premises", [])
        lines = "\n".join(
            "P%d. %s" % (i + 1, _premise_line(p)) for i, p in enumerate(premises)
        )
        body = (
            "Plan summary argument. %s\n%s\n"
            "Verdict under grounded semantics: PSA(P) is %s."
            % (self.s8_result.get("conclusion", ""), lines, self.verdict)
        )
        return Move("E", Locution.ASSERT, target="PSA_P",
                    content={"scheme": self.s8_result, "verdict": self.verdict},
                    text=body)

    def _answer_challenge(self, record, node):
        """R3.  E must answer, and the answer is fixed by the recorded outcome.

        This is where faithfulness is enforced.  JUSTIFY is played only for a
        defeated CQ, CONCEDE only for one that succeeds.  E cannot conceal a
        succeeding CQ, because the branch is selected by the outcome and not
        by any presentational choice.
        """
        outcome = record["Outcome"]
        cq      = record["CQ"]
        meta    = self.cq_meta.get(cq, {})

        # The attack chain is stated before the answer, so the user always
        # sees WHICH scheme the question attacks, at which premise, and which
        # premise of the plan summary argument that premise supports. Without
        # it the reply names only the defeater, which leaves the structure of
        # the framework invisible.
        attacked = meta.get("attacks", record.get("Attacks via", ""))
        at_premise = meta.get("attacks_premise", "")
        psa_premise = meta.get("s8_premise", "")

        chain = "%s attacks %s" % (cq, attacked)
        if at_premise:
            chain += ", at %s" % at_premise
        chain += "."
        if psa_premise:
            chain += ("\nThat premise supports the plan summary argument at %s."
                      % psa_premise)

        if outcome == "defeated":
            premises = record.get("Defeating premises") or []
            lines = "\n".join(
                "P%d. %s" % (i + 1, _premise_line(p)) for i, p in enumerate(premises)
            )
            body = (
                "%s\n\nIt is DEFEATED by %s.\n%s\n%s\nConclusion. %s"
                % (chain, meta.get("defeated_by", record.get("Defeated by", "")),
                   meta.get("defeating_logic", record.get("Detail", "")),
                   lines, record.get("Defeating conclusion", ""))
            )
            return Move("E", Locution.JUSTIFY, target=node,
                        content={"record": record, "meta": meta}, text=body)

        if outcome == "succeeds":
            body = (
                "%s\n\nIt SUCCEEDS. Nothing defeats it, so the premise it "
                "attacks does not survive and the plan genuinely fails on this "
                "point.\n%s" % (chain, record.get("Detail", ""))
            )
            return Move("E", Locution.CONCEDE, target=node,
                        content={"record": record, "meta": meta}, text=body)

        body = ("%s\n\nIt does NOT APPLY here. %s No attack on the plan "
                "summary argument arises from it."
                % (chain, record.get("Detail", "")))
        return Move("E", Locution.DECLARE_NA, target=node,
                    content={"record": record, "meta": meta}, text=body)

    def _ground_premise(self, node, index, premise):
        """R4 and R5.  Ground a premise in the replayed state.

        S0 is reconstructed from the initial state and the effects of every
        action that has run up to the relevant time point, so nothing lies
        behind it.  This is the base case of the recursion.

        If premise provenance is added to Module 3 later, as a field naming
        the scheme instance that establishes the premise, R4 can be made
        genuinely recursive here.  The termination argument is unaffected,
        because R6 still admits each WHY at most once.
        """
        detail = premise.get("detail", "")
        body = (
            "Premise %d. %s\nThis %s in the replayed state. %s\n"
            "The replayed state is reconstructed from the initial state and "
            "the effects of every action that has run up to this point, so "
            "there is no further evidence behind it."
            % (index + 1, premise.get("label", ""),
               "holds" if premise.get("holds") else "does not hold", detail)
        )
        return Move("E", Locution.GROUND, target=node,
                    content={"premise": premise, "premise_index": index,
                             "s0": True},
                    text=body)

    def _reformulate(self, node):
        """Answer NOT_UNDERSTAND by re-expressing at plainer granularity,
        using the premise detail strings and the plain language challenge
        text rather than the formal premise labels."""
        if node is None or node not in self._by_node:
            return Move("E", Locution.REFORMULATE, target=node,
                        text="In plain terms, the system checked every "
                             "requirement the plan has to meet, and reports "
                             "whether each one was met.")
        record = self._by_node[node]
        meta   = self.cq_meta.get(record["CQ"], {})
        plain  = [p.get("detail") or p.get("label", "")
                  for p in (record.get("Defeating premises") or [])]
        plain  = [p for p in plain if p]
        body = ("Put more simply. The question was: %s\n"
                "The answer rests on the following.\n%s"
                % (meta.get("challenge", record["CQ"]),
                   "\n".join("- %s" % p for p in plain)))
        return Move("E", Locution.REFORMULATE, target=node,
                    content={"record": record}, text=body)

    def _closing_summary(self):
        o = self.outcome()
        return ("%d of %d critical questions raised, %d premises grounded. "
                "PSA(P) verdict conveyed: %s."
                % (o["cqs_challenged"], o["cqs_available"],
                   o["premises_grounded"], o["verdict"]))

    # -- termination and outcome --------------------------------------------

    def is_terminated(self):
        """Terminates on CLOSE, or by exhaustion when no substantive
        explainee move remains."""
        if self.state.closed:
            return True
        remaining = [m for m in self.legal_moves()
                     if m.locution in (Locution.CHALLENGE, Locution.WHY)]
        return (not remaining) and self.state.opened

    def move_bound(self):
        """Upper bound on explainee moves, used in the termination argument.

        Each CQ instance is challengeable at most once (R6).  Each premise of
        each defeating scheme is groundable at most once (R6).  Each node
        admits at most one NOT_UNDERSTAND, which is followed by exactly one
        UNDERSTAND or by CLOSE.  Adding the opening and closing moves,

            bound = |CQ instances| + total premises + 2|CQ instances| + 2

        which is finite because AF(P) is finite.  Every explainee move
        removes at least one move from the legal set and adds none, so every
        dialogue terminates within this bound.
        """
        challenges = len(self._by_node)
        whys = sum(len(r.get("Defeating premises") or [])
                   for r in self._by_node.values())
        return challenges + whys + 2 * challenges + 2

    def outcome(self):
        """Outcome rules.  Not win or lose, but verdict plus understanding."""
        return {
            "verdict":            self.verdict,
            "terminated":         self.is_terminated(),
            "closed_by_user":     self.state.closed,
            "cqs_available":      len(self._by_node),
            "cqs_challenged":     len(self.state.answered),
            "premises_grounded":  len(self.state.grounded),
            "nodes_understood":   len(self.state.understood),
            "moves_played":       len(self.state.history),
            "explainee_moves":    sum(1 for m in self.state.history
                                      if m.speaker == "U"),
            "move_bound":         self.move_bound(),
            "conceded_cqs":       sorted(
                n for n in self.state.answered
                if self._by_node[n]["Outcome"] == "succeeds"),
        }

    def transcript(self):
        """A serialisable transcript, for user study logging and analysis.

        Dialogue depth, breadth of critical questions raised, the ratio of
        drill-down to top-level moves, and whether a participant closed early
        or exhausted the tree are all derivable from these rows.
        """
        return [{"index": i, "speaker": m.speaker,
                 "locution": m.locution.value, "target": m.target,
                 "text": m.text}
                for i, m in enumerate(self.state.history)]

    def reset(self):
        """Return the dialogue to its opening state, for a new participant."""
        self.state = DialogueState()


# --- Faithfulness check -----------------------------------------------------

def check_faithfulness(dialogue):
    """Verify the dialogue so far is faithful to the grounded extension.

    Faithfulness holds when, for every answered CQ instance, E played JUSTIFY
    exactly where the outcome is defeated, CONCEDE exactly where it succeeds,
    and DECLARE_NA exactly where it does not apply.  This is the property to
    state and prove alongside Theorem 1 and Corollary 1.  Running it as a
    test gives empirical support for the proof and guards against any future
    edit that lets an interface present a succeeding CQ as answered.
    """
    problems = []
    expected = {"defeated": Locution.JUSTIFY,
                "succeeds": Locution.CONCEDE,
                "n/a":      Locution.DECLARE_NA}
    for move in dialogue.state.history:
        if move.speaker != "E" or move.locution not in expected.values():
            continue
        record = move.content.get("record")
        if record is None:
            continue
        if expected.get(record["Outcome"]) is not move.locution:
            problems.append(
                "%s on %s has outcome %s but E played %s."
                % (record["CQ"], record["Action(s)"],
                   record["Outcome"], move.locution.value))
    return (not problems), problems


# Imported as a module by streamlit_app.py and by the Colab notebook.

