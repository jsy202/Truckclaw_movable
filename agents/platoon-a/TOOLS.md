# Tools - Platoon A

Run bridge commands from inside the OpenClaw container. The `openclaw:local`
image has `curl`, but not `python3`, so use the REST API directly.
Destination source for OpenClaw decisions is `/data/openclaw/.openclaw/workspace/data/vehicle_destinations.json`; use bridge `snapshot` only to confirm live transfer state after reload.

```bash
curl -s http://127.0.0.1:18801/snapshot
curl -s http://127.0.0.1:18801/readiness
curl -s http://127.0.0.1:18801/platoons/platoon_a/transfer-candidates
curl -s http://127.0.0.1:18801/transfers/<request_id>
curl -s -X POST http://127.0.0.1:18801/transfers -H 'Content-Type: application/json' -d '{"vehicle_id":"<vehicle_id>","from_platoon_id":"platoon_a","to_platoon_id":"platoon_b"}'
cat /data/openclaw/.openclaw/workspace/data/vehicle_destinations.json
cat /data/openclaw/.openclaw/workspace/data/platoon_decision_context.json
```

If a Python dialogue guard command appears in older notes, do not run it inside
`openclaw:local`; Python is unavailable there. Apply the same validation rules
manually from the JSON files and bridge REST responses.

## Safe Use

- Use `snapshot` before creating a request and after the peer reports commit.
- Before any negotiation Discord response, apply the inbound gate from `AGENTS.md`.
- Before posting destinations, read `/data/openclaw/.openclaw/workspace/data/vehicle_destinations.json`; do not rely on examples, bridge defaults, or remembered prompt text.
- Before creating or confirming a request, manually validate the vehicle against `vehicle_destinations.json`, `platoon_decision_context.json`, and bridge candidates; if mismatched, stop and report one mismatch message only.
- Use `readiness` after commit/merging when explaining why CARLA has or has not started the physical merge. If readiness is `idle`, poll once more before responding.
- Use `candidates platoon_a` to confirm the bridge agrees with the destination match.
- Use `transfer <request_id>` when a request id appears in Discord.
- Do not call `accept`, `reject`, or `commit`; those are Platoon B actions.
- Do not call `/start_merge`; the bridge server triggers CARLA after commit.
