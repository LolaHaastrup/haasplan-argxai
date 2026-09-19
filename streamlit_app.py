"""
streamlit_app.py
================

HaasPlan XAI — argumentation-based explanations for collaborative planning.

Two modes share one engine.

  Explore      pick a domain, inspect the framework, hold a dialogue with the
               system. This is the demonstration mode.
  User study   the six-stage participant flow, ending in a questionnaire.

ARCHITECTURE
------------
The dialogue is driven by explanation_dialogue.py, which is the same file as
Module 10b of the notebook. Nothing about the protocol is reimplemented here.
The interface renders whatever legal_moves() returns and routes clicks through
play(), so an illegal move cannot be produced by clicking.

The framework is not recomputed at run time. The app loads the JSON artefacts
written by the notebook, which were produced for BOTH domains by one extraction
implementation (Module 3b). Adding a domain is an entry in domains.py plus four
files in data/.
"""

import hashlib
import json
import random
import string
import time
from pathlib import Path

import streamlit as st

import domains as dm
import plain
import schemes as sch
import study_config as cfg
from explanation_dialogue import (CQ_META, ExplanationDialogue, IllegalMove,
                                  Locution, check_faithfulness)
from storage import build_record, get_store, to_csv

CONSENT_VERSION = "2026-09-v1"

#: Share of study participants shown a failing plan. The study reports the two
#: groups descriptively rather than testing a difference between them, so the
#: faulty arm only needs to be large enough to populate F1, F2 and
#: found_failure. At 0.30 you need roughly 50 participants to reach 15 faulty.
#: Raise it to 0.50 if you later decide to compare the arms statistically.
FAULTY_SHARE = 0.30

APP_DIR = Path(__file__).parent
DATA_DIR = APP_DIR / "data"
LOGO = APP_DIR / "assets" / "uoh_logo_web.png"

STUDY_STAGES = ["information", "consent", "choose", "questionnaire",
                "debrief"]

INK = "#0B2D8F"          # University of Huddersfield blue
MUTED = "#5B6472"
LINE = "#DCE1EA"


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

@st.cache_data(show_spinner=False)
def load_domain(key: str):
    """Load one domain's artefacts. Returns (steps, s8, cq, labels, names)."""
    def read(suffix, required=True):
        path = DATA_DIR / f"{key}_{suffix}.json"
        if not path.exists():
            if required:
                raise FileNotFoundError(path.name)
            return None
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)

    return (read("plan"), read("S8_PSA"), read("CQ_results"),
            read("AF_labels"), read("display_names", required=False) or {})


def available_domains():
    """Only domains whose artefacts are actually present."""
    out = []
    for domain in dm.REGISTRY:
        if (DATA_DIR / f"{domain.key}_plan.json").exists():
            out.append(domain)
    return out


@st.cache_data(show_spinner=False)
def display_names_for(key: str):
    """Action identifier to the name a reader should see, per domain."""
    steps, _s8, _cq, _labels, names = load_domain(key)
    if names:
        return dict(names)
    return {step["action_name"]: step.get("display_name", step["action_name"])
            for step in steps}


@st.cache_data(show_spinner=False)
def failure_detail_for(key: str) -> dict:
    """Everything the framework knows about why this plan fails.

    Read from the artefacts, so it is recorded whether or not the participant
    ever asked the failing question. Returns empty strings for a valid plan.
    """
    steps, _s8, cq, _labels, names = load_domain(key)
    failing = [r for r in cq if r["Outcome"] == "succeeds"]
    if not failing:
        return {k: "" for k in ("failure_cq", "failure_step", "failure_action",
                                "failure_scheme", "failure_premise",
                                "failure_detail", "failure_reason")}
    first = failing[0]
    meta = CQ_META.get(first["CQ"], {})
    action_key = str(first["Action(s)"])
    step = next((s for s in steps
                 if str(s.get("step_index", s.get("action_index"))) == action_key),
                {})
    raw_name = step.get("action_name", "")
    action = (names or {}).get(raw_name, step.get("display_name", raw_name))
    detail = str(first.get("Detail", "")).replace("—", "").strip(" .")

    reason = (f"{action or 'Step ' + action_key} fails {first['CQ']}, which "
              f"attacks {meta.get('attacks', '')} at "
              f"{meta.get('attacks_premise', '')}. {detail}.")

    return {
        "failure_cq": first["CQ"],
        "failure_step": action_key,
        "failure_action": action,
        "failure_scheme": meta.get("attacks", ""),
        "failure_premise": meta.get("attacks_premise", ""),
        "failure_detail": detail,
        "failure_reason": reason,
    }


@st.cache_data(show_spinner=False)
def failure_point_for(key: str) -> str:
    """Where this plan fails, as the framework sees it.

    Returns something like "CQ5@10", or an empty string for a valid plan.
    Read from the artefacts rather than from the participant's session, so it
    is recorded whether or not they ever asked the failing question.
    """
    _steps, _s8, cq, _labels, _names = load_domain(key)
    failing = [f"{r['CQ']}@{r['Action(s)']}" for r in cq
               if r["Outcome"] == "succeeds"]
    return "; ".join(failing)


def build_dialogue(key: str) -> ExplanationDialogue:
    steps, s8, cq, labels, names = load_domain(key)
    for step in steps:
        step.setdefault("display_name",
                        names.get(step["action_name"], step["action_name"]))
    return ExplanationDialogue(steps, s8, cq, labels, CQ_META)


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

def new_code() -> str:
    return "P-" + "".join(random.choices(string.ascii_uppercase + string.digits,
                                         k=8))


def init_state():
    """One session per domain, kept side by side.

    A participant may look at both plans. If switching domain discarded the
    previous conversation, the study could only ever report on whichever one
    they happened to open last, and a participant who explored transport
    thoroughly then glanced at the payment workflow would be recorded as a
    payment-workflow participant with almost no engagement.

    So each domain keeps its own dialogue and its own log, and the study asks
    which one the participant is reporting on. The answer is then a fact they
    stated rather than an inference from the order they clicked in.
    """
    ss = st.session_state
    ss.setdefault("mode", None)
    ss.setdefault("stage", "information")
    ss.setdefault("participant_code", new_code())
    ss.setdefault("submitted", False)
    # Opens on the assigned variant, so the 70/30 split is the default a
    # participant meets. They can switch version in Explore, and if they do,
    # condition_choice records that their condition was self-selected rather
    # than assigned.
    if "domain_key" not in ss:
        ss.domain_key = domain_for(dm.families_present(
            {d.key for d in available_domains()})[0])
    ss.setdefault("study_domain_key", None)                # chosen for the study
    ss.setdefault("condition", None)                       # valid or faulty
    ss.setdefault("condition_choice", "assigned")          # assigned or self_selected
    ss.setdefault("started_at", {})
    ss.setdefault("dialogues", {})
    ss.setdefault("logs", {})
    ensure_session(ss.domain_key)


def assigned_condition() -> str:
    """Valid or faulty, assigned rather than chosen.

    Derived from the participant's own randomly generated code, so assignment
    needs no shared counter and no read of the store. That matters: an earlier
    version alternated on the number of rows already submitted, which meant
    everyone arriving before the first submission landed in the same arm.

    The share comes from FAULTY_SHARE above.
    """
    ss = st.session_state
    if ss.get("condition"):
        return ss.condition
    digest = hashlib.sha256(ss.participant_code.encode()).digest()
    bucket = int.from_bytes(digest[:4], "big") % 100
    ss.condition = "faulty" if bucket < int(FAULTY_SHARE * 100) else "valid"
    ss.condition_choice = "assigned"
    return ss.condition


def domain_for(family: str) -> str:
    """The artefact key for this family in the participant's condition."""
    try:
        return dm.variant(family, assigned_condition()).key
    except KeyError:
        return dm.variant(family, "valid").key


def ensure_session(key: str):
    """Create this domain's dialogue and log if it does not have one yet."""
    ss = st.session_state
    if key not in ss.dialogues:
        ss.dialogues[key] = build_dialogue(key)
        ss.logs[key] = []


def explored_domains():
    """Domains the participant has actually held a conversation about."""
    ss = st.session_state
    return [key for key, dialogue in ss.dialogues.items()
            if dialogue.outcome()["explainee_moves"] > 0]


def in_explore() -> bool:
    return st.session_state.mode == "explore"


def active_dialogue():
    """The dialogue for the domain currently open in Explore."""
    ss = st.session_state
    ensure_session(ss.domain_key)
    return ss.dialogues[ss.domain_key]


def active_log() -> list:
    ss = st.session_state
    ensure_session(ss.domain_key)
    return ss.logs[ss.domain_key]


def active_domain_key() -> str:
    ss = st.session_state
    return ss.domain_key


def study_in_progress() -> bool:
    """True once a participant is past consent and has not yet submitted.

    Deliberately independent of the CURRENT mode. The point of this flag is to
    say "a study session is open", which has to remain true while the user is
    looking at Explore. Checking mode == "study" here would make it false in
    the only situation it exists for.
    """
    ss = st.session_state
    return (ss.stage not in ("information", "consent") and not ss.submitted)


def switch_domain(key: str):
    """Open a different domain in Explore, keeping the previous session."""
    ss = st.session_state
    ensure_session(key)
    ss.domain_key = key


def start_new_participant():
    """Reset everything that belongs to one participant.

    Without this, a second run in the same browser session reuses the first
    participant's code and their answered widgets, so two people look like
    one row in the sheet and the second set of answers is pre-filled with the
    first set. Consent ticks, questionnaire responses, the dialogue and the
    participant code are all cleared here.
    """
    ss = st.session_state
    for key in list(ss.keys()):
        if (key.startswith("consent_") or key.startswith("q_")
                or key.startswith("open_") or key.startswith("pick_")):
            del ss[key]
    ss.participant_code = new_code()
    ss.study_domain_key = None
    ss.condition = None          # reassigned for the next participant
    ss.condition_choice = "assigned"
    ss.submitted = False
    ss.save_note = ""
    # The dialogue and the log are deliberately NOT cleared. The participant
    # holds their dialogue in Explore, and the questionnaire asks about it, so
    # wiping it here would erase the very session being reported on.


def goto(stage: str):
    st.session_state.stage = stage
    st.rerun()


# ---------------------------------------------------------------------------
# Chrome
# ---------------------------------------------------------------------------

@st.cache_data(show_spinner=False)
def _logo_uri() -> str:
    """The logo as a cached data URI.

    Read once per session rather than re-sent through Streamlit's media
    handler on every rerun, and rendered in the same markdown block as the
    title so the header is one element instead of two columns.
    """
    import base64
    if not LOGO.exists():
        return ""
    return ("data:image/png;base64,"
            + base64.b64encode(LOGO.read_bytes()).decode())


def header():
    left, right = st.columns([1, 2], vertical_alignment="center")
    with left:
        uri = _logo_uri()
        if uri:
            st.markdown(
                f"<img src='{uri}' alt='University of Huddersfield' "
                f"style='width:240px;max-width:100%;'>",
                unsafe_allow_html=True)
        else:
            st.markdown("**University of Huddersfield**")
    with right:
        st.markdown(
            f"<div style='text-align:right;'>"
            f"<div style='font-size:16px;font-weight:700;color:{INK};'>"
            f"HaasPlan XAI</div>"
            f"<div style='font-size:12px;color:{MUTED};line-height:1.5;'>"
            f"Argumentation-Based Explanations for Collaborative Planning<br>"
            f"School of Computing and Engineering &nbsp;&middot;&nbsp; "
            f"{cfg.PROJECT_CODE}</div></div>",
            unsafe_allow_html=True)
    st.markdown(f"<hr style='margin:10px 0 18px;border:none;"
                f"border-top:2px solid {INK};'>", unsafe_allow_html=True)


def mode_switch():
    """A persistent switch between the two modes, on every page.

    Safe to press at any point, because the modes keep separate dialogues.
    A participant mid-study who wanders into Explore returns to exactly the
    stage they left. It is hidden on the landing page, where the two cards
    already do this job.
    """
    if st.session_state.mode is None:
        return

    left, middle, right = st.columns([1, 1, 2])
    explore_now = in_explore()
    with left:
        if st.button("Explore", type="primary" if explore_now else "secondary",
                     width="stretch", key="sw_explore",
                     help="Pick a domain and question the system freely. "
                          "Nothing is recorded."):
            st.session_state.mode = "explore"
            st.rerun()
    with middle:
        if st.button("User study",
                     type="secondary" if explore_now else "primary",
                     width="stretch", key="sw_study",
                     help="The participant flow, from information sheet to "
                          "questionnaire."):
            st.session_state.mode = "study"
            st.rerun()
    with right:
        if study_in_progress() and explore_now:
            st.caption("A study session is open. Your place is kept.")
        elif st.button("Home", width="stretch", key="sw_home"):
            st.session_state.mode = None
            st.rerun()

    st.markdown(f"<div style='height:6px;border-bottom:1px solid {LINE};'></div>",
                unsafe_allow_html=True)


def domain_badge(domain):
    st.markdown(
        f"<span style='display:inline-block;background:#EAF0FA;color:{INK};"
        f"padding:3px 10px;border-radius:12px;font-size:12px;"
        f"font-weight:600;'>{domain.short}</span>",
        unsafe_allow_html=True)


def workflow_selector(label="Plan", key="dsel"):
    """Offer the workflows. Returns a family name, not a variant key.

    Which variant is served is decided by the caller, because Explore lets the
    participant choose valid or faulty while the study assigns it. Having the
    selector also decide made the two fight: the selector reset the condition
    the radio had just set, so switching version appeared to do nothing.
    """
    available = {d.key for d in available_domains()}
    families = dm.families_present(available)
    if not families:
        return None
    if len(families) == 1:
        return families[0]
    current = dm.family_of(st.session_state.domain_key)
    index = families.index(current) if current in families else 0
    return st.selectbox(label, families, index=index,
                        format_func=dm.family_label, key=key)


def verdict_panel(dialogue, domain, teaching=True):
    verdict = dialogue.verdict
    with st.container(border=True):
        st.markdown(f"**Is this {domain.subject} plan valid?**")
        if verdict == "ACCEPTED":
            st.success("The system's verdict is that the plan is VALID.",
                       icon=":material/check_circle:")
        else:
            st.error("The system's verdict is that the plan is NOT valid.",
                     icon=":material/cancel:")
            # The framework knows exactly where the plan fails. Making the
            # participant hunt through forty buttons for it was a UI choice,
            # not a principled one, and testing showed nobody found it.
            info = failure_detail_for(st.session_state.domain_key)
            if info["failure_cq"]:
                st.markdown(
                    f"**Where it fails.** Step {info['failure_step']}, "
                    f"{info['failure_action']}. {info['failure_detail']}.")
                st.caption(
                    f"This breaks {info['failure_scheme']} at "
                    f"{info['failure_premise']}. Put {info['failure_cq']} to "
                    f"the system on that step to see the evidence behind it.")

        if teaching:
            with st.expander("How the system reaches a verdict"):
                st.markdown(sch.PSA_EXPLANATION)

        with st.expander("The eight premises of the Plan Summary Argument"):
            st.caption("Each premise is supplied by one argument scheme, named "
                       "in brackets.")
            for i, premise in enumerate(dialogue.s8_result.get("premises", [])):
                holds = premise.get("holds")
                icon = "check" if holds else "close"
                st.markdown(f":material/{icon}: **P{i + 1}.** "
                            f"{premise.get('label', '')}")

        if teaching:
            with st.expander("What each argument scheme claims"):
                for scheme_id, (short, description) in sch.SCHEMES.items():
                    st.markdown(f"**{scheme_id} — {short}.** {description}")


def plan_table(dialogue, domain):
    rows = []
    for step in dialogue.steps:
        rows.append({
            "#": step.get("step_index", step.get("action_index")),
            "Action": step.get("display_name", step.get("action_name")),
            "Start": step.get("start"),
            "End": step.get("end"),
            domain.columns.get("resource", "Resource"): step.get("resource", "-"),
        })
    st.dataframe(rows, hide_index=True, width="stretch")


SPEAKER = {
    Locution.ASSERT: "The system states",
    Locution.JUSTIFY: "The system answers",
    Locution.CONCEDE: "The system concedes",
    Locution.DECLARE_NA: "The system replies",
    Locution.GROUND: "The system explains",
    Locution.REFORMULATE: "The system rephrases",
    Locution.CLOSE: "The system closes",
}


def render_log():
    if not active_log():
        st.caption("The conversation will appear here.")
        return
    for speaker, title, text in active_log():
        role = "user" if speaker == "U" else "assistant"
        avatar = ":material/person:" if speaker == "U" else ":material/smart_toy:"
        with st.chat_message(role, avatar=avatar):
            if title:
                st.markdown(f"**{title}**")
            st.markdown(text.replace("\n", "  \n"))


def play_move(move):
    dialogue = active_dialogue()
    try:
        replies = dialogue.play(move)
    except IllegalMove as exc:
        st.warning(str(exc))
        return
    key = st.session_state.domain_key
    st.session_state.started_at.setdefault(key, time.time())
    # Displayed text is rendered into plain language. The stored transcript
    # keeps the framework's own wording, so analysis is unaffected.
    names = display_names_for(key)
    active_log().append(("U", "You ask", plain.render(move.text, names)))
    for reply in replies:
        active_log().append(
            ("E", SPEAKER.get(reply.locution, "The system"),
             plain.render(reply.text, names)))
    st.rerun()


def move_buttons(dialogue, columns=3):
    """Render exactly the legal moves.

    Grouping is presentational only. The protocol decides what exists here;
    this function decides where it sits on screen. A move that legal_moves()
    does not return cannot be rendered, so an illegal move cannot be clicked.
    """
    moves = dialogue.legal_moves()
    if not moves:
        return
    names = {s.get("step_index", s.get("action_index")):
             s.get("display_name", s.get("action_name")) for s in dialogue.steps}

    focus_moves = [m for m in moves if m.locution in
                   (Locution.WHY, Locution.UNDERSTAND, Locution.NOT_UNDERSTAND,
                    Locution.OPEN)]
    challenges = [m for m in moves if m.locution is Locution.CHALLENGE]
    closing = [m for m in moves if m.locution is Locution.CLOSE]

    # Whatever is in focus comes first, because it is the live thread.
    if focus_moves:
        whys = [m for m in focus_moves if m.locution is Locution.WHY]
        if whys:
            # Testing showed nobody used these. Labelling them as a prompt
            # rather than leaving them as bare buttons is the cheapest thing
            # that might change that.
            st.markdown(f"<div style='font-size:13px;color:{INK};"
                        f"font-weight:600;margin:6px 0 2px;'>"
                        f"Not convinced? Ask the system to justify any premise "
                        f"of that answer</div>", unsafe_allow_html=True)
        cols = st.columns(min(columns, len(focus_moves)))
        for i, move in enumerate(focus_moves):
            kind = "primary" if move.locution in (Locution.OPEN,
                                                  Locution.UNDERSTAND) else "secondary"
            with cols[i % len(cols)]:
                if st.button(move.label()[:46], key=f"f_{i}", help=move.text,
                             type=kind, width="stretch"):
                    play_move(move)

    # Challenges grouped by the action they concern, so the button count per
    # screen stays readable on a domain with 90 legal challenges.
    if challenges:
        grouped = {}
        for move in challenges:
            grouped.setdefault(move.content["action"], []).append(move)
        st.markdown(f"<div style='font-size:13px;color:{MUTED};margin:10px 0 2px;'>"
                    f"Questions you can put to the system, by step</div>",
                    unsafe_allow_html=True)
        for action_index in sorted(grouped):
            group = grouped[action_index]
            title = f"Step {action_index} · {names.get(action_index, '')}"
            with st.expander(f"{title}  ({len(group)})",
                             expanded=(action_index == min(grouped))):
                cols = st.columns(columns)
                for i, move in enumerate(group):
                    with cols[i % columns]:
                        if st.button(move.content["cq"],
                                     key=f"c_{action_index}_{i}",
                                     help=move.text, width="stretch"):
                            play_move(move)
                st.caption(group[0].text[:150] if len(group) == 1 else
                           "Hover a button to see the question it asks.")

    if closing:
        st.markdown("")
        if st.button("End dialogue", key="close_btn", type="secondary"):
            play_move(closing[0])


def dialogue_hint(dialogue):
    state = dialogue.state
    if state.pending_reformulation is not None:
        return ("You said you did not follow. The system has rephrased, and "
                "under rule R6 it may not simply repeat itself.")
    if state.focus is not None:
        return ("Question any premise of the answer (rule R4), or move on to "
                "another critical question.")
    if not state.opened:
        return "Open the dialogue to receive the plan summary argument (rule R1)."
    return "Choose a critical question to put to the system (rule R2)."


def dialogue_status(dialogue):
    o = dialogue.outcome()
    st.markdown(
        f"<div style='font-size:12px;color:{MUTED};border-top:1px solid {LINE};"
        f"padding-top:8px;margin-top:4px;'>"
        f"Critical questions raised {o['cqs_challenged']} of {o['cqs_available']}"
        f" &nbsp;·&nbsp; premises grounded {o['premises_grounded']}"
        f" &nbsp;·&nbsp; your moves {o['explainee_moves']} of a bound of "
        f"{o['move_bound']}</div>", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Landing
# ---------------------------------------------------------------------------

def stage_landing():
    st.markdown(
        f"<div style='font-size:19px;font-weight:600;color:{INK};'>"
        f"Explaining automated plans through argumentation</div>"
        f"<div style='color:{MUTED};font-size:14px;margin:6px 0 14px;'>"
        f"This system checks whether a scheduled plan is valid, then explains "
        f"its verdict through a dialogue you can interrogate. Every answer it "
        f"gives is drawn from a formal argumentation framework computed in "
        f"advance, so it can say exactly which requirement a plan fails."
        f"</div>", unsafe_allow_html=True)

    with st.container(border=True):
        st.markdown("**How to take part**")
        st.markdown(
            "There are two parts and together they take about 25 to 30 "
            "minutes.\n\n"
            "**First, explore the system.** Open the explorer below and choose "
            "one of the two plans, a transport journey or a social protection "
            "payment workflow. Pick whichever is closer to work you have done. "
            "The system gives its verdict on the plan, and you question it "
            "using the buttons offered. Only the questions that make sense at "
            "that point appear, so they change as you go. Ask as many or as "
            "few as you like.\n\n"
            "**Then run the user study.** It takes four steps, with a progress "
            "bar at the top.\n\n"
            "1. **Information sheet.** What the study is about and what happens "
            "to your answers.\n"
            "2. **Consent.** Six statements to confirm before you begin.\n"
            "3. **Choose a plan.** You say which of the plans you explored "
            "the questionnaire is about.\n"
            "4. **Questionnaire.** Short statements and three open questions "
            "about the explanation you just explored.\n"
            "5. **Finish.** Your responses are recorded anonymously and you "
            "can close the window.\n\n"
            "You can stop at any point before submitting by closing the "
            "window, and nothing will be recorded.")

    left, right = st.columns(2)
    with left:
        with st.container(border=True):
            st.markdown("**Explore the system**")
            st.caption("Pick a domain, inspect the framework, and question the "
                       "system freely. No consent form, nothing recorded.")
            if st.button("Open explorer", type="primary", width="stretch"):
                st.session_state.mode = "explore"
                st.rerun()
    with right:
        with st.container(border=True):
            st.markdown("**Run the user study**")
            st.caption("Information sheet, consent, then the questionnaire "
                       "about the plan you explored. Explore first.")
            if st.button("Start the study", width="stretch"):
                start_new_participant()
                st.session_state.mode = "study"
                st.session_state.stage = "information"
                st.rerun()

    with st.container(border=True):
        st.markdown("**Plans available**")
        # One card per workflow, not per variant. The registry holds a valid
        # and a faulty version of each, which made this list read as four
        # separate domains and confused a tester.
        available = {d.key for d in available_domains()}
        for family in dm.families_present(available):
            domain = dm.get(dm.variant(family, "valid").key)
            steps, _s8, cq, _labels, _n = load_domain(domain.key)
            st.markdown(
                f"<div style='margin:6px 0;'><b>{domain.name}</b>"
                f"<div style='color:{MUTED};font-size:13px;'>{domain.blurb}</div>"
                f"<div style='color:{MUTED};font-size:12px;margin-top:2px;'>"
                f"{len(steps)} steps &nbsp;·&nbsp; {len(cq)} critical question "
                f"instances</div></div>",
                unsafe_allow_html=True)
        st.caption("Both frameworks are produced by one extraction "
                   "implementation, which reads conditions and effects from the "
                   "planning model rather than matching on action names.")


# ---------------------------------------------------------------------------
# Explore mode
# ---------------------------------------------------------------------------

def stage_explore():
    family = workflow_selector("Plan", key="explore_domain")
    if family is None:
        st.error("No plan artefacts found in data/.")
        return

    # Free choice of version here, because Explore records nothing. Two
    # testers asked for it. In the study the condition stays assigned.
    wanted = st.radio(
        "Plan version", ["valid", "faulty"],
        index=0 if dm.get(st.session_state.domain_key).condition == "valid" else 1,
        format_func=lambda c: ("A plan that works" if c == "valid"
                               else "A plan that fails"),
        horizontal=True, key="explore_condition")

    try:
        target = dm.variant(family, wanted).key
    except KeyError:
        target = dm.variant(family, "valid").key
    if target != st.session_state.domain_key:
        if wanted != assigned_condition():
            st.session_state.condition_choice = "self_selected"
        switch_domain(target)
        st.rerun()

    domain = dm.get(target)
    st.caption(domain.blurb)
    dialogue = active_dialogue()

    tab_plan, tab_framework, tab_dialogue = st.tabs(
        [domain.plan_caption, "Framework", "Dialogue"])

    with tab_plan:
        plan_table(dialogue, domain)
        st.caption("The framework explains the plan.")

    with tab_framework:
        framework_tab(domain)

    with tab_dialogue:
        verdict_panel(dialogue, domain)
        with st.container(border=True, height=360):
            render_log()
        if dialogue.state.closed:
            st.info("Dialogue closed. " + dialogue._closing_summary())
            if st.button("Start a new dialogue"):
                switch_domain(domain.key)
                key = st.session_state.domain_key
                st.session_state.dialogues[key] = build_dialogue(key)
                st.session_state.logs[key] = []
                st.session_state.started_at.pop(key, None)
                st.rerun()
        else:
            st.caption(dialogue_hint(dialogue))
            move_buttons(dialogue)
        dialogue_status(dialogue)


def framework_tab(domain):
    """Attack and defeat structure, plus the framework diagram."""
    steps, s8, cq, labels, _names = load_domain(domain.key)

    counts = {}
    for row in cq:
        bucket = counts.setdefault(row["CQ"], {})
        bucket[row["Outcome"]] = bucket.get(row["Outcome"], 0) + 1

    st.markdown("**Critical question coverage, attack and defeat structure**")
    st.caption("Each critical question attacks one premise of one scheme. That "
               "premise in turn supports one premise of the Plan Summary "
               "Argument. Where another scheme answers the question, the "
               "question is defeated.")

    table = []
    for cq_id in sorted(counts, key=lambda c: int(c[2:])):
        meta = CQ_META.get(cq_id, {})
        outcome = counts[cq_id]
        table.append({
            "CQ": cq_id,
            "Scheme attacked": meta.get("attacks", ""),
            "At premise": meta.get("attacks_premise", ""),
            "Supports PSA at": meta.get("s8_premise", ""),
            "Defeated by": meta.get("defeated_by", ""),
            "Instances": sum(outcome.values()),
            "Defeated": outcome.get("defeated", 0),
            "Succeeds": outcome.get("succeeds", 0),
            "N/A": outcome.get("n/a", 0),
        })
    st.dataframe(table, hide_index=True, width="stretch")

    succeeding = [r for r in cq if r["Outcome"] == "succeeds"]
    if succeeding:
        st.markdown("**Critical questions that succeed**")
        st.caption("Each is an unanswered attack on the plan.")
        for row in succeeding:
            st.markdown(f"- `{row['CQ']}` on {row['Action(s)']} — "
                        f"{row['Detail'][:140]}")
    else:
        st.success("Every applicable critical question is defeated.",
                   icon=":material/verified:")

    st.markdown("**The argumentation framework**")
    st.caption("Red arrows are attacks, green arrows are defeats. Nodes are "
               "grouped by critical question rather than by instance, so the "
               "structure stays readable. Per-instance counts are in the table "
               "above.")
    try:
        st.graphviz_chart(sch.build_dot(cq, CQ_META))
    except Exception as exc:
        st.caption(f"The diagram could not be rendered here ({exc}). The table "
                   f"above carries the same information.")


# ---------------------------------------------------------------------------
# Study mode
# ---------------------------------------------------------------------------

def study_progress():
    index = STUDY_STAGES.index(st.session_state.stage)
    st.progress((index + 1) / len(STUDY_STAGES),
                text=f"Step {index + 1} of {len(STUDY_STAGES)}")


def stage_information():
    st.subheader("Participant information sheet")
    st.caption(f"Your participant code for this session is "
               f"**{st.session_state.participant_code}**.")
    for heading, body in cfg.PARTICIPANT_INFORMATION:
        with st.container(border=True):
            st.markdown(f"**{heading}**")
            st.markdown(body)
    st.info(cfg.RETENTION_STATEMENT, icon=":material/schedule:")
    st.caption(f"*{cfg.PIS_CLOSING}*")
    st.divider()
    if st.button("Continue to consent", type="primary"):
        goto("consent")


def stage_consent():
    """Consent inside a form.

    Without a form, Streamlit reruns the whole script on every checkbox tick.
    Six ticks is six full reruns, which on a free container reads as the page
    hanging. A form batches them: the widgets update locally and the script
    runs once, when the participant submits.
    """
    st.subheader("Consent form")
    st.markdown(cfg.CONSENT_PREAMBLE)

    with st.form("consent_form", border=True):
        ticks = [st.checkbox(f"**{i + 1}.**  {item}", key=f"consent_{i}")
                 for i, item in enumerate(cfg.CONSENT_ITEMS)]
        st.caption(f"*{cfg.CONSENT_NOTE}*")
        submitted = st.form_submit_button("I consent, begin the study",
                                          type="primary")

    st.caption(cfg.CONSENT_TIMESTAMP_NOTE)

    if submitted:
        if all(ticks):
            goto("choose")
        else:
            missing = [str(i + 1) for i, t in enumerate(ticks) if not t]
            st.error(f"Please tick every statement to continue. "
                     f"Outstanding: {', '.join(missing)}.")

    if st.button("I do not wish to take part"):
        st.session_state.stage = "declined"
        st.rerun()


def stage_declined():
    st.subheader("Thank you")
    st.write("You have chosen not to take part. Nothing has been recorded. "
             "You may close this window.")


def stage_choose():
    """Which plan is this questionnaire about?

    Asked explicitly rather than inferred from whichever domain happened to
    be open. A participant who looked at both plans would otherwise be
    recorded against the one they clicked last, which may not be the one they
    have opinions about.

    Only plans the participant has actually held a conversation about are
    offered, because the questionnaire asks about an explanation they
    received.
    """
    ss = st.session_state
    st.subheader("Which plan are you reporting on?")

    available = explored_domains()

    if not available:
        st.warning(
            "You have not explored a plan yet, so there is nothing for the "
            "questionnaire to be about. Please open the explorer, choose a "
            "plan and ask the system a few questions, then come back.",
            icon=":material/info:")
        if st.button("Open the explorer", type="primary"):
            ss.mode = "explore"
            st.rerun()
        return

    st.markdown("Your answers will be about the plan you choose here. Only "
                "plans you have explored are shown.")

    for key in dm.BY_KEY:
        if key not in available:
            continue
        domain = dm.get(key)
        dialogue = ss.dialogues[key]
        outcome = dialogue.outcome()
        with st.container(border=True):
            st.markdown(f"**{domain.name}**")
            st.caption(domain.blurb)
            st.caption(f"You raised {outcome['cqs_challenged']} of "
                       f"{outcome['cqs_available']} available questions, and "
                       f"the verdict was {dialogue.verdict}.")
            if st.button("Report on this plan", key=f"study_pick_{key}",
                         type="primary", width="stretch"):
                ss.study_domain_key = key
                goto("questionnaire")

    not_yet = [dm.get(k).short for k in dm.BY_KEY if k not in available]
    if not_yet:
        st.caption("Not shown because you have not explored it yet: "
                   + ", ".join(not_yet) + ". You can go to Explore and come "
                   "back if you would rather report on that one.")


def stage_questionnaire():
    if st.session_state.study_domain_key is None:
        goto("choose")
    key = st.session_state.study_domain_key
    dialogue = st.session_state.dialogues[key]
    domain = dm.get(key)
    verdict = dialogue.verdict
    outcome = dialogue.outcome()
    sections = cfg.sections_for(verdict)

    st.subheader("User study questionnaire")
    st.markdown(cfg.QUESTIONNAIRE_PREAMBLE)

    # The questionnaire asks about an explanation. If the participant has not
    # held a dialogue, there is nothing to report on and every answer would be
    # guesswork, so they are sent to Explore rather than allowed to proceed.
    if outcome["explainee_moves"] == 0:
        st.warning(
            "You have not yet explored a plan, so there is nothing for these "
            "questions to be about. Please open the explorer, ask the system "
            "a few questions about a plan, then come back here.",
            icon=":material/info:")
        if st.button("Open the explorer", type="primary"):
            st.session_state.mode = "explore"
            st.rerun()
        return

    with st.container(border=True):
        left, right = st.columns(2)
        with left:
            st.markdown("**Plan you explored**")
            st.markdown(domain.name)
        with right:
            st.markdown("**Verdict you were given**")
            st.markdown(f"`{verdict}`")
        st.caption(f"You raised {outcome['cqs_challenged']} of "
                   f"{outcome['cqs_available']} available questions. Recorded "
                   f"automatically from your session.")

    answers = {}
    form = st.form("questionnaire_form", border=False)
    with form:
        for label, section_items in sections:
            with st.container(border=True):
                st.markdown(f"**{label}**")
                if label.startswith("F "):
                    st.caption(cfg.SECTION_F_NOTE)
                for item_id, text in section_items:
                    answers[item_id] = st.radio(
                        f"**{item_id}.**  {text}", cfg.LIKERT_SCALE,
                        key=f"q_{item_id}", index=None, horizontal=True)

        va_id, va_text = cfg.VERDICT_ITEM
        vr_id, vr_text = cfg.VERDICT_REASON
        with st.container(border=True):
            st.markdown("**Do you accept the verdict?**")
            st.caption(f"The system's verdict on this plan was "
                       f"**{verdict}**.")
            verdict_agreement = st.radio(
                f"**{va_id}.**  {va_text}", cfg.VERDICT_OPTIONS,
                key=f"q_{va_id}", index=None)
            verdict_reason = st.text_area(f"**{vr_id}.**  {vr_text}",
                                          key=f"open_{vr_id}", height=80)

        count_id, count_text = cfg.COUNT_ITEM
        available = outcome["cqs_available"]
        with st.container(border=True):
            st.markdown("**G — Dialogue engagement, your own count**")
            reported = st.selectbox(
                f"**{count_id}.**  {count_text}",
                list(range(0, available + 6)), index=None,
                key=f"q_{count_id}", placeholder="Choose a number",
                help=f"This plan offered {available} question buttons in total.")

        open_answers = {}
        with st.container(border=True):
            st.markdown("**H — Open feedback**")
            st.caption("All three are required. Write \"none\" if you have "
                       "nothing to add.")
            for item_id, text in cfg.OPEN_QUESTIONS:
                open_answers[item_id] = st.text_area(
                    f"**{item_id}.**  {text}", key=f"open_{item_id}",
                    height=80)

        submitted = st.form_submit_button("Submit my responses", type="primary")

    st.caption(f"*{cfg.QUESTIONNAIRE_CLOSING}*")

    if submitted:
        missing = [k for k, v in answers.items() if v is None]
        if reported is None:
            missing.append(count_id)
        missing += [k for k, v in open_answers.items() if not v.strip()]
        if verdict_agreement is None:
            missing.append(va_id)
        if not verdict_reason.strip():
            missing.append(vr_id)
        if missing:
            st.error(f"Please answer every item before submitting. "
                     f"Outstanding: {', '.join(missing)}.")
        else:
            submit(answers, int(reported), open_answers,
                   verdict_agreement, verdict_reason)


def submit(likert, reported_count, open_answers,
           verdict_agreement="", verdict_reason=""):
    key = st.session_state.study_domain_key
    dialogue = st.session_state.dialogues[key]
    began = st.session_state.started_at.get(key)
    elapsed = (time.time() - began) if began else None
    record = build_record(
        participant_code=st.session_state.participant_code,
        domain=key,
        workflow=dm.family_of(key),
        condition=dm.get(key).condition,
        condition_choice=(
            "assigned" if dm.get(key).condition == assigned_condition()
            else "self_selected"),
        verdict=dialogue.verdict,
        outcome=dialogue.outcome(),
        transcript=dialogue.transcript(),
        likert=likert,
        reported_count=reported_count,
        open_responses=open_answers,
        failure_point=failure_point_for(key),
        failure_info=failure_detail_for(key),
        verdict_agreement=verdict_agreement or "",
        verdict_reason=verdict_reason or "",
        seconds_on_dialogue=elapsed,
        consent_version=CONSENT_VERSION,
    )
    store = get_store(getattr(st, "secrets", None))
    saved, note = store.save(record)
    if not saved:
        st.error(f"Your responses could not be saved ({note}). Please tell the "
                 f"researcher and do not close this window yet.")
        return
    st.session_state.submitted = True
    st.session_state.save_note = note
    goto("debrief")


def stage_debrief():
    st.subheader("Thank you for taking part")
    st.markdown(cfg.DEBRIEF)
    st.info(f"Recorded under participant code "
            f"**{st.session_state.participant_code}**. You may close this window.",
            icon=":material/check_circle:")


# ---------------------------------------------------------------------------
# Researcher panel
# ---------------------------------------------------------------------------

def researcher_panel():
    with st.sidebar:
        st.markdown("**Researcher**")
        try:
            expected = st.secrets["admin_password"]
        except Exception:
            st.caption("Set admin_password in secrets to enable this panel.")
            return
        if st.text_input("Password", type="password", key="adm") != expected:
            return

        st.divider()
        st.markdown("**Sessions in this browser**")
        for key in dm.BY_KEY:
            if key in st.session_state.dialogues:
                moves = st.session_state.dialogues[key].outcome()["explainee_moves"]
                st.write(f"{dm.get(key).short}: {moves} move(s)")
        st.write("Reporting on:", st.session_state.study_domain_key or "not chosen yet")

        store = get_store(getattr(st, "secrets", None))
        st.write("Storage:", store.name)
        if not store.durable:
            st.error("Active store is NOT durable. Responses will be lost on "
                     "restart. Set sheet_id and gcp_service_account in "
                     "secrets.", icon=":material/warning:")
        else:
            ok, detail = store.check()
            if ok:
                st.success(f"Sheet {detail}", icon=":material/check:")
            else:
                st.error(f"Sheet unreachable: {detail}",
                         icon=":material/warning:")

        faithful, problems = check_faithfulness(active_dialogue())
        st.write("Faithful to grounded extension:", faithful)
        if problems:
            st.write(problems)
        st.write("Likert items (all):", len(cfg.all_items()))
        st.write("Items for an ACCEPTED plan:", len(cfg.all_items("ACCEPTED")))

        rows = store.load_all()
        st.write("Responses stored:", len(rows))
        if rows:
            st.download_button("Download responses (CSV)",
                               data=to_csv(rows),
                               file_name="haasplan_responses.csv",
                               mime="text/csv")
            st.download_button("Download responses (JSON)",
                               data=json.dumps(rows, indent=2),
                               file_name="haasplan_responses.json",
                               mime="application/json")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    st.set_page_config(page_title="HaasPlan XAI", layout="centered",
                       page_icon=str(LOGO) if LOGO.exists() else None)
    try:
        init_state()
    except FileNotFoundError as missing:
        header()
        st.error(f"Missing artefact `{missing}` in data/. Run the notebook's "
                 f"Module 9 and 9b and commit the JSON files.",
                 icon=":material/error:")
        st.stop()

    header()
    mode_switch()
    mode = st.session_state.mode

    if mode is None:
        stage_landing()
    elif mode == "explore":
        stage_explore()
    else:
        if st.session_state.stage == "declined":
            stage_declined()
        else:
            study_progress()
            {"information": stage_information, "consent": stage_consent,
             "choose": stage_choose,
             "questionnaire": stage_questionnaire,
             "debrief": stage_debrief}[st.session_state.stage]()

    researcher_panel()


if __name__ == "__main__":
    main()
