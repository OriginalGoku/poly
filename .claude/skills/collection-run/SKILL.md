---
name: collection-run
description: Launch data collectors on the Oracle VM. Rsyncs configs, generates a batched launch script (max 10 concurrent), SSHs to the VM, and runs collectors in tmux. Fire-and-forget.
disable-model-invocation: true
argument-hint: <collection-log-path or date>
---

# /collection-run

Launch collectors on the Oracle VM for a planned collection session.

## Arguments

`$ARGUMENTS` should be one of:
- A date like `2026-03-26` → resolves to `collection_logs/2026-03-26.md`
- A file path like `collection_logs/2026-03-26.md`

If no argument, check for today's collection log.

## Steps

### 1. Read VM details

Read `ORACLE_DATA_COLLECTOR.md` for:
- VM IP: `140.238.137.121`
- SSH key: `~/.ssh/oracle_poly.key`
- User: `ubuntu`
- Repo path: `/home/ubuntu/poly`
- RAM: 956 MB, ~27 MB per collector, max 10 concurrent

### 2. Extract match_ids from collection log

Read the collection log file. Parse the Game Roster table to extract all `match_id` values.

### 3. Verify configs exist locally

For each match_id, verify the config file exists at `configs/match_{match_id}.json`. Report any missing configs.

### 4. Rsync configs to VM

Generate and run the rsync command:
```bash
rsync -avz -e "ssh -i ~/.ssh/oracle_poly.key" \
  configs/ \
  ubuntu@140.238.137.121:/home/ubuntu/poly/configs/
```

### 5. Generate batched launch script

Create a launch script (`scripts/run_collection_YYYY-MM-DD.sh`) that:

1. **Sorts games by scheduled start time** (parse from config files or use insertion order)
2. **Groups into batches of max 10** concurrent collectors
3. For each batch:
   - Launches collectors as background processes with `&`
   - Stores PIDs
   - Calls `wait` to block until all processes in the batch complete
   - Runs `free -h` between batches
4. Includes a SIGINT/SIGTERM trap for graceful shutdown:
   ```bash
   trap 'echo "Shutting down..."; kill $(jobs -p) 2>/dev/null; wait; exit 0' SIGINT SIGTERM
   ```
5. Each collector command:
   ```bash
   python -m collector --config configs/match_{match_id}.json &>> logs/collection-YYYY-MM-DD.log &
   ```

Example script structure for 12 games:
```bash
#!/usr/bin/env bash
set -euo pipefail
trap 'echo "Shutting down..."; kill $(jobs -p) 2>/dev/null; wait; exit 0' SIGINT SIGTERM

echo "=== Batch 1/2 (10 collectors) ==="
python -m collector --config configs/match_game1.json &>> logs/collection-2026-03-26.log &
# ... 9 more
wait
free -h

echo "=== Batch 2/2 (2 collectors) ==="
python -m collector --config configs/match_game11.json &>> logs/collection-2026-03-26.log &
python -m collector --config configs/match_game12.json &>> logs/collection-2026-03-26.log &
wait

echo "=== All collectors finished ==="
```

### 6. Push script + launch on VM

Run the following sequence:

```bash
# Push the launch script
rsync -avz -e "ssh -i ~/.ssh/oracle_poly.key" \
  scripts/run_collection_YYYY-MM-DD.sh \
  ubuntu@140.238.137.121:/home/ubuntu/poly/scripts/

# SSH to VM: git pull, activate venv, start tmux, run script
ssh -i ~/.ssh/oracle_poly.key ubuntu@140.238.137.121 << 'EOF'
cd ~/poly
git pull
source .venv/bin/activate
mkdir -p logs
tmux new-session -d -s collection-YYYY-MM-DD "bash scripts/run_collection_YYYY-MM-DD.sh"
echo "tmux session started: collection-YYYY-MM-DD"
free -h
EOF
```

### 7. Report

Show the user:
- Tmux session name: `collection-YYYY-MM-DD`
- Batch layout (how many batches, games per batch)
- Commands for later:
  ```bash
  # Check status
  ssh -i ~/.ssh/oracle_poly.key ubuntu@140.238.137.121 "tmux attach -t collection-YYYY-MM-DD"

  # Check memory
  ssh -i ~/.ssh/oracle_poly.key ubuntu@140.238.137.121 "free -h"

  # Sync data after games finish
  bash scripts/sync_from_cloud.sh
  ```
