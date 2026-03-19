from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

SOURCE = Path("Monitoring/mongodb_exporter_result.txt")
OUTPUT = Path("Monitoring/mongodb_exporter_all_metrics_dashboard.json")
PROGRESS = Path("Monitoring/mongodb_exporter_all_metrics_progress.json")
PROM_DS = {"type": "prometheus", "uid": "${DS_PROMETHEUS}"}
ROW_ORDER = ["Overview", "MongoDB Core", "Database Stats", "Top Operations", "Server Status", "Server Status Metrics", "WiredTiger", "System Metrics", "Exporter Runtime", "Other"]
IGNORE_LABELS = {"cl_id", "cl_role"}
LABEL_PRIORITY = ["database", "collection", "namespace", "cmd_name", "op_type", "legacy_op_type", "doc_op_type", "conn_type", "csr_type", "count_type", "member_state", "member_idx", "state", "type", "resource", "lock_mode", "device", "mountpoint", "cpu", "mode", "quantile", "version", "cluster_role", "exporter", "collector"]
RATE_INTERVAL = "$__rate_interval"
MAX_NAMES_PER_PANEL = 20


def parse_result_snapshot() -> tuple[dict[str, str], dict[str, str], dict[str, str], dict[str, set[str]]]:
    family_types: dict[str, str] = {}
    family_help: dict[str, str] = {}
    sample_family: dict[str, str] = {}
    sample_labels: dict[str, set[str]] = defaultdict(set)
    current_family: str | None = None
    for line in SOURCE.read_text().splitlines():
        if line.startswith("# HELP "):
            parts = line.split(maxsplit=3)
            current_family = parts[2]
            family_help[current_family] = parts[3] if len(parts) > 3 else ""
        elif line.startswith("# TYPE "):
            _, _, family, metric_type = line.split()
            family_types[family] = metric_type
        elif current_family and line and not line.startswith("#"):
            match = re.match(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{([^}]*)\})? (.+)$", line)
            if not match:
                continue
            name, _, labels, _ = match.groups()
            sample_family.setdefault(name, current_family)
            if labels:
                for item in labels.split(","):
                    sample_labels[name].add(item.split("=", 1)[0])
    return family_types, family_help, sample_family, sample_labels


def explicit_kind(name: str, family: str, family_types: dict[str, str]) -> str:
    metric_type = family_types[family]
    if metric_type == "summary":
        return "counter" if name.endswith("_sum") or name.endswith("_count") else "gauge"
    return "counter" if metric_type == "counter" else "gauge"


def humanize(text: str) -> str:
    words: list[str] = []
    for token in text.split("_"):
        if not token:
            continue
        words.append(token if any(ch.isupper() for ch in token[1:]) else token.capitalize())
    return " ".join(words)


def bucket_for(name: str) -> tuple[str, str]:
    parts = name.split("_")
    if name.startswith("mongodb_ss_metrics_"):
        return "Server Status Metrics", "_".join(parts[:4])
    if name.startswith("mongodb_ss_wt_"):
        return "WiredTiger", "_".join(parts[:4])
    if name.startswith("mongodb_ss_"):
        return "Server Status", "_".join(parts[:3])
    if name.startswith("mongodb_sys_"):
        size = 4 if len(parts) >= 4 and parts[2] in {"netstat", "mounts"} else 3
        return "System Metrics", "_".join(parts[:size])
    if name.startswith("mongodb_dbstats_"):
        return "Database Stats", "mongodb_dbstats"
    if name.startswith("mongodb_top_"):
        return "Top Operations", "_".join(parts[:3])
    if name.startswith("collector_"):
        return "Exporter Runtime", "collector_scrape"
    if name.startswith("go_") or name.startswith("process_"):
        return "Exporter Runtime", "_".join(parts[:2])
    if name.startswith("mongodb_transportLayerStats_") or name.startswith("mongodb_profile_") or name.startswith("mongodb_fcv_") or name.startswith("mongodb_pbm_") or name in {"mongodb_up", "mongodb_start", "mongodb_end"}:
        return "MongoDB Core", "mongodb_core"
    if name.startswith("mongodb_"):
        return "MongoDB Core", "_".join(parts[:2])
    return "Other", "_".join(parts[:2])


def title_for(prefix: str) -> str:
    mapping = {"mongodb_core": "Core", "mongodb_dbstats": "Database Stats", "collector_scrape": "Collector Scrape"}
    if prefix in mapping:
        return mapping[prefix]
    for marker in ("mongodb_ss_metrics_", "mongodb_ss_wt_", "mongodb_ss_", "mongodb_sys_", "mongodb_top_", "mongodb_", "go_", "process_"):
        if prefix.startswith(marker):
            return humanize(prefix[len(marker) :])
    return humanize(prefix)


def suffix_after_prefix(name: str, prefix: str) -> str:
    if name == prefix:
        return "value"
    if name.startswith(prefix + "_"):
        return name[len(prefix) + 1 :] or "value"
    if name.startswith(prefix):
        return name[len(prefix) :].lstrip("_") or "value"
    for marker in ("mongodb_", "go_", "process_", "collector_"):
        if name.startswith(marker):
            return name[len(marker) :] or "value"
    return name


def infer_base_unit(name: str, help_text: str) -> str:
    low = f"{name} {help_text}".lower()
    if any(term in low for term in ["percent", "percentage", " ratio", "_ratio", "utilization"]):
        return "percent"
    if any(term in low for term in ["totalmicros", "micros", "microseconds"]):
        return "µs"
    if any(term in low for term in ["bytes", "byte", "resident", "virtual", "memory", "memusage", "size", "capacity", "cache", "fsusedsize", "fstotalsize", "avgobjsize"]):
        return "bytes"
    if "millis" in low or "milliseconds" in low or re.search(r"(^|[_ ])ms($|[_ ])", low):
        return "ms"
    if any(term in low for term in ["duration_seconds", " uptime ", " uptime", "btime"]) or re.search(r"(^|[_ ])seconds($|[_ ])", low):
        return "s"
    if "sectors" in low:
        return "sectors"
    if any(term in low for term in ["requests", "numrequests"]):
        return "requests"
    if any(term in low for term in ["ops", "opcounters", "cursorinsert", "cursorremove"]):
        return "ops"
    if any(term in low for term in ["connections", "threads", "tickets", "queue", "clients", "cursors", "open", "collections", "indexes", "objects", "count", "counts"]):
        return "count"
    return "none"


def grafana_unit(base_unit: str, kind: str) -> str:
    if kind == "counter":
        return {"bytes": "Bps", "requests": "reqps", "ops": "ops", "count": "ops", "percent": "percent", "µs": "µs", "ms": "ms", "s": "s", "sectors": "short", "none": "ops"}.get(base_unit, "ops")
    return {"bytes": "bytes", "requests": "short", "ops": "ops", "count": "short", "percent": "percent", "µs": "µs", "ms": "ms", "s": "s", "sectors": "short", "none": "none"}.get(base_unit, "none")


def unit_title(base_unit: str, kind: str) -> str:
    if kind == "counter":
        return {"bytes": "Bytes/sec", "requests": "Requests/sec", "ops": "Ops/sec", "count": "Count/sec", "percent": "Percent", "µs": "Microseconds/sec", "ms": "Milliseconds/sec", "s": "Seconds/sec", "sectors": "Sectors/sec", "none": "Rate"}.get(base_unit, "Rate")
    return {"bytes": "Bytes", "requests": "Requests", "ops": "Ops", "count": "Count", "percent": "Percent", "µs": "Microseconds", "ms": "Milliseconds", "s": "Seconds", "sectors": "Sectors", "none": "Values"}.get(base_unit, "Values")


def chunked(items: list[str], size: int) -> list[list[str]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def split_metric_group(prefix: str, names: list[str]) -> list[tuple[str | None, list[str]]]:
    names = sorted(names)
    if len(names) <= MAX_NAMES_PER_PANEL:
        return [(None, names)]
    by_token: dict[str, list[str]] = defaultdict(list)
    for name in names:
        token = suffix_after_prefix(name, prefix).split("_")[0]
        by_token[token].append(name)
    if 1 < len(by_token) <= 8:
        candidate = [(humanize(token), sorted(group)) for token, group in sorted(by_token.items())]
        if max(len(group) for _, group in candidate) <= MAX_NAMES_PER_PANEL * 2:
            return candidate
    groups: list[tuple[str | None, list[str]]] = []
    for index, group in enumerate(chunked(names, MAX_NAMES_PER_PANEL), start=1):
        start = humanize(suffix_after_prefix(group[0], prefix).split("_")[0])
        end = humanize(suffix_after_prefix(group[-1], prefix).split("_")[0])
        groups.append((f"Part {index}: {start} → {end}", group))
    return groups


def legend_format_for_metric(name: str, prefix: str, sample_labels: dict[str, set[str]]) -> str:
    keys = (sample_labels.get(name, set()) - IGNORE_LABELS)
    chosen = [key for key in LABEL_PRIORITY if key in keys][:2]
    base = suffix_after_prefix(name, prefix)
    if not chosen:
        return base
    return " ".join([base] + [f"{{{{{key}}}}}" for key in chosen])


def thresholds_for(title: str, unit: str) -> dict | None:
    low = title.lower()
    if title == "MongoDB Up":
        return {"mode": "absolute", "steps": [{"color": "red", "value": None}, {"color": "green", "value": 1}]}
    if "cache used %" in low or unit == "percent":
        return {"mode": "absolute", "steps": [{"color": "green", "value": None}, {"color": "yellow", "value": 80}, {"color": "red", "value": 95}]}
    if "scrape time" in low:
        return {"mode": "absolute", "steps": [{"color": "green", "value": None}, {"color": "yellow", "value": 500}, {"color": "red", "value": 2000}]}
    return None


def ref_id(index: int) -> str:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    value = ""
    number = index
    while True:
        value = alphabet[number % 26] + value
        number = number // 26 - 1
        if number < 0:
            return value


def target(expr: str, index: int, legend: str) -> dict:
    return {"expr": expr, "legendFormat": legend, "refId": ref_id(index)}


def row_panel(panel_id: int, title: str, y: int) -> dict:
    return {"collapsed": False, "gridPos": {"h": 1, "w": 24, "x": 0, "y": y}, "id": panel_id, "panels": [], "title": title, "type": "row"}


def panel_defaults(unit: str, title: str) -> dict:
    defaults = {"unit": unit}
    thresholds = thresholds_for(title, unit)
    if thresholds:
        defaults["thresholds"] = thresholds
    return defaults


def stat_panel(panel_id: int, title: str, description: str, expr: str, x: int, y: int, unit: str = "none") -> dict:
    return {"datasource": PROM_DS, "description": description, "fieldConfig": {"defaults": panel_defaults(unit, title), "overrides": []}, "gridPos": {"h": 4, "w": 4, "x": x, "y": y}, "id": panel_id, "options": {"colorMode": "value", "graphMode": "none", "justifyMode": "auto", "orientation": "auto", "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}, "textMode": "auto"}, "targets": [target(expr, 0, title)], "title": title, "type": "stat"}


def timeseries_panel(panel_id: int, title: str, description: str, targets: list[dict], x: int, y: int, unit: str = "none", width: int = 8, height: int = 8) -> dict:
    return {"datasource": PROM_DS, "description": description, "fieldConfig": {"defaults": panel_defaults(unit, title), "overrides": []}, "gridPos": {"h": height, "w": width, "x": x, "y": y}, "id": panel_id, "options": {"legend": {"displayMode": "list", "placement": "bottom"}, "tooltip": {"mode": "multi", "sort": "desc"}}, "targets": targets, "title": title, "type": "timeseries"}


def bargauge_panel(panel_id: int, title: str, description: str, expr: str, x: int, y: int, unit: str = "none") -> dict:
    return {"datasource": PROM_DS, "description": description, "fieldConfig": {"defaults": panel_defaults(unit, title), "overrides": []}, "gridPos": {"h": 8, "w": 8, "x": x, "y": y}, "id": panel_id, "options": {"displayMode": "basic", "orientation": "horizontal", "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}, "showUnfilled": True}, "targets": [target(expr, 0, "{{database}}")], "title": title, "type": "bargauge"}


def build_metadata() -> dict[str, dict]:
    family_types, family_help, sample_family, sample_labels = parse_result_snapshot()
    metadata: dict[str, dict] = {}
    for name, family in sample_family.items():
        row, prefix = bucket_for(name)
        help_text = family_help.get(family, "")
        kind = explicit_kind(name, family, family_types)
        base_unit = infer_base_unit(name, help_text)
        metadata[name] = {"family": family, "help": help_text, "kind": kind, "row": row, "prefix": prefix, "base_unit": base_unit, "panel_unit": grafana_unit(base_unit, kind), "labels": sorted(sample_labels.get(name, set()))}
    return metadata


def panel_description(row: str, prefix: str, kind: str, base_unit: str, names: list[str], metadata: dict[str, dict], subgroup: str | None = None) -> str:
    mode = "per-second rates" if kind == "counter" else "current values"
    examples = ", ".join(f"`{suffix_after_prefix(name, prefix)}`" for name in names[:4])
    unit_note = unit_title(base_unit, kind)
    subgroup_note = f" Subgroup: {subgroup}." if subgroup else ""
    return f"{mode.capitalize()} for {len(names)} metric sample(s) in the {row} / {title_for(prefix)} category. Only {unit_note.lower()} metrics are grouped here to avoid mixing units.{subgroup_note} Example metrics: {examples}."


def overview_panels(panel_id: int, y: int) -> tuple[list[dict], int, int]:
    panels = [row_panel(panel_id, "Overview", y)]
    panel_id += 1
    y += 1
    panels.extend([
        stat_panel(panel_id, "MongoDB Up", "Shows whether the selected MongoDB exporter target is reachable and returning metrics. Green means the scrape target is up.", 'max(mongodb_up{instance="$instance"})', 0, y),
        stat_panel(panel_id + 1, "Uptime", "Server uptime in seconds for the selected MongoDB instance.", 'max(mongodb_ss_uptime{instance="$instance"})', 4, y, "s"),
        stat_panel(panel_id + 2, "Current Connections", "Current number of active client connections reported by serverStatus.connections.current.", 'max(mongodb_ss_connections{instance="$instance",conn_type="current"})', 8, y, "short"),
        stat_panel(panel_id + 3, "Resident Memory", "Resident memory currently used by the mongod process. The exporter exposes this in MiB, so the query converts it to bytes.", 'max(mongodb_ss_mem_resident{instance="$instance"}) * 1024 * 1024', 12, y, "bytes"),
        stat_panel(panel_id + 4, "WT Cache Used %", "Percent of the WiredTiger cache currently in use. Warning at 80% and critical at 95%.", '100 * max(mongodb_ss_wt_cache_bytes_currently_in_the_cache{instance="$instance"}) / clamp_min(max(mongodb_ss_wt_cache_maximum_bytes_configured{instance="$instance"}), 1)', 16, y, "percent"),
        stat_panel(panel_id + 5, "Max Scrape Time", "Maximum collector scrape duration in milliseconds across exporter collectors for the selected instance.", 'max(collector_scrape_time_ms{instance="$instance"})', 20, y, "ms"),
    ])
    panel_id += 6
    y += 4
    panels.extend([
        timeseries_panel(panel_id, "Opcounters - Ops/sec", "Per-second MongoDB operation rate split by legacy operation type.", [target('sum by (legacy_op_type) (rate(mongodb_ss_opcounters{instance="$instance"}[$__rate_interval]))', 0, "{{legacy_op_type}}")], 0, y, "ops"),
        timeseries_panel(panel_id + 1, "Network Throughput - Bytes/sec", "Ingress and egress network throughput in bytes per second for the selected instance.", [target('rate(mongodb_ss_network_bytesIn{instance="$instance"}[$__rate_interval])', 0, "bytes in"), target('rate(mongodb_ss_network_bytesOut{instance="$instance"}[$__rate_interval])', 1, "bytes out")], 8, y, "Bps"),
        timeseries_panel(panel_id + 2, "Request Rate - Requests/sec", "Per-second request rate as reported by serverStatus.network.numRequests.", [target('rate(mongodb_ss_network_numRequests{instance="$instance"}[$__rate_interval])', 0, "requests/sec")], 16, y, "reqps"),
    ])
    panel_id += 3
    y += 8
    panels.extend([
        bargauge_panel(panel_id, "Database Total Size", "Current total database size by database for the selected instance.", 'sum by (database) (mongodb_dbstats_totalSize{instance="$instance"})', 0, y, "bytes"),
        timeseries_panel(panel_id + 1, "Slow Query Rate - Queries/sec", "Per-second slow query rate grouped by database.", [target('sum by (database) (rate(mongodb_profile_slow_query_count{instance="$instance"}[$__rate_interval]))', 0, "{{database}}")], 8, y, "ops"),
        timeseries_panel(panel_id + 2, "Average Operation Latency - ms", "Average read, write, command, and transaction latency derived from opLatencies cumulative counters.", [target('rate(mongodb_ss_opLatencies_latency{instance="$instance",op_type="commands"}[$__rate_interval]) / clamp_min(rate(mongodb_ss_opLatencies_ops{instance="$instance",op_type="commands"}[$__rate_interval]), 1) / 1000', 0, "commands"), target('rate(mongodb_ss_opLatencies_latency{instance="$instance",op_type="reads"}[$__rate_interval]) / clamp_min(rate(mongodb_ss_opLatencies_ops{instance="$instance",op_type="reads"}[$__rate_interval]), 1) / 1000', 1, "reads"), target('rate(mongodb_ss_opLatencies_latency{instance="$instance",op_type="writes"}[$__rate_interval]) / clamp_min(rate(mongodb_ss_opLatencies_ops{instance="$instance",op_type="writes"}[$__rate_interval]), 1) / 1000', 2, "writes"), target('rate(mongodb_ss_opLatencies_latency{instance="$instance",op_type="transactions"}[$__rate_interval]) / clamp_min(rate(mongodb_ss_opLatencies_ops{instance="$instance",op_type="transactions"}[$__rate_interval]), 1) / 1000', 3, "transactions")], 16, y, "ms"),
    ])
    return panels, panel_id + 3, y + 9


def grouped_panel_specs(metadata: dict[str, dict]) -> dict[str, list[dict]]:
    grouped: dict[tuple[str, str, str, str], list[str]] = defaultdict(list)
    sample_labels = {name: set(meta["labels"]) for name, meta in metadata.items()}
    for name, meta in metadata.items():
        if name == "mongodb_ss_connections":
            continue
        grouped[(meta["row"], meta["prefix"], meta["kind"], meta["base_unit"])].append(name)
    row_specs: dict[str, list[dict]] = defaultdict(list)
    row_specs["Server Status"].append({"title": "Connections - Current Values", "description": "Current connection gauges from serverStatus.connections excluding the cumulative totalCreated label value.", "targets": [target('mongodb_ss_connections{instance="$instance",conn_type!="totalCreated"}', 0, "{{conn_type}}")], "unit": "short"})
    row_specs["Server Status"].append({"title": "Connections - Created/sec", "description": "Per-second rate of new connections created from serverStatus.connections.totalCreated.", "targets": [target('rate(mongodb_ss_connections{instance="$instance",conn_type="totalCreated"}[$__rate_interval])', 0, "created/sec")], "unit": "ops"})
    for (row, prefix, kind, base_unit), names in sorted(grouped.items()):
        for subgroup, group_names in split_metric_group(prefix, names):
            targets: list[dict] = []
            for index, name in enumerate(group_names):
                expr = f'rate({name}{{instance="$instance"}}[{RATE_INTERVAL}])' if kind == "counter" else f'{name}{{instance="$instance"}}'
                targets.append(target(expr, index, legend_format_for_metric(name, prefix, sample_labels)))
            title = f"{title_for(prefix)} - {unit_title(base_unit, kind)}"
            if subgroup:
                title = f"{title} - {subgroup}"
            row_specs[row].append({"title": title, "description": panel_description(row, prefix, kind, base_unit, group_names, metadata, subgroup), "targets": targets, "unit": grafana_unit(base_unit, kind), "metrics": group_names})
    return row_specs


def build_dashboard() -> tuple[dict, dict]:
    metadata = build_metadata()
    panels, panel_id, y = overview_panels(1, 0)
    row_specs = grouped_panel_specs(metadata)
    for row in ROW_ORDER[1:]:
        specs = row_specs.get(row, [])
        if not specs:
            continue
        panels.append(row_panel(panel_id, row, y))
        panel_id += 1
        y += 1
        x = 0
        for spec in specs:
            panels.append(timeseries_panel(panel_id, spec["title"], spec["description"], spec["targets"], x, y, spec["unit"]))
            panel_id += 1
            x += 8
            if x >= 24:
                x = 0
                y += 8
        if x:
            y += 8
        y += 1
    progress = {"generated_at": datetime.now(UTC).isoformat(), "source": str(SOURCE), "sample_count": len(metadata), "row_panel_counts": {row: len(row_specs.get(row, [])) for row in ROW_ORDER[1:]}, "unit_counts": dict(Counter(meta["panel_unit"] for meta in metadata.values())), "notes": {"instance_variable": "Single-select instance variable with includeAll disabled.", "mixed_family": "mongodb_ss_connections is split into a gauge panel and a created/sec panel because totalCreated is counter-like.", "query_strategy": "Grouped panels use one Prometheus target per metric name so rate()/instant queries do not collide on identical label sets."}, "rows": [{"title": "Overview", "panel_count": 9}] + [{"title": row, "panel_count": len(row_specs.get(row, [])), "panels": [{"title": spec["title"], "unit": spec["unit"], "metric_count": len(spec.get("metrics", [])), "metrics": spec.get("metrics", []), "description": spec["description"]} for spec in row_specs.get(row, [])]} for row in ROW_ORDER[1:] if row_specs.get(row)]}
    dashboard = {"__inputs": [{"name": "DS_PROMETHEUS", "label": "Prometheus", "description": "", "type": "datasource", "pluginId": "prometheus", "pluginName": "Prometheus"}], "annotations": {"list": [{"builtIn": 1, "datasource": {"type": "grafana", "uid": "-- Grafana --"}, "enable": True, "hide": True, "iconColor": "rgba(0, 211, 255, 1)", "name": "Annotations & Alerts", "type": "dashboard"}]}, "description": f"Generated from {SOURCE}. Uses metric types from mongodb_exporter_result.txt, row-based layout, single-instance selection, and per-panel descriptions.", "editable": True, "graphTooltip": 0, "links": [], "panels": panels, "refresh": "30s", "schemaVersion": 39, "style": "dark", "tags": ["mongodb", "mongodb_exporter", "all-metrics"], "templating": {"list": [{"current": {"selected": False, "text": "", "value": ""}, "datasource": PROM_DS, "definition": "label_values(mongodb_up, instance)", "hide": 0, "includeAll": False, "label": "Instance", "multi": False, "name": "instance", "options": [], "query": {"query": "label_values(mongodb_up, instance)", "refId": "PrometheusVariableQueryEditor-VariableQuery"}, "refresh": 1, "regex": "", "skipUrlSync": False, "sort": 1, "type": "query"}]}, "time": {"from": "now-6h", "to": "now"}, "timezone": "", "title": "MongoDB Exporter - All Metrics", "uid": "mongodb-exporter-all-metrics", "version": 3, "weekStart": ""}
    return dashboard, progress


if __name__ == "__main__":
    dashboard, progress = build_dashboard()
    PROGRESS.write_text(json.dumps(progress, indent=2) + "\n")
    OUTPUT.write_text(json.dumps(dashboard, indent=2) + "\n")
    print(f"Wrote {PROGRESS}")
    print(f"Wrote {OUTPUT}")