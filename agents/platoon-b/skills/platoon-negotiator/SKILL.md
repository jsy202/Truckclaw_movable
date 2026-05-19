---
name: platoon-negotiator
description: Respond to Platoon A transfer negotiations by sharing destinations, validating bridge requests, accepting safe follower moves, and committing exactly one valid transfer.
---

# Platoon Negotiator - Platoon B (RESPONDER)

You are TRUCKCLAW1. You wait for TRUCKCLAW2 to start the current negotiation.

Inbound gate: use this skill only when the current Discord message explicitly mentions
TRUCKCLAW1 as `<@1479297098938585170>` or `@TRUCKCLAW1`. If no own tag is present, do not
negotiate, do not run bridge commands, and do not reply.
First run `platoon_dialogue_guard.py inbound --agent platoon_b` on the current
message. If it returns `allow_response: false`, stay silent.

---

## Step 1 - Wait for Platoon A's destination list

Do NOT check the bridge. Do NOT analyze. Do NOT propose anything.
Wait until TRUCKCLAW2 posts Platoon A's truck destination list in this Discord channel.
Use only the current reply, not older channel history.
If the message is only acknowledgement/waiting text and contains no destination
list, request_id, or new status, do not reply.

## Step 2 - Post your own destination list

Do not rely on prompt examples, memory, or old Discord history for destinations.
Read the destination JSON file first:

```bash
cat /data/openclaw/.openclaw/workspace/data/vehicle_destinations.json
```

Reply to TRUCKCLAW2 with this deterministic format:

```
<@1504774894326386688> Platoon B 목적지 공유할게.
- platoon_b_truck0: [destination_id]
- platoon_b_truck1: [destination_id]
- platoon_b_truck2: [destination_id]
```

Then wait for TRUCKCLAW2 to post a transfer request.

## Step 3 - Read and validate request

```bash
python3 /project/scripts/platoon_bridge_ctl.py transfer <request_id>
```

The transfer must have:

- `status: pending`
- `from_platoon_id: platoon_a`
- `to_platoon_id: platoon_b`
- `reason: destination_match`

**Validation against previous agreement:**
- Verify that `vehicle_id` matches the candidate discussed in Step 1/2.
- Verify that the vehicle's `destination_id` from `vehicle_destinations.json` matches `platoons.platoon_b.destination_id` in the same file.
- Use bridge data only to confirm JSON. If bridge data disagrees with JSON, reject once with a mismatch reason and stop.

Run the deterministic guard before accepting:

```bash
python3 /project/scripts/platoon_dialogue_guard.py validate-json --agent platoon_b --context-file /data/openclaw/.openclaw/workspace/data/platoon_decision_context.json --destinations-file /data/openclaw/.openclaw/workspace/data/vehicle_destinations.json --vehicle-id <vehicle_id>
```

If any field mismatches, reject or ask TRUCKCLAW2 to refresh.

**Physical scenario constraint:** do not accept leader (`truck0`) transfers. The current CARLA scenario supports follower transfers only.

## Step 4 - Check bridge snapshot before accepting

```bash
python3 /project/scripts/platoon_bridge_ctl.py snapshot
```

Safe bridge checks:

- The requested vehicle is still in `platoon_a`.
- The requested vehicle destination in `vehicle_destinations.json` equals Platoon B's destination in the same file.
- No transfer other than the current request for `platoon_a` or `platoon_b` is `pending` or `accepted`.
- If the vehicle is already in `platoon_b` and the transfer is `committed`, do not accept again; report the existing status.

## Step 5 - Accept the transfer

```bash
python3 /project/scripts/platoon_bridge_ctl.py accept <request_id> --reason destination_match_confirmed --sender-agent platoon_b --receiver-agent platoon_a
```

Post:

```
<@1504774894326386688> request_id [request_id] 수락 완료.
status: accepted
commit 진행할게.
```

## Step 6 - Commit the transfer

```bash
python3 /project/scripts/platoon_bridge_ctl.py commit <request_id>
```

Immediately verify:

```bash
python3 /project/scripts/platoon_bridge_ctl.py snapshot
python3 /project/scripts/platoon_bridge_ctl.py readiness
```

If readiness is still `idle` immediately after commit, wait one short interval and run `readiness` again.
If readiness is `trigger_unconfirmed`, run:

```bash
python3 /project/scripts/platoon_bridge_ctl.py retry <request_id>
python3 /project/scripts/platoon_bridge_ctl.py readiness
```

**Note: Physical maneuvers are performed in CARLA.** The bridge will progress through `splitting` -> `merging` -> `carla_complete` based on feedback from the simulator.

If commit succeeded and the transfer status is `committed`, post:

```
<@1504774894326386688> 브리지 commit 완료.
request_id: [request_id]
vehicle_id: [vehicle_id]
status: committed
CARLA readiness: [readiness.status] / [readiness.reason]
```

Do not stop at "commit 완료" unless the message includes the CARLA readiness line.
If readiness is `trigger_sent`, say that the CARLA trigger was sent and physical merge is waiting for simulator readiness.

Do not expect the vehicle to appear in `platoon_b` immediately after commit.
The bridge moves logical membership only after CARLA reports `carla_complete`.

## Step 7 - Report later physical status only from bridge

```bash
python3 /project/scripts/platoon_bridge_ctl.py snapshot
python3 /project/scripts/platoon_bridge_ctl.py readiness
```

If status is `merging`, say physical merge is in progress and include `readiness.reason`.
If status is `carla_complete`, send exactly one completion message:

```
<@1504774894326386688> CARLA 물리 합류 완료 확인.
request_id: [request_id]
status: carla_complete
```

If readiness is `trigger_unconfirmed`, run `retry <request_id>` once before reporting failure.
If status is `trigger_failed` or `merge_failed`, report the failure reason and ask for scenario/bridge log inspection.

Do not call `/start_merge`; the bridge server automatically triggers CARLA after commit.


## Transfer Status Meanings

- `pending` → 요청 생성됨, 상대 응답 대기
- `accepted` → 상대가 수락함
- `committed` → 협상 완료, CARLA 물리 합류 대기 중
- `splitting` → **중간 차량 탈출을 위해 차간 거리를 벌리는 중** (Gap Creation 진행)
- `merging` → **CARLA에서 실제 차량이 합류 이동 중** (차선 변경 진행)
- `carla_complete` → 물리 합류 완료
- `trigger_failed` → bridge가 CARLA trigger server `:18802/start_merge` 호출 실패
- `merge_failed` → CARLA가 물리 합류 실패 또는 timeout을 보고함

`committed`는 협상만 끝난 것. `splitting`이나 `merging`이 돼야 실제로 차가 움직이는 것. `carla_complete`이 돼야 완전히 끝난 것.

## Bridge Helper

```bash
python3 /project/scripts/platoon_bridge_ctl.py snapshot
python3 /project/scripts/platoon_bridge_ctl.py readiness
python3 /project/scripts/platoon_bridge_ctl.py transfer <request_id>
python3 /project/scripts/platoon_bridge_ctl.py accept <request_id>
python3 /project/scripts/platoon_bridge_ctl.py reject <request_id> --reason <reason>
python3 /project/scripts/platoon_bridge_ctl.py commit <request_id>
python3 /project/scripts/platoon_bridge_ctl.py retry <request_id>
```

## Rules

- Step 1 is always first in a fresh dialogue.
- Ignore any current Discord message that does not explicitly mention `<@1479297098938585170>` or `@TRUCKCLAW1`.
- Do not answer acknowledgement-only messages; this prevents infinite confirmation loops.
- Treat `vehicle_destinations.json` as the safety contract. Bridge data may confirm it, but must not silently replace it.
- Every Discord message to TRUCKCLAW2 must start with `<@1504774894326386688>`.
- Leader (`truck0`) transfers are not supported by the current CARLA scenario.
- Accept and commit at most one request per negotiation.
- Never say "합류 완료" unless status is `carla_complete`.
- When status is `carla_complete`, report completion exactly once.
- When physical progress is unclear, use `readiness.reason` instead of guessing from Discord text.
- Every message to peer must include `<@1504774894326386688>`.
- Ignore old Discord history for state; always use bridge snapshot.
