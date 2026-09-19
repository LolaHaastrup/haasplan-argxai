"""
storage.py
==========

Where participant responses go, and in what shape.

THE SHEET IS THE ANALYSIS FILE
------------------------------
One row per participant, one column per questionnaire item, Likert responses
coded 1 to 5. Download the sheet as CSV and it loads straight into R, SPSS,
Stata or pandas with no unpacking step. That is why the questionnaire is not
stored as a JSON blob: a blob is fine for archiving and useless for analysis.

Section F applies only to participants who saw a rejected plan. Their cells are
left EMPTY rather than zero, so a mean over F1 ignores them correctly instead
of dragging the average down.

EVERY SAVE IS WRITTEN TWICE
---------------------------
Once to the sheet, once to a local JSON Lines file. The local copy exists
because Streamlit Community Cloud has an ephemeral filesystem and the sheet is
the durable record, but if the Sheets call fails mid-study the response is
still on disk for the remainder of that container's life, which gives you a
chance to recover it. save() reports success only if at least one destination
took the row, and reports which.

API CALLS
---------
The gspread calls used here were verified against gspread 6.1.4:
service_account_from_dict, open_by_key, sheet1, acell, append_row,
get_all_values, get_all_records. The header row is written once, detected by
reading cell A1 rather than pulling the whole sheet, because pulling the whole
sheet on every save gets slow and burns quota as the study grows.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import study_config as cfg

LOCAL_BACKUP = "responses_backup.jsonl"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Column layout
# ---------------------------------------------------------------------------

#: Session and assignment metadata.
META_COLUMNS = [
    "timestamp",
    "participant_code",
    "domain",                 # artefact key for the exact plan seen
    "workflow",               # transport or social_protection, participant's choice
    "condition",              # valid or faulty
    "condition_choice",       # assigned, or self_selected if they switched
    "verdict_shown",
    "consent_version",
    "retention_years",
]

#: Behavioural measures, taken from the dialogue transcript rather than asked.
BEHAVIOUR_COLUMNS = [
    "cqs_available",
    "cqs_challenged_observed",
    "premises_grounded",
    "nodes_understood",
    "explainee_moves",
    "move_bound",
    "closed_by_user",
    "seconds_on_dialogue",
    "conceded_cqs",
    "failure_point",          # where the plan fails, e.g. CQ5@10
    "failure_cq",             # the critical question that succeeds
    "failure_step",           # which plan step
    "failure_action",         # that step's readable name
    "failure_scheme",         # the scheme whose premise it attacks
    "failure_premise",        # the premise that does not survive
    "failure_detail",         # the framework's own wording
    "failure_reason",         # one sentence, quotable
    "found_failure",          # did the participant challenge that question
    "verdict_agreement",      # accepts, rejects or unsure
    "verdict_reason",         # their own words
    "drill_down_moves",       # WHY moves, how deep they went
    "distinct_cq_types",      # how many of the nine question types they used
    "closed_deliberately",    # ended by choice rather than by exhaustion
    "cq_sequence",            # the order questions were asked, compact
]


def likert_columns() -> List[str]:
    """Every Likert item id, in questionnaire order, section F included."""
    return [item_id for _section, item_id, _text in cfg.all_items()]


def open_columns() -> List[str]:
    return [item_id for item_id, _text in cfg.OPEN_QUESTIONS]


def columns() -> List[str]:
    """The full column order. Keep stable: rows are appended positionally."""
    return (META_COLUMNS
            + BEHAVIOUR_COLUMNS
            + likert_columns()
            + [cfg.COUNT_ITEM[0]]          # DL, the self-reported count
            + open_columns()
            + ["transcript_compact"])


def summarise_transcript(transcript: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Derive the behavioural measures at save time.

    WHY THIS EXISTS. The full transcript was previously stored as JSON in one
    cell, and a spreadsheet cell holds about 32,000 characters. A participant
    who asked forty questions produced more than that, so their row was
    silently truncated and their transcript became unparseable. The most
    engaged participants were the ones whose data was lost.

    So the measures are computed here, into their own columns, and what is
    stored alongside them is a compact trace rather than full text. Nothing in
    the analysis then depends on parsing a field that might have been cut.
    """
    user_moves = [r for r in transcript if r.get("speaker") == "U"]
    challenges = [r for r in user_moves if r.get("locution") == "challenge"]
    whys = [r for r in user_moves if r.get("locution") == "why"]
    types = {str(r.get("target", "")).split("@")[0] for r in challenges}
    sequence = ";".join(str(r.get("target", "")) for r in challenges)

    return {
        "drill_down_moves": len(whys),
        "distinct_cq_types": len(types),
        "closed_deliberately": any(r.get("locution") == "close"
                                   for r in user_moves),
        # Truncated defensively. The per-question counts live in the columns
        # above, so this is a convenience trace, not the source of any number.
        "cq_sequence": sequence[:1500],
    }


def compact_trace(transcript: List[Dict[str, Any]]) -> str:
    """Speaker, locution and target only. No text, so it cannot overflow."""
    parts = ["%s:%s:%s" % (r.get("speaker", ""), r.get("locution", ""),
                           r.get("target", "") or "")
             for r in transcript]
    return "|".join(parts)[:20000]


def likert_code(label: Optional[str]) -> str:
    """Code a Likert label as 1 to 5. Unanswered or not applicable is empty.

    Empty rather than zero matters: a blank cell is excluded from a mean,
    a zero is not.
    """
    if label is None or label == "":
        return ""
    try:
        return str(cfg.LIKERT_SCALE.index(label) + 1)
    except ValueError:
        return ""


# ---------------------------------------------------------------------------
# Record assembly
# ---------------------------------------------------------------------------

def build_record(
    participant_code: str,
    domain: str,
    workflow: str,
    condition: str,
    condition_choice: str,
    verdict: str,
    outcome: Dict[str, Any],
    transcript: List[Dict[str, Any]],
    likert: Dict[str, Optional[str]],
    reported_count: Optional[int],
    open_responses: Dict[str, str],
    failure_point: str = "",
    failure_info: Optional[Dict[str, str]] = None,
    verdict_agreement: str = "",
    verdict_reason: str = "",
    seconds_on_dialogue: Optional[float] = None,
    consent_version: str = "",
) -> Dict[str, Any]:
    """Assemble one participant's row.

    No field identifies the participant. The participant code is generated in
    the browser session and is not linked to any identity.
    """
    record: Dict[str, Any] = {
        "timestamp": _now(),
        "participant_code": participant_code,
        "domain": domain,
        "workflow": workflow,
        "condition": condition,
        "condition_choice": condition_choice,
        "verdict_shown": verdict,
        "consent_version": consent_version,
        "retention_years": cfg.RETENTION_YEARS,
        "cqs_available": outcome.get("cqs_available", ""),
        "cqs_challenged_observed": outcome.get("cqs_challenged", ""),
        "premises_grounded": outcome.get("premises_grounded", ""),
        "nodes_understood": outcome.get("nodes_understood", ""),
        "explainee_moves": outcome.get("explainee_moves", ""),
        "move_bound": outcome.get("move_bound", ""),
        "closed_by_user": outcome.get("closed_by_user", ""),
        "seconds_on_dialogue": (round(seconds_on_dialogue, 1)
                                if seconds_on_dialogue else ""),
        "conceded_cqs": "; ".join(outcome.get("conceded_cqs", []) or []),
        # Where the plan fails according to the framework, regardless of what
        # the participant asked. conceded_cqs only records failures they
        # happened to challenge, so on its own it cannot tell you whether a
        # rejected plan's fault was ever located.
        "failure_point": failure_point,
        "verdict_agreement": verdict_agreement,
        "verdict_reason": verdict_reason.strip(),
    }
    record.update(failure_info or {})
    record.update(summarise_transcript(transcript))
    # Did they actually put the failing question to the system? Blank for a
    # valid plan, where there is no failure to find.
    if failure_point:
        asked = set(record.get("cq_sequence", "").split(";"))
        record["found_failure"] = failure_point in asked
    else:
        record["found_failure"] = ""

    # Likert items, coded. Items not shown to this participant stay empty.
    for item_id in likert_columns():
        record[item_id] = likert_code(likert.get(item_id))

    record[cfg.COUNT_ITEM[0]] = ("" if reported_count is None
                                 else int(reported_count))

    for item_id in open_columns():
        record[item_id] = (open_responses.get(item_id) or "").strip()

    record["transcript_compact"] = compact_trace(transcript)
    return record


def record_to_row(record: Dict[str, Any]) -> List[str]:
    return [str(record.get(column, "")) for column in columns()]


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------

class ResponseStore:
    name = "abstract"
    durable = False

    def save(self, record: Dict[str, Any]) -> Tuple[bool, str]:
        raise NotImplementedError

    def load_all(self) -> List[Dict[str, Any]]:
        raise NotImplementedError


class LocalJSONLStore(ResponseStore):
    """Append to a local JSON Lines file.

    NOT DURABLE on Streamlit Community Cloud: the container filesystem is
    wiped on redeploy and after inactivity. Used as a development backend and,
    always, as a backup alongside the sheet.
    """

    name = "Local file only (NOT durable)"
    durable = False

    def __init__(self, path: str = LOCAL_BACKUP) -> None:
        self.path = path

    def save(self, record: Dict[str, Any]) -> Tuple[bool, str]:
        try:
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            return True, "local file"
        except OSError as exc:
            return False, f"local file failed: {exc}"

    def load_all(self) -> List[Dict[str, Any]]:
        if not os.path.exists(self.path):
            return []
        rows = []
        with open(self.path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return rows


class GoogleSheetsStore(ResponseStore):
    """Append one row per participant to a Google Sheet.

    Every save also writes to the local backup, so a transient Sheets failure
    does not lose the response outright.
    """

    name = "Google Sheet"
    durable = True

    def __init__(self, service_account_info: Dict[str, Any],
                 sheet_id: str) -> None:
        import gspread  # imported lazily so the app runs without it

        self._gspread = gspread
        self._client = gspread.service_account_from_dict(service_account_info)
        self._sheet_id = sheet_id
        self._backup = LocalJSONLStore()
        self._header_checked = False

    def _worksheet(self):
        return self._client.open_by_key(self._sheet_id).sheet1

    def _ensure_header(self, worksheet) -> None:
        """Write the header row once.

        Reads a single cell rather than the whole sheet, so this stays cheap
        as rows accumulate.
        """
        if self._header_checked:
            return
        first_cell = worksheet.acell("A1").value
        if not first_cell:
            worksheet.append_row(columns())
        self._header_checked = True

    def save(self, record: Dict[str, Any]) -> Tuple[bool, str]:
        backup_ok, backup_note = self._backup.save(record)
        try:
            worksheet = self._worksheet()
            self._ensure_header(worksheet)
            worksheet.append_row(record_to_row(record))
            return True, "Google Sheet" + ("" if backup_ok
                                           else " (local backup failed)")
        except Exception as exc:
            if backup_ok:
                return True, (f"LOCAL BACKUP ONLY. The sheet rejected the row: "
                              f"{exc}")
            return False, f"both destinations failed: {exc} / {backup_note}"

    def load_all(self) -> List[Dict[str, Any]]:
        try:
            return self._worksheet().get_all_records()
        except Exception:
            return self._backup.load_all()

    def check(self) -> Tuple[bool, str]:
        """Verify the sheet is reachable and writable, without adding a row.

        Called from the researcher panel so a misconfiguration is found before
        recruiting rather than after.
        """
        try:
            worksheet = self._worksheet()
            values = worksheet.get_all_values()
            header_present = bool(values and values[0])
            return True, (f"connected, {max(len(values) - 1, 0)} response "
                          f"row(s), header "
                          f"{'present' if header_present else 'not yet written'}")
        except Exception as exc:
            return False, str(exc)


def get_store(secrets: Optional[Any] = None) -> ResponseStore:
    """Choose a backend from what is configured.

    Google Sheets when both a service account and a sheet id are present,
    local file otherwise. The app shows which is active and warns when it is
    not durable, so a study cannot be run against the development backend by
    accident.
    """
    if secrets is None:
        return LocalJSONLStore()
    try:
        has_account = "gcp_service_account" in secrets
        has_sheet = "sheet_id" in secrets
    except Exception:
        return LocalJSONLStore()

    if has_account and has_sheet:
        try:
            return GoogleSheetsStore(dict(secrets["gcp_service_account"]),
                                     secrets["sheet_id"])
        except Exception:
            return LocalJSONLStore()
    return LocalJSONLStore()


def to_csv(rows: List[Dict[str, Any]]) -> str:
    """Render stored rows as CSV in the sheet's column order."""
    import csv
    import io

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns(), extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({c: row.get(c, "") for c in columns()})
    return buffer.getvalue()
