# HaasPlan XAI

**Argumentation-Based Explanations for Collaborative Planning**

Streamlit app for the argumentation-based plan explanation chatbot
(ECR_2024_17, University of Huddersfield).

## Two modes

**Explore** — a domain selector, the plan, the framework, and a free dialogue.
This is the demonstration mode. Nothing is recorded.

**User study** — the six-stage participant flow: information sheet, consent,
briefing, dialogue, questionnaire, debrief.

## Domains

| Key | Domain | Actions | CQ instances |
|-----|--------|---------|--------------|
| `bus_train` | Transport, bus and train journey | 6 | 40 |
| `cash_transfer` | Social protection, cash transfer with grievance resolution | 11 | 73 |

Both frameworks come from **one** extraction implementation, Module 3b of the
notebook, which reads conditions and effects off the Unified Planning model
rather than matching on action names. That is what the second domain is
evidence for.

### Adding a third domain

1. Add an entry to `REGISTRY` in `domains.py`.
2. Drop `<key>_plan.json`, `<key>_S8_PSA.json`, `<key>_CQ_results.json` and
   `<key>_AF_labels.json` into `data/`. Optionally `<key>_display_names.json`.

No other file changes. The selector picks up any domain whose artefacts exist.

## Running it

```bash
pip install -r requirements.txt
streamlit run streamlit_app.py
```

The `data/` folder already contains both domains, so it runs immediately.

## Deploying

Push to GitHub, create a Streamlit Cloud app pointing at `streamlit_app.py`,
then paste your secrets into the app's Secrets box.

**Storage.** Streamlit Community Cloud has an ephemeral filesystem: it is wiped
on redeploy and after inactivity. The default local store is for development.
Set `sheet_id` and `[gcp_service_account]` in secrets to switch to Google
Sheets. The researcher panel shows which store is active and warns in red when
it is not durable.

**Researcher panel.** Set `admin_password` in secrets. It exposes the domain
assignment for study participants, the storage status, a faithfulness check,
and a response download.

## Before running with participants

`study_config.py` holds every participant-facing string in one file. Text
marked `[REPLACE]` is placeholder scaffolding and needs your approved wording.
`RETENTION_YEARS = 10` is the single source of truth for the retention period
and is interpolated into the information sheet, a consent item and the debrief.

## Layout

| File | Purpose |
|------|---------|
| `streamlit_app.py` | Landing page, explore mode, study flow, researcher panel |
| `domains.py` | Domain registry, the only file a new domain touches |
| `explanation_dialogue.py` | Protocol engine, identical to notebook Module 10b |
| `study_config.py` | All participant-facing text and the questionnaire |
| `storage.py` | Pluggable response storage |
| `data/` | Framework artefacts, both domains |
| `assets/` | University logo, converted from CMYK to RGB |
