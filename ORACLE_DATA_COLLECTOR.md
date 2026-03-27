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

| Game | Config | Tokens | Start | Events | Notes |
|------|--------|--------|-------|--------|-------|
| NBA Pelicans vs Pistons | `match_nba-nop-det-2026-03-26.json` | 84 | 7:00 PM | Yes | Recovered from transient 403 |
| NBA Knicks vs Hornets | `match_nba-nyk-cha-2026-03-26.json` | 78 | 7:00 PM | Yes | Recovered from transient 403 |
| NBA Kings vs Magic | `match_nba-sac-orl-2026-03-26.json` | 80 | 7:00 PM | Yes | Recovered from transient 403 |
| CBB Texas vs Purdue | `match_cbb-tx-pur-2026-03-26.json` | 10 | 7:10 PM | No | CBB not on Sports WS tonight |
| NHL Wild vs Panthers | `match_nhl-min-fla-2026-03-26.json` | 12 | 7:00 PM | Pending | NHL API accessible, awaiting plays |
| NHL Blackhawks vs Flyers | `match_nhl-chi-phi-2026-03-26.json` | 12 | 7:00 PM | Pending | NHL API accessible, awaiting plays |
| ATP Cerundolo vs Zverev | `match_atp-cerundo-zverev-2026-03-26.json` | 20 | 7:00 PM | Yes | Sports WS capturing events |
| MLB Diamondbacks vs Dodgers | `match_mlb-ari-lad-2026-03-26.json` | 8 | 8:30 PM | Pending | Pre-game |
| WTA Sabalenka vs Rybakina | `match_wta-sabalen-rybakin-2026-03-26.json` | 20 | 8:30 PM | Pending | Pre-game |
| CBB Illinois vs Houston | `match_cbb-ill-hou-2026-03-26.json` | 10 | 10:05 PM | Pending | Pre-game |

**Status snapshot (~7:20 PM ET):** All 10 collectors running, 0 errors, 319 MB available. All 3 NBA games recovered from transient NBA CDN 403 and are capturing game events.

**Quality report (~8:25 PM ET, 90 min into collection):** 247K signals, 17K trades, 719 events, 0 data gaps across all 10 games. NBA games are the richest (500-826 signals/min, 160+ events each at halftime). ATP match completed cleanly (21 events). Two global Polymarket WS disconnects (23:26 and 00:23 UTC) — all collectors recovered within 1-6 seconds. See issues below.

**Issues observed:**
- **NHL 0-0 anomaly**: Both NHL games show period_end and timeout events but zero score_change events through 3 periods. Likely a bug in NHL score change detection, not actual 0-0 games.
- **CBB no game events**: Sports WS sees CBB games (`PUR vs TX`) but fuzzy matcher can't resolve abbreviated names to config names (`Texas Longhorns`). Fix committed but collectors need restart.
- **WTA not on Sports WS**: The `wta` league is not broadcast on the Polymarket Sports WS feed at all. WTA matches will have market data only, no game events.
- **New Sports WS leagues discovered**: `cwbb` (women's CBB), `ufc`, `fif` (FIFA/soccer), `lol` (League of Legends) now visible alongside existing leagues.

### Pushing configs to the VM

Config files are gitignored, so `git pull` won't bring new ones. Push configs from your local Mac before running:
```bash
rsync -avz -e "ssh -i ~/.ssh/oracle_poly.key" \
  /Users/god/vs_code/poly_market_v2/configs/ \
  ubuntu@140.238.137.121:/home/ubuntu/poly/configs/
```

### Running

```bash
# SSH into the VM
ssh -i ~/.ssh/oracle_poly.key ubuntu@140.238.137.121

# Start a tmux session
cd ~/poly && source .venv/bin/activate
tmux new -s tonight

# Update code (configs come from rsync, not git)
git pull

# Run a few collectors manually (DO NOT use run_tonight.sh if it launches >5)
python -m collector --config configs/match_nba-xxx-yyy-2026-03-26.json &
python -m collector --config configs/match_nba-aaa-bbb-2026-03-26.json &
python -m collector --config configs/match_nhl-ccc-ddd-2026-03-26.json &

# Check memory usage
free -h

# Detach: Ctrl-B then D
# Reattach later: tmux attach -t tonight
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
