# Tools - Platoon A

Run bridge commands from inside the OpenClaw container.
Destination source for OpenClaw decisions is `/data/openclaw/.openclaw/workspace/data/vehicle_destinations.json`; use bridge `snapshot` only to confirm live transfer state after reload.

```bash
python3 /project/scripts/platoon_bridge_ctl.py snapshot
python3 /project/scripts/platoon_bridge_ctl.py readiness
python3 /project/scripts/platoon_bridge_ctl.py candidates platoon_a
python3 /project/scripts/platoon_bridge_ctl.py transfer <request_id>
python3 /project/scripts/platoon_bridge_ctl.py request <vehicle_id> platoon_a platoon_b --reason destination_match --sender-agent platoon_a --receiver-agent platoon_b
python3 /project/scripts/platoon_dialogue_guard.py inbound --agent platoon_a --message '<current Discord message>'
python3 /project/scripts/platoon_dialogue_guard.py validate-json --agent platoon_a --context-file /data/openclaw/.openclaw/workspace/data/platoon_decision_context.json --destinations-file /data/openclaw/.openclaw/workspace/data/vehicle_destinations.json --vehicle-id <vehicle_id>
python3 /project/scripts/platoon_dialogue_guard.py validate-request --a-list '<current Platoon A list>' --b-list '<current Platoon B list>' --request '<current request message>'
cat /data/openclaw/.openclaw/workspace/data/vehicle_destinations.json
cat /data/openclaw/.openclaw/workspace/data/platoon_decision_context.json
```

## Safe Use

- Use `snapshot` before creating a request and after the peer reports commit.
- Before any Discord response, run `platoon_dialogue_guard.py inbound`; if `allow_response` is false, stay silent.
- Before posting destinations, read `/data/openclaw/.openclaw/workspace/data/vehicle_destinations.json`; do not rely on examples, bridge defaults, or remembered prompt text.
- Before creating or confirming a request, run `platoon_dialogue_guard.py validate-json`; if `valid` is false, stop and report one mismatch message only.
- Use `readiness` after commit/merging when explaining why CARLA has or has not started the physical merge. If readiness is `idle`, poll once more before responding.
- Use `candidates platoon_a` to confirm the bridge agrees with the destination match.
- Use `transfer <request_id>` when a request id appears in Discord.
- Do not call `accept`, `reject`, or `commit`; those are Platoon B actions.
- Do not call `/start_merge`; the bridge server triggers CARLA after commit.
