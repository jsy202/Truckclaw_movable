# Tools - Platoon B

Run bridge commands from inside the OpenClaw container.
Editable destination source is mounted at `/project/platoon_destinations.json`; use bridge `snapshot` as the live source of truth after reload.

```bash
python3 /project/scripts/platoon_bridge_ctl.py snapshot
python3 /project/scripts/platoon_bridge_ctl.py readiness
python3 /project/scripts/platoon_bridge_ctl.py transfer <request_id>
python3 /project/scripts/platoon_bridge_ctl.py accept <request_id> --reason destination_match_confirmed --sender-agent platoon_b --receiver-agent platoon_a
python3 /project/scripts/platoon_bridge_ctl.py reject <request_id> --reason <reason> --sender-agent platoon_b --receiver-agent platoon_a
python3 /project/scripts/platoon_bridge_ctl.py commit <request_id>
python3 /project/scripts/platoon_bridge_ctl.py retry <request_id>
python3 /project/scripts/platoon_dialogue_guard.py inbound --agent platoon_b --message '<current Discord message>'
python3 /project/scripts/platoon_dialogue_guard.py validate-json --agent platoon_b --context-file /data/openclaw/.openclaw/workspace/data/platoon_decision_context.json --vehicle-id <vehicle_id>
python3 /project/scripts/platoon_dialogue_guard.py validate-request --a-list '<current Platoon A list>' --b-list '<current Platoon B list>' --request '<current request message>'
cat /data/openclaw/.openclaw/workspace/data/platoon_decision_context.json
```

## Safe Use

- Use `transfer <request_id>` and `snapshot` before accepting.
- Before any Discord response, run `platoon_dialogue_guard.py inbound`; if `allow_response` is false, stay silent.
- Before posting destinations, read `/data/openclaw/.openclaw/workspace/data/platoon_decision_context.json`; do not rely on examples or remembered prompt text.
- Before accepting a request, run `platoon_dialogue_guard.py validate-json`; if `valid` is false, reject or ask for a refreshed request once, then stop.
- Use `accept` only for a pending, validated transfer from `platoon_a` to `platoon_b`.
- Use `commit` only after `accept` succeeds.
- After every `commit`, run `readiness`; if it says `trigger_unconfirmed`, run `retry <request_id>` once.
- Use `readiness` after commit when explaining CARLA wait, merge-ready, timeout, or failure state.
- Use `reject` for stale, mismatched, leader, or duplicate requests.
- Do not call `/start_merge`; the bridge server triggers CARLA after commit.
