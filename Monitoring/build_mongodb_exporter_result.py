from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

SNAPSHOT_1 = Path("Monitoring/mongodb_exporter_snaphost_1.txt")
SNAPSHOT_2 = Path("Monitoring/mongodb_exporter_snaphost_2.txt")
RESULT_PATH = Path("Monitoring/mongodb_exporter_result.txt")
PROGRESS_PATH = Path("Monitoring/mongodb_exporter_type_inference_progress.json")

SOURCE_URLS = [
    "https://prometheus.io/docs/concepts/metric_types/",
    "https://www.mongodb.com/docs/manual/reference/command/serverstatus/",
    "https://www.mongodb.com/docs/manual/reference/command/top/",
    "https://www.mongodb.com/docs/manual/reference/command/dbstats/",
    "https://docs.kernel.org/admin-guide/iostats.html",
    "https://man7.org/linux/man-pages/man5/proc_vmstat.5.html",
    "https://facebookmicrosites.github.io/psi/docs/overview",
]


def parse_samples(path: Path) -> dict[str, dict[str, str]]:
    samples: dict[str, dict[str, str]] = defaultdict(dict)
    for line in path.read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        match = re.match(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{.*\})? (.+)$", line)
        if match:
            name, labels, value = match.groups()
            samples[name][labels or ""] = value
    return samples


def parse_blocks(path: Path) -> tuple[list[str], dict[str, dict]]:
    order: list[str] = []
    blocks: dict[str, dict] = {}
    current: str | None = None
    for line in path.read_text().splitlines():
        if line.startswith("# HELP "):
            parts = line.split(maxsplit=3)
            family = parts[2]
            current = family
            order.append(family)
            blocks[family] = {
                "help": parts[3] if len(parts) > 3 else "",
                "type": None,
                "raw": [line],
                "samples": [],
            }
            continue
        if current is None:
            continue
        blocks[current]["raw"].append(line)
        if line.startswith("# TYPE "):
            _, _, family, metric_type = line.split()
            blocks[family]["type"] = metric_type
        elif line and not line.startswith("#"):
            blocks[current]["samples"].append(line)
    return order, blocks


def delta_pattern(name: str, before: dict[str, dict[str, str]], after: dict[str, dict[str, str]]) -> dict[str, int]:
    inc = dec = same = missing = 0
    for labels, value_after in after.get(name, {}).items():
        value_before = before.get(name, {}).get(labels)
        if value_before is None:
            missing += 1
            continue
        try:
            start = float(value_before)
            end = float(value_after)
        except ValueError:
            same += int(value_before == value_after)
            continue
        if end > start:
            inc += 1
        elif end < start:
            dec += 1
        else:
            same += 1
    return {"inc": inc, "dec": dec, "same": same, "missing": missing}


def contains_any(text: str, terms: list[str]) -> bool:
    return any(term in text for term in terms)


def ends_with_any(text: str, suffixes: list[str]) -> bool:
    return any(text.endswith(suffix) for suffix in suffixes)


def gauge_hint(name: str, help_text: str) -> bool:
    low = name.lower()
    help_low = help_text.lower()
    terms = [
        "average", "current", "currently", "available", "active", "waiting", "running",
        "size", "memory", "resident", "virtual", "cached", "free", "used", "capacity", "ratio",
        "percent", "maximum", "minimum", "max", "min", "timestamp", "localtime", "uptime",
        "threads", "state", "version", "queuedepths",
        "sessioncatalogsize", "backlogqueuedepths", "transitioning", "kernelsetting", "supported",
        "page_algorithm", "blockedopsgauge", "queryableencryption", "capped", "clustered",
        "fsusedsize", "fstotalsize", "avgobjsize", "totaltickets",
        "sustainerrate", "targetratelimit", "locksperkiloop",
    ]
    help_terms = ["current ", "currently ", "number of threads running", "number of clients currently"]
    return contains_any(low, terms) or contains_any(help_low, help_terms) or bool(re.search(r"mongodb_sys_vmstat_nr_", low))


def counter_hint(name: str, help_text: str) -> bool:
    low = name.lower()
    help_low = help_text.lower()
    terms = [
        "_total", "count", "counts", "bytesin", "bytesout", "physicalbytes", "requests", "accepted",
        "asserts", "opcounters", "acquirecount", "timeacquiring", "totaltime", "latency", "ops",
        "durationmicros", "timemicros", "committed", "aborted", "deleted", "inserted", "updated",
        "returned", "scanned", "evict", "written", "read", "reads", "writes", "sectors", "fault",
        "ctxt", "processes", "pg", "pswp", "rollback", "retried", "checkpoint", "cursorsclosed",
        "filesopened", "filesclosed", "bytessorted", "bytesspilled", "spilled", "sorted", "successful",
        "rejected", "interrupted", "kills", "conflicts", "created", "cleanedup", "ended", "refreshed",
        "failed", "timeout", "timedout", "expired", "arrayfilters", "pipeline", "jsonschema",
        "startedprocessing", "finishedprocessing", "measurementdelete", "measurementupdate", "metadelete",
        "metaupdate", "numbatches", "attempts", "selections", "changes", "timedout",
    ]
    help_terms = ["since the mongod or mongos last started", "total time", "the total number", "amount of time"]
    return contains_any(low, terms) or contains_any(help_low, help_terms) or bool(re.search(r"mongodb_sys_cpu_.*_ms$", low))


def infer_type(name: str, help_text: str, delta: dict[str, int]) -> tuple[str, str]:
    low = name.lower()
    if low.startswith("mongodb_dbstats_"):
        return "gauge", "dbstats fields are current database size/object statistics"
    if low.startswith("mongodb_top_"):
        return "counter", "top reports cumulative usage statistics since startup"
    if low in {"mongodb_ss_uptime", "mongodb_ss_uptimeestimate", "mongodb_ss_uptimemillis"}:
        return "gauge", "uptime values are current point-in-time durations since process start"
    if low in {"mongodb_start", "mongodb_end", "mongodb_ss_start", "mongodb_ss_end", "mongodb_ss_localtime"}:
        return "gauge", "timestamps/local time are point-in-time values"
    if low.startswith("mongodb_transportlayerstats_"):
        if contains_any(low, ["_start", "_end", "backlogqueuedepths"]):
            return "gauge", "transport start/end and backlog depth are current values"
        return "counter", "transport discard counters accumulate over time"
    if low.startswith("mongodb_sys_pressure_"):
        return "counter", "PSI total is accumulated microseconds"
    if low.startswith("mongodb_sys_memory_") or low.startswith("mongodb_sys_mounts_"):
        return "gauge", "system memory and mount capacity metrics are current values"
    if low.startswith("mongodb_sys_cpu_"):
        if contains_any(low, ["_btime", "num_cores_available_to_process", "num_logical_cores", "procs_blocked", "procs_running"]):
            return "gauge", "selected CPU proc fields are current/configuration values"
        return "counter", "remaining CPU proc fields are cumulative counters"
    if low.startswith("mongodb_sys_disks_"):
        return ("gauge", "disk io_in_progress is the only current-value field") if low.endswith("_io_in_progress") else ("counter", "other diskstats fields are cumulative counters")
    if low.startswith("mongodb_sys_netstat_"):
        gauge_terms = ["_defaultttl", "_forwarding", "_currestab", "_rtoalgorithm", "_rtomax", "_rtomin", "_maxconn"]
        return ("gauge", "selected SNMP fields are current/configuration values") if contains_any(low, gauge_terms) else ("counter", "other SNMP fields are cumulative counters")
    if low.startswith("mongodb_sys_vmstat_"):
        return ("gauge", "nr_* vmstat fields are current quantities") if re.search(r"mongodb_sys_vmstat_nr_", low) else ("counter", "other vmstat fields are cumulative counters")
    if low.startswith("mongodb_ss_changestreampreimages_purgingjob_"):
        if low in {
            "mongodb_ss_changestreampreimages_purgingjob_maxstartwalltimemillis",
            "mongodb_ss_changestreampreimages_purgingjob_maxtimestampeligiblefortruncate",
            "mongodb_ss_changestreampreimages_purgingjob_timeelapsedmillis",
        }:
            return "gauge", "change stream pre-image purging job exposes current wall-time and elapsed-time values"
        return "counter", "change stream pre-image purging job counters accumulate passes, scans, deletions, and bytes removed"
    if low == "mongodb_ss_connections":
        return "gauge", "mixed family; most conn_type values are current gauges, while totalCreated is counter-like"
    if low.startswith("mongodb_ss_connections_establishmentratelimit_"):
        return "counter", "rate-limit event totals accumulate over time"
    if low.startswith("mongodb_ss_asserts") or low.startswith("mongodb_ss_opcounters"):
        return "counter", "serverStatus assert/op counters are cumulative totals"
    if low.startswith("mongodb_ss_readconcerncounters") or low.startswith("mongodb_ss_readpreferencecounters"):
        return "counter", "concern/preference counters accumulate by request"
    if low.startswith("mongodb_ss_mem_") or low.startswith("mongodb_ss_featurecompatibilityversion_") or low.startswith("mongodb_ss_querysettings_"):
        return "gauge", "memory/version/query-settings size metrics are point-in-time values"
    if low.startswith("mongodb_ss_metrics_commands_"):
        return "counter", "serverStatus.metrics.commands tracks cumulative command and feature usage totals"
    if low.startswith("mongodb_ss_metrics_cursor_"):
        return ("gauge", "serverStatus.metrics.cursor.open reports current open cursors by type") if low == "mongodb_ss_metrics_cursor_open" else ("counter", "remaining serverStatus.metrics.cursor fields count cursor events by bucket")
    if low.startswith("mongodb_ss_metrics_getlasterror_"):
        return "counter", "serverStatus.metrics.getLastError fields are cumulative request and wait totals"
    if low.startswith("mongodb_ss_metrics_operation_"):
        return "counter", "serverStatus.metrics.operation fields accumulate operation outcomes and wait times"
    if low.startswith("mongodb_ss_metrics_queryexecutor_"):
        return "counter", "serverStatus.metrics.queryExecutor fields are cumulative scan counters"
    if low.startswith("mongodb_ss_metrics_querystats_"):
        if low in {
            "mongodb_ss_metrics_querystats_maxsizebytes",
            "mongodb_ss_metrics_querystats_numentries",
            "mongodb_ss_metrics_querystats_numpartitions",
            "mongodb_ss_metrics_querystats_querystatsstoresizeestimatebytes",
        }:
            return "gauge", "queryStats size and entry-count fields are current store state values"
        return "counter", "remaining queryStats fields accumulate evictions, rate limits, and write/error events"
    if low.startswith("mongodb_ss_metrics_timeseries_"):
        return "counter", "serverStatus.metrics.timeseries fields accumulate time-series write/update/delete events"
    if low.startswith("mongodb_ss_flowcontrol_"):
        if low in {
            "mongodb_ss_flowcontrol_enabled",
            "mongodb_ss_flowcontrol_islagged",
            "mongodb_ss_flowcontrol_locksperkiloop",
            "mongodb_ss_flowcontrol_sustainerrate",
            "mongodb_ss_flowcontrol_targetratelimit",
        }:
            return "gauge", "flow control state/rate metrics are current values"
        return "counter", "flow control time accumulators are cumulative"
    if low.startswith("mongodb_ss_globallock_"):
        return ("gauge", "active/current queue metrics are current values") if contains_any(low, ["activeclients", "currentqueue"]) else ("counter", "global lock totals accumulate")
    if low.startswith("mongodb_ss_locks_"):
        return "counter", "lock acquisition metrics are cumulative counts"
    if low.startswith("mongodb_ss_extra_info_"):
        return ("gauge", "thread and resident-set values are point-in-time") if contains_any(low, ["threads", "maximum_resident_set_kb"]) else ("counter", "resource usage and PSI totals accumulate")
    if low.startswith("mongodb_ss_electionmetrics_"):
        return ("gauge", "average catch-up ops is a current average") if "average" in low else ("counter", "election metrics count takeover/step-up events")
    if low.startswith("mongodb_ss_indexbuilds_"):
        return ("gauge", "index build phase values represent current phase counts") if "_phases_" in low else ("counter", "index build totals/failures are event counters")
    if low.startswith("mongodb_ss_indexbulkbuilder_"):
        return ("gauge", "memory usage is point-in-time") if "memusage" in low else ("counter", "bulk builder byte/file counters accumulate")
    if low.startswith("mongodb_ss_ftdccollectionmetrics_"):
        return "counter", "FTDC collection metrics accumulate runs, delays, and total collection duration"
    if low.startswith("mongodb_ss_batcheddeletes_"):
        return ("gauge", "staged size is current memory footprint") if "stagedsizebytes" in low else ("counter", "batched delete work metrics accumulate over runs")
    if low.startswith("mongodb_ss_opworkingtime_"):
        return "counter", "opWorkingTime exposes cumulative ops and working time"
    if low.startswith("mongodb_ss_twophasecommitcoordinator_currentinsteps_"):
        return "gauge", "currentInSteps reports current in-flight counts"
    if low.startswith("mongodb_ss_twophasecommitcoordinator_"):
        return "counter", "two-phase commit completed totals accumulate"
    if low.startswith("mongodb_ss_logicalsessionrecordcache_"):
        if contains_any(low, ["timestamp", "sessioncatalogsize"]):
            return "gauge", "timestamps and catalog size are point-in-time values"
        return "counter", "job durations and work counts accumulate per run"
    if low.startswith("mongodb_ss_intentregistry_"):
        return "counter", "declared intents are cumulative counts"
    if low.startswith("mongodb_ss_queues_") or low.startswith("mongodb_ss_wt_concurrenttransactions_"):
        gauge_suffixes = ["_available", "_out", "_processing", "_totaltickets", "_queuelength", "_maxacquisitiondelinquencymillis", "_totalavailabletokens"]
        return ("gauge", "ticket/queue depth metrics are current values") if ends_with_any(low, gauge_suffixes) else ("counter", "queue activity metrics accumulate over time")
    if ends_with_any(low, [
        "_file_magic_number",
        "_file_major_version_number",
        "_minor_version_number",
        "_number_of_key_value_pairs",
        "_number_of_files_remaining_for_migration_completion",
        "_number_of_sessions_without_a_sweep_for_5_minutes",
        "_number_of_sessions_without_a_sweep_for_60_minutes",
        "_number_of_pre_allocated_log_files_to_create",
    ]):
        return "gauge", "descriptor and remaining-item fields expose current state rather than cumulative events"
    if low.startswith("mongodb_ss_wt_") or low.startswith("mongodb_ss_metrics_") or low.startswith("mongodb_ss_"):
        if gauge_hint(name, help_text) and not counter_hint(name, help_text):
            return "gauge", "name/help keywords align with current-value semantics"
        if counter_hint(name, help_text) and not gauge_hint(name, help_text):
            return "counter", "name/help keywords align with cumulative counter semantics"
        if delta["dec"] > 0:
            return "gauge", "value decreases between snapshots, so it is gauge-like"
        if delta["inc"] > 0 and delta["dec"] == 0:
            return "counter", "monotonic increase between snapshots suggests counter semantics"
    if gauge_hint(name, help_text) and not counter_hint(name, help_text):
        return "gauge", "fallback keyword match suggests point-in-time gauge"
    if counter_hint(name, help_text):
        return "counter", "fallback keyword match suggests cumulative counter"
    if delta["dec"] > 0:
        return "gauge", "fallback to gauge because the value decreased between snapshots"
    if delta["inc"] > 0 and delta["dec"] == 0:
        return "counter", "fallback to counter because the value only increased"
    return "gauge", "default conservative fallback for ambiguous family"


def build_files() -> None:
    before = parse_samples(SNAPSHOT_1)
    order, blocks = parse_blocks(SNAPSHOT_2)
    after = parse_samples(SNAPSHOT_2)

    typed_families: list[str] = []
    inferred_families: list[dict] = []
    result_lines: list[str] = []

    for family in order:
        block = blocks[family]
        if block["type"] not in {None, "untyped"}:
            typed_families.append(family)
            result_lines.extend(block["raw"])
            continue
        delta = delta_pattern(family, before, after)
        guessed_type, reason = infer_type(family, block["help"], delta)
        inferred_families.append(
            {
                "family": family,
                "help": block["help"],
                "guessed_type": guessed_type,
                "reason": reason,
                "delta": delta,
            }
        )

    progress = {
        "generated_at": datetime.now(UTC).isoformat(),
        "snapshot_1": str(SNAPSHOT_1),
        "snapshot_2": str(SNAPSHOT_2),
        "sources": SOURCE_URLS,
        "typed_family_count": len(typed_families),
        "inferred_family_count": len(inferred_families),
        "inferred_type_counts": dict(Counter(item["guessed_type"] for item in inferred_families)),
        "notes": {
            "mongodb_ss_connections": "Family is mixed: most conn_type labels are gauge-like, conn_type=totalCreated is counter-like. Result file uses gauge for the family and preserves the raw samples.",
            "policy": "Typed families from snapshot 2 are copied directly. Families with untyped/missing TYPE are appended at the bottom with inferred gauge/counter types.",
        },
        "inferred_families": inferred_families,
    }
    PROGRESS_PATH.write_text(json.dumps(progress, indent=2) + "\n")

    result_lines.append("")
    result_lines.append("# Inferred metric families appended below.")
    result_lines.append("# Types were inferred from snapshot deltas plus Prometheus, MongoDB, and Linux procfs documentation.")
    result_lines.append("# NOTE: mongodb_ss_connections is a mixed family; it is typed as gauge here because most conn_type values are current values.")
    for item in inferred_families:
        block = blocks[item["family"]]
        result_lines.append("")
        result_lines.append(f"# HELP {item['family']} {block['help']}")
        result_lines.append(f"# TYPE {item['family']} {item['guessed_type']}")
        result_lines.extend(block["samples"])

    RESULT_PATH.write_text("\n".join(result_lines) + "\n")


if __name__ == "__main__":
    build_files()