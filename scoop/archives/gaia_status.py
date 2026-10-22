"""Is Gaia answering, and is our job queue clear? Used by the field pipeline before it
queries and by the chat agent when a count or build is slow or fails.

Async TAP jobs keep running on the server after the client gives up, and the servers run
only a couple per IP at once, so abandoned jobs queue every later query behind them (seen
2026-10-21: twenty abandoned counts over the Galactic plane stalled Heidelberg for hours).
The pipeline names its jobs ``scoop:<host>:<pid>``; a job with that name from this host
whose process is gone is stale and safe to abort. Other unfinished jobs from this IP (a
teammate's, an unnamed one) are only reported.
"""
from __future__ import annotations

import os
import re
import socket
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

SERVERS = {"heidelberg": "https://gaia.ari.uni-heidelberg.de/tap",
           "esa": "https://gea.esac.esa.int/tap-server/tap"}
PROBE_ADQL = "SELECT TOP 1 source_id FROM gaiadr3.gaia_source"
# ESA lists only an authenticated user's jobs, so the queue is read from Heidelberg alone.
QUEUE_SERVERS = ("heidelberg",)
UNFINISHED = ("PENDING", "QUEUED", "EXECUTING")
JOB_NAME = re.compile(r"scoop:(?P<host>[^:]+):(?P<pid>\d+)")


def job_name() -> str:
    """The name this process gives its async jobs."""
    return f"scoop:{socket.gethostname()}:{os.getpid()}"


def probe(url: str, timeout_s: float = 20.0) -> dict:
    """One tiny synchronous query: {ok, seconds, error}."""
    t = time.time()
    body = urllib.parse.urlencode({"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "csv",
                                   "QUERY": PROBE_ADQL}).encode()
    try:
        with urllib.request.urlopen(url + "/sync", data=body, timeout=timeout_s) as r:
            ok = r.status == 200 and len(r.read().splitlines()) >= 2
        return {"ok": ok, "seconds": round(time.time() - t, 1), "error": None if ok else f"HTTP {r.status}"}
    except Exception as e:
        return {"ok": False, "seconds": round(time.time() - t, 1), "error": f"{type(e).__name__}: {e}"}


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _get(url: str, timeout_s: float) -> str:
    with urllib.request.urlopen(url, timeout=timeout_s) as r:
        return r.read().decode(errors="replace")


def queue(server: str = "heidelberg", timeout_s: float = 20.0) -> list[dict]:
    """Unfinished async jobs from this IP: id, phase, created, name, stale, query (start)."""
    base = SERVERS[server] + "/async"
    listing = _get(base, timeout_s)
    jobs = []
    for m in re.finditer(r'jobref id="([^"]+)".*?<phase>(\w+)</phase>.*?<creationTime>([^<]+)', listing, re.S):
        if m.group(2) not in UNFINISHED:
            continue
        detail = _get(f"{base}/{m.group(1)}", timeout_s)
        name = re.search(r'id="jobname">([^<]*)', detail)
        query = re.search(r'id="query">([^<]*)', detail)
        tag = JOB_NAME.fullmatch(name.group(1)) if name else None
        jobs.append({"id": m.group(1), "phase": m.group(2), "created": m.group(3),
                     "name": name.group(1) if name else None,
                     "stale": bool(tag and tag["host"] == socket.gethostname() and not _alive(int(tag["pid"]))),
                     "query": (query.group(1) if query else "")[:160]})
    return jobs


def abort(server: str, job_id: str, timeout_s: float = 20.0) -> bool:
    req = urllib.request.Request(f"{SERVERS[server]}/async/{job_id}/phase", data=b"PHASE=ABORT")
    try:
        urllib.request.urlopen(req, timeout=timeout_s).close()
        return True
    except Exception:
        return False


def status(clean_up: bool = False) -> dict:
    """Health of each server and the state of our job queue; with ``clean_up``, abort the
    stale SCOOP jobs (never anyone else's)."""
    with ThreadPoolExecutor(max_workers=len(SERVERS)) as pool:
        health = dict(zip(SERVERS, pool.map(probe, SERVERS.values())))
    out: dict = {"servers": health, "queues": {}, "aborted": []}
    for server in QUEUE_SERVERS:
        if not health[server]["ok"]:
            continue
        try:
            jobs = queue(server)
        except Exception as e:
            out["queues"][server] = {"error": f"{type(e).__name__}: {e}"}
            continue
        if clean_up:
            for j in jobs:
                if j["stale"] and abort(server, j["id"]):
                    out["aborted"].append(j["id"])
            jobs = [j for j in jobs if j["id"] not in out["aborted"]]
        out["queues"][server] = {
            "executing": sum(j["phase"] == "EXECUTING" for j in jobs),
            "queued": sum(j["phase"] != "EXECUTING" for j in jobs),
            "stale_scoop_jobs": sum(j["stale"] for j in jobs),
            "jobs": jobs[:10]}
    out["diagnosis"] = diagnose(out)
    return out


def diagnose(st: dict) -> list[str]:
    """Plain-language findings for an agent (and the user)."""
    found = []
    down = [s for s, h in st["servers"].items() if not h["ok"]]
    if len(down) == len(st["servers"]):
        found.append("No Gaia server answers a one-row query: most likely this machine's network (or both "
                     "archives are down). Tell the user; retrying will not help until the network is back.")
    elif down:
        found.append(f"{', '.join(down)} not answering ({'; '.join(st['servers'][s]['error'] or '' for s in down)}); "
                     "the pipeline uses the other server.")
    for server, q in st["queues"].items():
        if "error" in q:
            found.append(f"Could not read the {server} job queue: {q['error']}.")
            continue
        if st.get("aborted"):
            found.append(f"Aborted {len(st['aborted'])} stale SCOOP job(s) on {server}.")
        if q["stale_scoop_jobs"]:
            found.append(f"{q['stale_scoop_jobs']} stale SCOOP job(s) on {server} from runs that were stopped; "
                         "gaia_status(clean_up=true) aborts them.")
        others = q["executing"] + q["queued"] - q["stale_scoop_jobs"]
        if others and q["queued"]:
            found.append(f"{server} has {q['executing']} executing and {q['queued']} queued job(s) from this IP; new "
                         "queries wait behind them. Those not marked stale may be a running field or a teammate's: "
                         "tell the user rather than aborting them.")
    if not found:
        found.append("Both Gaia servers answer and the job queue is clear: a slow count is the query itself "
                     "(a large or dense region, e.g. near the Galactic plane).")
    return found


__all__ = ["SERVERS", "abort", "diagnose", "job_name", "probe", "queue", "status"]
