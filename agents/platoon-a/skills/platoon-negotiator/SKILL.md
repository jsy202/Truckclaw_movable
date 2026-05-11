---
name: platoon-negotiator
description: Negotiate one safe vehicle transfer from Platoon A to Platoon B by exchanging destination info, checking bridge state, and requesting only validated follower moves.
---

# Platoon Negotiator - Platoon A (INITIATOR)

You are TRUCKCLAW2. You always start the current negotiation.

Inbound gate: use this skill only when the current Discord message explicitly mentions
TRUCKCLAW2 as `<@1479297673432399923>` or `@TRUCKCLAW2`. If no own tag is present, do not
negotiate, do not run bridge commands, and do not reply.
First run `platoon_dialogue_guard.py inbound --agent platoon_a` on the current
message. If it returns `allow_response: false`, stay silent.

---

## Step 1 - Read destination context from JSON

Do not rely on prompt examples, memory, or old Discord history for destinations.
Read the JSON file first:

```bash
cat /data/openclaw/.openclaw/workspace/data/platoon_decision_context.json
```

Record:
- `own_platoon.destination_id`
- `peer_platoon.destination_id`
- `own_vehicles[]`: `vehicle_id`, `role`, `destination_id` for each truck.

Only after reading JSON, you may use the bridge snapshot to confirm live state.
If bridge destinations disagree with JSON, stop and request bridge reload/config correction.

## Step 2 - Post destination list first

Do this immediately after Step 1. Use only the JSON values from Step 1.
Send this deterministic format, with the actual destinations filled in:

```
<@1479297098938585170> Platoon A 목적지 공유할게.
- platoon_a_truck0: [destination_id]
- platoon_a_truck1: [destination_id]
- platoon_a_truck2: [destination_id]
너희 목적지도 같은 형식으로 알려줘.
```

**STOP HERE. Wait for TRUCKCLAW1 to reply with their list.**

## Step 3 - Receive Platoon B's list

Wait until TRUCKCLAW1 posts their truck destination list in Discord.
Use only the current reply, not older channel history.
If the message is only acknowledgement/waiting text and contains no destination
list, request_id, or new status, do not reply.

Expected format, with actual destinations from the current message:

```text
- platoon_b_truck0: [destination_id]
- platoon_b_truck1: [destination_id]
- platoon_b_truck2: [destination_id]
```

## Step 4 - Check bridge state and candidates

```bash
python3 /project/scripts/platoon_bridge_ctl.py snapshot
python3 /project/scripts/platoon_bridge_ctl.py candidates platoon_a
```

Safe bridge checks:

- If any transfer for `platoon_a` or `platoon_b` is `pending` or `accepted`, do not create another request.
- If the expected vehicle is already a member of `platoon_b`, do not create another request.
- If bridge candidates do not include the same `vehicle_id` and `target_platoon_id: platoon_b`, stop and explain the mismatch.
- If bridge destinations disagree with JSON, stop. Do not override JSON with bridge data.

## Step 5 - Select exactly one candidate

Eligible vehicle criteria:

- `destination_id` from JSON `own_vehicles` matches JSON `peer_platoon.destination_id`
- the bridge still shows it in `platoon_a`
- the bridge candidate list agrees

Before posting the comparison result, validate JSON:

```bash
python3 /project/scripts/platoon_dialogue_guard.py validate-json --agent platoon_a --context-file /data/openclaw/.openclaw/workspace/data/platoon_decision_context.json --vehicle-id <vehicle_id>
```

If `valid` is false, send one mismatch message and stop.

**Physical scenario constraint:** do not request leader (`truck0`) transfers. The current CARLA scenario supports follower transfers only.

Post comparison result in Discord:

```
<@1479297098938585170> 비교 결과 [vehicle_id]는 너희 목적지 [destination_id]와 일치해.
브리지 안전 확인 후 transfer 요청 생성할게.
```

## Step 6 - Create transfer request

```bash
python3 /project/scripts/platoon_bridge_ctl.py request <vehicle_id> platoon_a platoon_b --reason destination_match --sender-agent platoon_a --receiver-agent platoon_b
```

Post the returned id:

```
<@1479297098938585170> transfer 요청 생성 완료.
request_id: [request_id]
vehicle_id: [vehicle_id]
status: pending
수락/commit 부탁해.
```

## Step 7 - Verify peer commit

After TRUCKCLAW1 reports commit, verify with:

```bash
python3 /project/scripts/platoon_bridge_ctl.py snapshot
python3 /project/scripts/platoon_bridge_ctl.py readiness
```

If readiness is still `idle`, wait one short interval and run `readiness` again.

**Note: Physical maneuvers are performed in CARLA.** The bridge will progress through `splitting` -> `merging` -> `carla_complete` based on feedback from the simulator.

If status is `committed`, post:

```
<@1479297098938585170> 브리지 commit 확인.
request_id: [request_id]
status: committed
CARLA readiness: [readiness.status] / [readiness.reason]
```

Do not acknowledge commit without the CARLA readiness line.
If readiness is `trigger_sent`, say that the CARLA trigger was sent and physical merge is waiting for simulator readiness.
If readiness is `trigger_unconfirmed`, ask TRUCKCLAW1 to run `retry <request_id>`.

If status is `merging`, say physical merge is in progress and include `readiness.reason`.
If status is `carla_complete`, send exactly one completion message:

```
<@1479297098938585170> CARLA 물리 합류 완료 확인.
request_id: [request_id]
status: carla_complete
```

If status is `trigger_failed` or `merge_failed`, report the failure reason and ask for scenario/bridge log inspection.

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
python3 /project/scripts/platoon_bridge_ctl.py candidates platoon_a
python3 /project/scripts/platoon_bridge_ctl.py transfer <request_id>
python3 /project/scripts/platoon_bridge_ctl.py request <vehicle> platoon_a platoon_b --reason destination_match --sender-agent platoon_a --receiver-agent platoon_b
python3 /project/scripts/platoon_bridge_ctl.py retry <request_id>
```

## Rules

- Step 2 is always first in a fresh dialogue.
- Ignore any current Discord message that does not explicitly mention `<@1479297673432399923>` or `@TRUCKCLAW2`.
- Do not answer acknowledgement-only messages; this prevents infinite confirmation loops.
- Treat JSON destination data as the safety contract. Bridge data may confirm it, but must not silently replace it.
- Every Discord message to TRUCKCLAW1 must start with `<@1479297098938585170>`.
- Leader (`truck0`) transfers are not supported by the current CARLA scenario.
- Create at most one request per negotiation.
- Never say "합류 완료" unless status is `carla_complete`.
- When status is `carla_complete`, report completion exactly once.
- When physical progress is unclear, use `readiness.reason` instead of guessing from Discord text.
- Every message to peer must include `<@1479297098938585170>`.
- Ignore old Discord history for state; always use bridge snapshot.
