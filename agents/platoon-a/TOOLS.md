# Tools - Platoon A

Run bridge commands from inside the OpenClaw container.

```bash
python3 /project/scripts/platoon_bridge_ctl.py snapshot
python3 /project/scripts/platoon_bridge_ctl.py readiness
python3 /project/scripts/platoon_bridge_ctl.py candidates platoon_a
python3 /project/scripts/platoon_bridge_ctl.py transfer <request_id>
python3 /project/scripts/platoon_bridge_ctl.py request <vehicle_id> platoon_a platoon_b --reason destination_match --sender-agent platoon_a --receiver-agent platoon_b
```

## Safe Use

- Use `snapshot` before creating a request and after the peer reports commit.
- Use `readiness` after commit/merging when explaining why CARLA has or has not started the physical merge.
- Use `candidates platoon_a` to confirm the bridge agrees with the destination match.
- Use `transfer <request_id>` when a request id appears in Discord.
- Do not call `accept`, `reject`, or `commit`; those are Platoon B actions.
- Do not call `/start_merge`; the bridge server triggers CARLA after commit.
