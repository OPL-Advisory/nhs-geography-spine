# Controlled-preview user trial — 20 minutes

**Not yet run.** The maintainer recruits 5–8 consenting participants only after
approving the audience, rights, host and access control. Include at least three
viewpoints: constituency/MP research, NHS organisation analysis, and a public
phone user unfamiliar with the project. Include a keyboard/screen-reader user
where possible. No invitations or contact-list exports are part of this PR.

Use the exact approved archive SHA, candidate snapshot, source periods and
immutable preview URL. For a local synthetic session, disclose the fictional
data and avoid measuring knowledge of actual NHS organisations. Do not expose
real data through an unapproved hosted session or screen-sharing service.

| Time | Facilitator instruction | Evidence to record |
| --- | --- | --- |
| 0–2 min | Explain voluntary participation, dated source data and optional notes. Ask device/input preference. Do not teach the site/patient distinction. | Anonymous participant ID, broad viewpoint, device/input method; no name, email, health details or unnecessary demographics. |
| 2–6 min | J1: “Find [approved constituency]. Who is its MP in this tool, and when is that information from? What do the two NHS sections tell you?” | Completion, seconds, assistance, answer and any factual misunderstanding. |
| 6–10 min | J2: “Find [approved cross-boundary practice]. Does its address identify all its patients' constituencies? Follow a different constituency and return.” | Navigation success; explanation of location versus registration allocation; unmapped bucket understood. |
| 10–14 min | J3: “Find [approved provider/ICB]. What relationship is actually evidenced? Can you identify its service catchment?” | Recognises RE6/address evidence and unknown catchment; dates/absence/inactive status not misread. |
| 14–17 min | J4: “On this phone, find a familiar practice/place, share a dated answer with a colleague by copying its link, and return to search.” | Unassisted completion ≤2 minutes as a proposed target; copied link opens correct code and source dates. |
| 17–20 min | Ask what was useful/confusing and one change. Repeat key interpretation question without coaching. Offer withdrawal of notes. | Final explanation, confidence in it, suggested priority, accessibility blockers. |

## Proposed acceptance targets

- ≥80% unassisted task completion, reported as actual numerator/denominator
  **for each journey**. With 5 people this means at least 4; with 8, at least 7.
- ≥80% correctly explain site location versus GP allocation unaided.
- Zero critical factual misinterpretations (e.g. trust address treated as
  catchment, registrations treated as exact residents, no evidence treated as
  no services) and zero critical accessibility blockers.
- No claim of representative population coverage or formal WCAG certification
  from a 5–8 person trial. Record assistance and failed tasks; do not discard them.

## Feedback intake and decisions

Maintainer keeps a restricted CSV or note file outside the public repository:
`participant_id, viewpoint, device, input_method, candidate_sha, task_id,
completed, assisted, seconds, interpretation_correct, issue, severity,
withdrawal_requested`. Use whole-second timings; record verbatim notes only
with consent. Delete recruitment contacts and anonymised notes after the
maintainer's agreed retention period (proposed 30 days after the launch decision).
No analytics, cookies, patient data or session recordings are needed.

Triage: **critical** = false substantive conclusion or task blocked; **major** =
assistance needed / materially confusing; **minor** = wording or friction.
Every issue gets an owner, candidate, evidence and fix/retest decision.
Stop the release decision for a critical issue, then retest the affected task.
Publish only aggregated outcomes and sanitised findings in a follow-up PR.
Do not treat running this protocol as permission to publish the site.
