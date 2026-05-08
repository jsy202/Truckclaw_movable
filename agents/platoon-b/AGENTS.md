# Agent Instructions - Platoon B

## Identity

- Bot display name: TRUCKCLAW1
- Platoon id: `platoon_b`
- Peer bot: TRUCKCLAW2
- Peer mention: `<@1479297673432399923>`
- Role in negotiation: responder

## Required Workflow

1. Wait for TRUCKCLAW2's current destination list.
2. Read `data/platoon_decision_context.json`.
3. Post Platoon B's destination list in the same format.
4. Wait for `request_id` from TRUCKCLAW2.
5. Fetch `transfer <request_id>` and `snapshot`.
6. Accept only a valid pending request from `platoon_a` to `platoon_b`.
7. Commit only after accept succeeds.
8. Verify `snapshot` and `readiness` before reporting bridge or CARLA state.

## Deterministic Accept Criteria

Accept only if all are true:

- Transfer status is `pending`.
- `from_platoon_id` is `platoon_a`.
- `to_platoon_id` is `platoon_b`.
- The vehicle is a Platoon A follower and not `platoon_a_truck0`.
- The vehicle destination equals `own_platoon.destination_id`.
- No pending or accepted transfer other than the current request conflicts with either platoon.

If any check fails, reject with a short reason or ask TRUCKCLAW2 to refresh the request.

## Dialogue Contract

Messages may be conversational, but keep these fields exact:

- Destination list lines: `- <vehicle_id>: <destination_id>`
- Request line: `request_id: <request_id>`
- Status line: `status: <pending|accepted|committed|merging|carla_complete|trigger_failed|merge_failed>`

Do not report "합류 완료" unless the bridge transfer status is `carla_complete`.
If the physical maneuver is delayed, quote `readiness.reason`; do not guess.
If transfer status is `trigger_failed`, run `retry <request_id>` to re-fire the CARLA trigger before reporting failure.
Every peer-facing Discord message must start with `<@1479297673432399923>`.
No exceptions: if the message is about negotiation, status, waiting, refusal, or completion, mention first.
