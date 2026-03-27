# Oracle Cloud Data Collector

## Current Setup (2026-03-26)

### VM Details

| Item | Value |
|------|-------|
| **Public IP** | `140.238.137.121` |
| **Shape** | VM.Standard.E2.1.Micro (AMD, 1 OCPU, 1 GB RAM) |
| **OS** | Ubuntu 22.04 Minimal (x86_64) |
| **Region** | ca-toronto-1 |
| **Availability Domain** | CQYv:CA-TORONTO-1-AD-1 |
| **Instance OCID** | `ocid1.instance.oc1.ca-toronto-1.an2g6ljrmq7n7jqcm5wbisviauoydb46mevrx4c4xbuvcxcwzvbaxtyagz7a` |
| **Tier** | Always Free |
| **Boot Volume** | 46.6 GB |
| **Python** | 3.12.13 (deadsnakes PPA) |
| **Package Manager** | uv |
| **Repo Path** | `/home/ubuntu/poly` |

### SSH Access

```bash
ssh -i ~/.ssh/oracle_poly.key ubuntu@140.238.137.121
```

Key files on local Mac:
- Private key: `~/.ssh/oracle_poly.key`
- Public key: `~/.ssh/oracle_poly.key.pub`

### OCI CLI

Configured at `~/.oci/config` with API signing key at `~/.oci/oci_api_key.pem`. The CLI can manage all Oracle Cloud resources from the local Mac.

### Networking

| Resource | OCID |
|----------|------|
| VCN | `ocid1.vcn.oc1.ca-toronto-1.amaaaaaamq7n7jqafh7re6nqmsphsdiotj3fx7nhngot26kjpknnflqsl7gq` |
| Subnet | `ocid1.subnet.oc1.ca-toronto-1.aaaaaaaazjz5eb2mubfcbsogyvfco3dkdmm4mbsmxvrehyupxjmndtztpvfq` |
| Internet Gateway | `ocid1.internetgateway.oc1.ca-toronto-1.aaaaaaaae5oyhyic2aztjev53zssa7ugwg2bu2iciznjwnet2o5wnykb7u4q` |
| Route Table | `ocid1.routetable.oc1.ca-toronto-1.aaaaaaaae4bwmdwzovfyzb6yoiystjhxanrq6kgggdt7tmlllqwq46yyjllq` |
| Security List | `ocid1.securitylist.oc1.ca-toronto-1.aaaaaaaa6jzn5fxsgao7o2q7rr7pfvfcf2qppu27gmx7psmxsrizzlbw2nca` |

Security list allows: SSH (port 22) inbound from 0.0.0.0/0, all outbound.

## How to Run Collectors

**IMPORTANT — Memory constraint**: The current VM has only **956 MB RAM**.

### Measured RAM usage (2026-03-26, 10 concurrent collectors)

| Collectors running | Used | Available | Per-collector avg |
|-------------------|------|-----------|-------------------|
| 0 (baseline OS) | ~165 MB | ~630 MB | — |
| 5 | ~300 MB | ~494 MB | ~27 MB |
| 8 | ~380 MB | ~413 MB | ~27 MB |
| 9 | ~409 MB | ~385 MB | ~27 MB |
| 10 | ~436 MB | ~358 MB | ~27 MB |

Each collector uses **~27 MB RAM** regardless of token count (NBA games with 80 tokens / 4 shards use similar RAM to NHL with 12 tokens / 1 shard). The OS baseline is ~165 MB.

**Maximum safe concurrent collectors: 10-12**. At 10 collectors (~436 MB used), ~358 MB remains available. Going beyond 12 risks OOM under memory spikes.

**Recommended: up to 10 concurrent collectors** to leave a comfortable margin. If running more than 10, stagger into batches by game start time. Monitor with `free -h` on the VM.

### First collection night (2026-03-26): 10 concurrent collectors

| Game | Config | Tokens | Start | Snapshots | Trades | Signals | Events | DB Size | Status |
|------|--------|--------|-------|-----------|--------|---------|--------|---------|--------|
| NBA Pelicans vs Pistons | `match_nba-nop-det-2026-03-26.json` | 84 | 7:00 PM | 13,854 | 6,525 | 124,908 | 329 | 55 MB | Complete |
| NBA Kings vs Magic | `match_nba-sac-orl-2026-03-26.json` | 80 | 7:00 PM | 13,561 | 6,367 | 130,256 | 303 | 56 MB | Complete |
| NBA Knicks vs Hornets | `match_nba-nyk-cha-2026-03-26.json` | 78 | 7:00 PM | 14,342 | 6,691 | 84,246 | 315 | 44 MB | Complete |
| CBB Texas vs Purdue | `match_cbb-tx-pur-2026-03-26.json` | 10 | 7:10 PM | 13,028+ | 6,346+ | 59,726+ | 0 | 36 MB | Still running, 0 events (fuzzy match bug) |
| CBB Illinois vs Houston | `match_cbb-ill-hou-2026-03-26.json` | 10 | 10:05 PM | 11,502 | 5,677 | 33,612 | 63 | 26 MB | Complete |
| NHL Wild vs Panthers | `match_nhl-min-fla-2026-03-26.json` | 12 | 7:00 PM | 3,104 | 1,497 | 42,608 | 7 | 17 MB | Complete |
| NHL Blackhawks vs Flyers | `match_nhl-chi-phi-2026-03-26.json` | 12 | 7:00 PM | 2,136+ | 1,015+ | 40,666+ | 11 | 15 MB | Relaunched (see note) |
| ATP Cerundolo vs Zverev | `match_atp-cerundo-zverev-2026-03-26.json` | 20 | 7:00 PM | 2,612 | 995 | 8,350 | 21 | 5.7 MB | Complete (Zverev 6-1, 6-2) |
| MLB Diamondbacks vs Dodgers | `match_mlb-ari-lad-2026-03-26.json` | 8 | 8:30 PM | 3,632+ | 1,791+ | 36,724+ | 0 | 16 MB | Relaunched (see note) |
| WTA Sabalenka vs Rybakina | `match_wta-sabalen-rybakin-2026-03-26.json` | 20 | 8:30 PM | 5,160 | 2,513 | 12,950 | 25 | 11 MB | Complete |

**Totals (at time of snapshot):** ~305 MB across 10 databases, 83K+ trades, 574K+ signals, 1,074+ events.

**Timeline:**
- **~7:20 PM ET**: All 10 collectors running, 0 errors, 319 MB RAM available. NBA games recovered from transient CDN 403.
- **~8:25 PM ET** (90 min): 247K signals, 17K trades, 719 events, 0 data gaps. NBA games richest at 500-826 signals/min.
- **~12:25 AM ET** (5.5 hrs): 7 of 10 games complete. Completed collectors killed to free RAM (172 MB → 532 MB available). MLB ARI-LAD and NHL CHI-PHI accidentally killed along with completed games (shared tmux session) and relaunched — **these two databases have a data gap** from ~12:25 AM when killed to relaunch. CBB TX-PUR unaffected (ran via nohup, not tmux).

**Issues observed:**
- **NHL score detection bug**: Both NHL games show period_end and timeout events but zero score_change events. Likely a bug in NHL client's score diff tracking, not actual 0-0 games. Needs investigation.
- **CBB fuzzy match failure**: Sports WS sees CBB games (`PUR vs TX`) but fuzzy matcher couldn't resolve abbreviated names to full config names (`Texas Longhorns`). Fix committed (consonant-abbreviation matching) but TX-PUR collector was not restarted, so 0 events. CBB ILL-HOU got 63 events (started after fix was pushed to VM).
- **WTA not on Sports WS**: The `wta` league is not broadcast. WTA Sabalenka-Rybakina still got 25 events — likely matched via a different league abbreviation or the WTA feed appeared later.
- **New Sports WS leagues discovered**: `cwbb` (women's CBB), `ufc`, `fif` (FIFA/soccer), `lol` (League of Legends) now visible.
- **Global WS disconnects**: Two Polymarket server-side disconnects (~23:26 and ~00:23 UTC) hit all collectors simultaneously. All recovered within 1-6 seconds, 0 data gaps.
- **Lesson learned**: When killing completed collectors in tmux, kill individual Python PIDs — not tmux sessions — to avoid accidentally killing still-running collectors in the same session.

### Automated overnight schedule (2026-03-26 → 2026-03-27 morning)

Two `nohup sleep + command` processes were scheduled on the VM:

| Time (UTC) | Time (ET) | Action | Log file |
|------------|-----------|--------|----------|
| 08:30 | 4:30 AM | `pkill -f "python -m collector"` — kill tonight's 3 remaining collectors (CBB TX-PUR, MLB ARI-LAD, NHL CHI-PHI) | `logs/auto_kill.log` |
| 09:00 | 5:00 AM | Launch 6 morning tennis collectors (30 min before first match) | `logs/auto_launch.log` |

**Morning matches (2026-03-27):**

| Match | Config (reused from prior day) | Tokens | Start (ET) | Tournament | Sports WS |
|-------|-------------------------------|--------|------------|------------|-----------|
| Coulibaly vs Vasilev | `match_atp-couliba-vasilev-2026-03-26.json` | 18 | 5:30 AM | Split | Likely (`challenger`) |
| Neumayer vs Ajdukovic | `match_atp-neumaye-ajdukov-2026-03-26.json` | 18 | 5:30 AM | Split | Likely (`challenger`) |
| Hercog vs Korpatsch | `match_wta-hercog-korpats-2026-03-25.json` | 18 | 6:00 AM | Dubrovnik | Uncertain (WTA not on Sports WS) |
| Kostovic vs Charaeva | `match_wta-kostovi-charaev-2026-03-26.json` | 18 | 6:00 AM | Dubrovnik | Uncertain |
| Garcia vs Lukas | `match_wta-garcia-lukas-2026-03-25.json` | 18 | 7:30 AM | Dubrovnik | Uncertain |
| Ruiz vs Butvila | `match_atp-ruiz-butvila-2026-03-27.json` | 18 | 10:30 AM | Alicante | Likely (`challenger`) |

**Note on reused configs**: These are multi-day tournament matches. Polymarket keeps the same event slug and token IDs across days, so configs from 2026-03-25 and 2026-03-26 work for 2026-03-27 matches. The `scheduled_start` is stale but the collector skips WAITING and goes straight to BACKOFF→LIVE.

**RAM estimate**: 6 collectors × 27 MB = ~162 MB. After auto-kill frees ~80 MB from tonight's collectors, ~530 MB available — comfortable.

**Morning verification** (run in a new Claude Code session):
```bash
# Check auto-kill and auto-launch logs
ssh -i ~/.ssh/oracle_poly.key ubuntu@140.238.137.121 \
  "cat ~/poly/logs/auto_kill.log ~/poly/logs/auto_launch.log 2>/dev/null"

# Verify collectors are running
ssh -i ~/.ssh/oracle_poly.key ubuntu@140.238.137.121 \
  "ps aux | grep 'python -m collector' | grep -v grep"

# Check memory
ssh -i ~/.ssh/oracle_poly.key ubuntu@140.238.137.121 "free -h | head -2"

# Check latest status from each collector
ssh -i ~/.ssh/oracle_poly.key ubuntu@140.238.137.121 \
  "cd ~/poly/logs && for f in collector_*2026-03-27*.log; do echo '---'; tail -1 \$f; done"

# Sync data to local machine
bash scripts/sync_from_cloud.sh
```

### Pushing configs to the VM

Config files are gitignored, so `git pull` won't bring new ones. Push configs from your local Mac before running:
```bash
rsync -avz -e "ssh -i ~/.ssh/oracle_poly.key" \
  /Users/god/vs_code/poly_market_v2/configs/ \
  ubuntu@140.238.137.121:/home/ubuntu/poly/configs/
```

### Running

Each collector launches in its **own tmux session** (named `col-<match_id>`), so they can be killed independently without affecting others.

**Scripts**: `scripts/cloud_launch.sh` and `scripts/cloud_kill.sh`

```bash
# SSH into the VM
ssh -i ~/.ssh/oracle_poly.key ubuntu@140.238.137.121

# Update code (configs come from rsync, not git)
cd ~/poly && git pull

# Launch collectors (each gets its own tmux session)
bash scripts/cloud_launch.sh \
  configs/match_nba-xxx-2026-03-27.json \
  configs/match_nba-yyy-2026-03-27.json \
  configs/match_nhl-zzz-2026-03-27.json

# List running collectors
tmux ls | grep '^col-'

# Attach to a specific collector to see output
tmux attach -t col-nba-xxx-2026-03-27

# Kill a specific finished collector (safe — won't affect others)
bash scripts/cloud_kill.sh nba-xxx-2026-03-27

# Kill all collectors whose games have ended (checks logs for game_end)
bash scripts/cloud_kill.sh --finished

# Kill all collectors
bash scripts/cloud_kill.sh --all

# Check memory
free -h
```

**From local Mac via SSH** (without interactive session):
```bash
# Launch
ssh -i ~/.ssh/oracle_poly.key ubuntu@140.238.137.121 \
  "cd ~/poly && bash scripts/cloud_launch.sh configs/match_nba-xxx-2026-03-27.json"

# Kill finished
ssh -i ~/.ssh/oracle_poly.key ubuntu@140.238.137.121 \
  "cd ~/poly && bash scripts/cloud_kill.sh --finished"

# Check status
ssh -i ~/.ssh/oracle_poly.key ubuntu@140.238.137.121 \
  "tmux ls 2>/dev/null | grep '^col-'"
```

## How to Sync Data

Script: `scripts/sync_from_cloud.sh`

After games are done, run from your local Mac:
```bash
bash scripts/sync_from_cloud.sh
```

This rsyncs `data/` and `logs/` from the VM to your local `poly_market_v2/` directory using the SSH key at `~/.ssh/oracle_poly.key`. The `--update` flag skips files already transferred unless the cloud version is newer.

**Important**: Only sync after collectors finish. Rsyncing live SQLite databases in WAL mode can produce inconsistent copies. NBA games typically end by ~11pm ET, NHL by ~10:30pm ET — sync the next morning to be safe.

## What Was Set Up

1. **Oracle Cloud account** created with Always Free tier in ca-toronto-1
2. **OCI CLI** installed via Homebrew, configured with API signing key
3. **VCN** (`poly-collector-vcn`) with internet gateway, route table, and security list (SSH open)
4. **Public subnet** (`poly-collector-subnet`) in 10.0.0.0/24
5. **VM instance** (`poly-collector`) launched with Ubuntu 22.04
6. **VM bootstrapped**: Python 3.12, uv, git, tmux, repo cloned, dependencies installed, 224 tests passing

## Current Limitations

### Small VM (1 GB RAM)

We got the AMD micro instance (1 OCPU, **1 GB RAM**) because the ARM A1.Flex (1 OCPU, **6 GB RAM**) was out of capacity in ca-toronto-1 at provisioning time. Based on measured usage (2026-03-26), the micro instance handles **up to 10 concurrent collectors comfortably** (~27 MB per collector, ~165 MB OS baseline). Beyond 12 concurrent collectors risks OOM. An ARM A1.Flex upgrade (6-12 GB RAM) would remove this constraint entirely.

### NBA CDN transient 403s from cloud IPs (discovered 2026-03-26)

The NBA CDN (`cdn.nba.com`) occasionally returns **transient 403 Access Denied** from Oracle Cloud IPs. This is **not a permanent block** — the same requests succeed minutes later. The issue is that the old backoff was too aggressive (30s→120s), so a transient 403 at startup could delay game event capture by several minutes.

**Fix applied**:
1. `nba_client.py`: Added explicit warning log on 403 (previously silent `GameNotStarted`)
2. `__main__.py`: Reduced backoff from 30-120s to 10-30s for faster recovery from transient blocks

**Observed behavior (2026-03-26)**: 3 NBA games started, all got initial 403. Two recovered within ~15 min and started capturing game events. Third was stuck in the old 120s backoff cycle. With the new 10-30s backoff, recovery should happen within 1-2 retries.

**Fallback** (if 403s become persistent): Switch NBA configs from `data_source: "nba_cdn"` to `"polymarket_sports_ws"`. Fewer event types (no fouls, turnovers, timeouts) but captures score/period changes needed for overshoot analysis.

---

## Instructions for Upgrading to ARM A1.Flex (for LLM agents)

The Oracle Cloud Free Tier allows **up to 4 OCPUs and 24 GB RAM total** across ARM A1.Flex instances. The current AMD micro instance is a fallback because ARM capacity was unavailable. Periodically retry to get the larger instance.

### Prerequisites

- OCI CLI configured at `~/.oci/config`
- SSH public key at `~/.ssh/oracle_poly.key.pub`
- All OCIDs listed in the Networking table above

### Step 1: Check ARM capacity

```bash
oci compute shape list \
  --compartment-id "ocid1.tenancy.oc1..aaaaaaaalqtfxegoimakidvc4z73zjrfclalfvlw4wabm3fkjgfybsjwai7q" \
  --availability-domain "CQYv:CA-TORONTO-1-AD-1" \
  --query "data[?shape=='VM.Standard.A1.Flex'].shape" \
  --output table
```

If the shape is listed, capacity may be available (listing doesn't guarantee launch success).

### Step 2: Find the ARM Ubuntu image

```bash
oci compute image list \
  --compartment-id "ocid1.tenancy.oc1..aaaaaaaalqtfxegoimakidvc4z73zjrfclalfvlw4wabm3fkjgfybsjwai7q" \
  --operating-system "Canonical Ubuntu" \
  --operating-system-version "22.04 Minimal aarch64" \
  --shape "VM.Standard.A1.Flex" \
  --query 'data[0].id' --raw-output
```

### Step 3: Launch the ARM instance

```bash
oci compute instance launch \
  --compartment-id "ocid1.tenancy.oc1..aaaaaaaalqtfxegoimakidvc4z73zjrfclalfvlw4wabm3fkjgfybsjwai7q" \
  --availability-domain "CQYv:CA-TORONTO-1-AD-1" \
  --display-name "poly-collector-arm" \
  --shape "VM.Standard.A1.Flex" \
  --shape-config '{"ocpus": 2, "memoryInGBs": 12}' \
  --image-id "<IMAGE_OCID_FROM_STEP_2>" \
  --subnet-id "ocid1.subnet.oc1.ca-toronto-1.aaaaaaaazjz5eb2mubfcbsogyvfco3dkdmm4mbsmxvrehyupxjmndtztpvfq" \
  --assign-public-ip true \
  --ssh-authorized-keys-file ~/.ssh/oracle_poly.key.pub
```

Recommended config: **2 OCPUs, 12 GB RAM** (still within the 4 OCPU / 24 GB free tier limit, leaves room for another instance if needed).

If you get `"Out of host capacity"` error, ARM capacity is still unavailable. Retry at a different time of day (early morning UTC tends to have better availability) or try weekly.

### Step 4: Bootstrap the new instance

Once launched, get the public IP:
```bash
INSTANCE_ID="<new instance OCID>"
COMPARTMENT="ocid1.tenancy.oc1..aaaaaaaalqtfxegoimakidvc4z73zjrfclalfvlw4wabm3fkjgfybsjwai7q"
VNIC_ID=$(oci compute vnic-attachment list --compartment-id "$COMPARTMENT" --instance-id "$INSTANCE_ID" --query 'data[0]."vnic-id"' --raw-output)
oci network vnic get --vnic-id "$VNIC_ID" --query 'data."public-ip"' --raw-output
```

Then bootstrap:
```bash
ssh -i ~/.ssh/oracle_poly.key ubuntu@<NEW_IP> << 'EOF'
sudo apt update && sudo apt install -y software-properties-common
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt install -y python3.12 python3.12-venv git tmux
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH=$HOME/.local/bin:$PATH
git clone https://github.com/OriginalGoku/poly.git ~/poly
cd ~/poly
uv venv --python python3.12
source .venv/bin/activate
uv pip install -r requirements.txt
mkdir -p data logs
python -m pytest tests/ -v
EOF
```

### Step 5: Migrate and decommission old instance

1. Sync any data from the old AMD instance to local Mac first
2. Update `scripts/sync_from_cloud.sh` with the new IP
3. Update the SSH config / this document with the new IP and instance OCID
4. Terminate the old AMD instance:
```bash
oci compute instance terminate \
  --instance-id "ocid1.instance.oc1.ca-toronto-1.an2g6ljrmq7n7jqcm5wbisviauoydb46mevrx4c4xbuvcxcwzvbaxtyagz7a" \
  --force
```

### Capacity Tips

- ARM free tier capacity is scarce. Toronto, Ashburn, and Phoenix are the most contested regions.
- If Toronto never has capacity, consider creating a new tenancy in a less popular region (e.g., `ca-montreal-1`, `us-chicago-1`, `us-sanjose-1`).
- Some users report success by trying the launch repeatedly via a cron job every 5-10 minutes until capacity opens up.
- The AMD micro instance (current) is a viable long-term fallback if ARM never becomes available — just limit concurrent collectors to 3-5 to stay within 1 GB RAM.
