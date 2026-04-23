#!/usr/bin/env python3
"""
OUTPOST Watchdog v2 — Tier failover с защитой от зацикливания при DPI.
DeepWork AILab, 2026.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import pathlib
import random
import signal
import socket
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from enum import Enum

# ────────────────────────────── Константы ──────────────────────────────
STATE_PATH = pathlib.Path("/var/lib/outpost/state.json")
SECRET_PATH = pathlib.Path("/etc/outpost/trust.key")

PROBE_INTERVAL = 30
GRACE_AFTER_SWITCH = 45
FAIL_THRESHOLD = 3
COOLDOWN_BASE = 300
COOLDOWN_MAX = 1800
UDP_DEGRADED_COOLDOWN = 1200
PROBE_TIMEOUT = 3.0

LINK_PROBES_TCP = [("1.1.1.1", 443), ("9.9.9.9", 443), ("dns.google", 443)]
UDP_LINK_PROBES = [("stun.l.google.com", 19302), ("stun.cloudflare.com", 3478)]

class Transport(Enum):
    UDP = "udp"
    TCP = "tcp"

@dataclass
class Tier:
    name: str
    service: str
    transport: Transport
    probe_host: str
    probe_port: int
    iface: str | None = None

TIERS: list[Tier] = [
    Tier("amneziawg",     "wg-quick@awg0",    Transport.UDP, "1.1.1.1", 443, "awg0"),
    Tier("hysteria2",     "hysteria-client",  Transport.UDP, "1.1.1.1", 443, None),
    Tier("vless-reality", "xray",             Transport.TCP, "1.1.1.1", 443, None),
]

@dataclass
class State:
    current_tier: int = 0
    consecutive_fails: int = 0
    tier_cooldown_until: dict[str, float] = field(default_factory=dict)
    udp_degraded_until: float = 0.0
    last_switch_ts: float = 0.0

    @classmethod
    def load(cls) -> State:
        if STATE_PATH.exists():
            try:
                return cls(**json.loads(STATE_PATH.read_text()))
            except Exception:
                pass
        return cls()

    def save(self) -> None:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2))
        os.replace(tmp, STATE_PATH)

def _read_or_create_secret() -> bytes:
    if SECRET_PATH.exists():
        return SECRET_PATH.read_bytes()
    SECRET_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    key = os.urandom(32)
    SECRET_PATH.write_bytes(key)
    os.chmod(SECRET_PATH, 0o400)
    return key

def _machine_id() -> str:
    return pathlib.Path("/etc/machine-id").read_text().strip()

def get_node_trust() -> dict:
    mid = _machine_id()
    host = socket.gethostname()
    ts = int(time.time())
    payload = f"{mid}|{host}|{ts}".encode()
    sig = hmac.new(_read_or_create_secret(), payload, hashlib.sha256).hexdigest()
    return {
        "schema": "outpost-trust-v1",
        "node_id": hashlib.sha256(mid.encode()).hexdigest()[:16],
        "hostname": host,
        "issued_at": ts,
        "signature": sig,
    }

def tcp_probe(host: str, port: int, timeout: float = PROBE_TIMEOUT) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False

def udp_probe(host: str, port: int, timeout: float = PROBE_TIMEOUT) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.settimeout(timeout)
        stun_req = b"\x00\x01\x00\x00\x21\x12\xa4\x42" + os.urandom(12)
        try:
            ip = socket.gethostbyname(host)
        except OSError:
            return False
        s.sendto(stun_req, (ip, port))
        data, _ = s.recvfrom(1024)
        return len(data) >= 20
    except (OSError, socket.timeout):
        return False
    finally:
        s.close()

def internet_alive() -> bool:
    for host, port in LINK_PROBES_TCP:
        if tcp_probe(host, port, timeout=2.0):
            return True
    return False

def udp_globally_dead() -> bool:
    for host, port in UDP_LINK_PROBES:
        if udp_probe(host, port, timeout=2.5):
            return False
    return True

def _iface_up(name: str) -> bool:
    try:
        return pathlib.Path(f"/sys/class/net/{name}/operstate").read_text().strip() == "up"
    except FileNotFoundError:
        return False

def tier_healthy(tier: Tier) -> bool:
    if tier.iface and not _iface_up(tier.iface):
        return False
    return tcp_probe(tier.probe_host, tier.probe_port)

def run_systemctl(action: str, unit: str, timeout: int = 30) -> bool:
    try:
        r = subprocess.run(["systemctl", action, unit], capture_output=True, timeout=timeout)
        return r.returncode == 0
    except subprocess.TimeoutExpired:
        return False

def restart_service(unit: str) -> bool:
    return run_systemctl("restart", unit, timeout=30)

def stop_service(unit: str) -> None:
    run_systemctl("stop", unit, timeout=15)

def pick_next_tier(state: State, now: float) -> int | None:
    udp_dead = state.udp_degraded_until > now
    for i, tier in enumerate(TIERS):
        if i == state.current_tier:
            continue
        if state.tier_cooldown_until.get(tier.name, 0) > now:
            continue
        if udp_dead and tier.transport is Transport.UDP:
            continue
        return i
    return None

def backoff(fails: int) -> float:
    base = min(2 ** fails, 60)
    return base * random.uniform(0.8, 1.2)

def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    log = logging.getLogger("outpost-watchdog")
    trust = get_node_trust()
    log.info(f"OUTPOST Watchdog v2 started. Node={trust['node_id']}")
    state = State.load()
    running = {"flag": True}

    def _stop(signum, _frame):
        log.info("Signal received, shutting down")
        running["flag"] = False

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    while running["flag"]:
        now = time.time()
        current = TIERS[state.current_tier]

        if now - state.last_switch_ts < GRACE_AFTER_SWITCH:
            time.sleep(5)
            continue

        if not internet_alive():
            log.warning("Link-level internet is down — holding state")
            time.sleep(PROBE_INTERVAL)
            continue

        if tier_healthy(current):
            if state.consecutive_fails > 0:
                state.consecutive_fails = 0
                state.save()
            time.sleep(PROBE_INTERVAL)
            continue

        state.consecutive_fails += 1
        state.save()

        if state.consecutive_fails < FAIL_THRESHOLD:
            time.sleep(backoff(state.consecutive_fails))
            continue

        if current.transport is Transport.UDP and udp_globally_dead():
            log.error("UDP globally unreachable — marking UDP tiers as degraded")
            state.udp_degraded_until = now + UDP_DEGRADED_COOLDOWN

        cooldown = min(COOLDOWN_MAX, COOLDOWN_BASE * state.consecutive_fails)
        state.tier_cooldown_until[current.name] = now + cooldown

        next_idx = pick_next_tier(state, now)
        if next_idx is None:
            log.critical("All tiers in cooldown")
            soonest = min(state.tier_cooldown_until.values(), default=now + 60)
            time.sleep(max(10.0, soonest - now))
            continue

        next_tier = TIERS[next_idx]
        log.info(f"SWITCHING: {current.name} → {next_tier.name}")

        stop_service(current.service)
        if not restart_service(next_tier.service):
            state.tier_cooldown_until[next_tier.name] = now + 120
            state.save()
            time.sleep(30)
            continue

        state.current_tier = next_idx
        state.consecutive_fails = 0
        state.last_switch_ts = now
        state.save()

    return 0

if __name__ == "__main__":
    sys.exit(main())
