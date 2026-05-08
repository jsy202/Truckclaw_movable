# Tools - Platoon B

Run bridge commands from inside the OpenClaw container.

```bash
python3 /project/scripts/platoon_bridge_ctl.py snapshot
python3 /project/scripts/platoon_bridge_ctl.py readiness
python3 /project/scripts/platoon_bridge_ctl.py transfer <request_id>
python3 /project/scripts/platoon_bridge_ctl.py accept <request_id> --reason destination_match_confirmed --sender-agent platoon_b --receiver-agent platoon_a
python3 /project/scripts/platoon_bridge_ctl.py reject <request_id> --reason <reason> --sender-agent platoon_b --receiver-agent platoon_a
python3 /project/scripts/platoon_bridge_ctl.py commit <request_id>
```

## Safe Use

- Use `transfer <request_id>` and `snapshot` before accepting.
- Use `accept` only for a pending, validated transfer from `platoon_a` to `platoon_b`.
- Use `commit` only after `accept` succeeds.
- Use `readiness` after commit when explaining CARLA wait, merge-ready, timeout, or failure state.
- Use `reject` for stale, mismatched, leader, or duplicate requests.
- Do not call `/start_merge`; the bridge server triggers CARLA after commit.
