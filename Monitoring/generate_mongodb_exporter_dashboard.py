from __future__ import annotations

import json
import re
import time
import urllib.parse
from collections import Counter, defaultdict
from pathlib import Path
from urllib.request import urlopen

EXPORTER_ENDPOINT = "http://pgpractice:9216/metrics"
PROMETHEUS_API = "http://localhost:9091/api/v1"
PROM_INSTANCE = "pgpractice:9216"
OUTPUT = Path("Monitoring/mongodb_exporter_all_metrics_dashboard.json")
PROM_DS = {"type": "prometheus", "uid": "${DS_PROMETHEUS}"}
LEGEND_FORMAT = (
    "{{__name__}} {{collector}} {{database}} {{collection}} {{namespace}} "
    "{{op_type}} {{legacy_op_type}} {{conn_type}} {{csr_type}} {{count_type}} "
    "{{member_state}} {{state}} {{type}} {{cmd_name}} {{mountpoint}} "
    "{{device}} {{cpu}} {{mode}}"
)
ROW_ORDER = [
    "MongoDB Core",
    "Database Stats",
    "Top Operations",
    "Server Status",
    "Server Status Metrics",
    "WiredTiger",
    "System Metrics",
    "Exporter Runtime",
    "Other",
]
RATE_INTERVAL = "$__rate_interval"
INFER_WINDOW_SECONDS = 30 * 60
INFER_STEP_SECONDS = 5 * 60
INFER_BATCH_SIZE = 50


def exporter_lines() -> list[str]:
    text = urlopen(EXPORTER_ENDPOINT, timeout=20).read().decode("utf-8", "replace")
    return text.splitlines()


def parse_exporter_snapshot() -> tuple[dict[str, str], list[str]]:
    family_types: dict[str, str] = {}
    sample_names: set[str] = set()
    for line in exporter_lines():
        if line.startswith("# TYPE "):
            _, _, family, kind = line.split()
            family_types[family] = kind
        elif line and not line.startswith("#"):
            match = re.match(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)", line)
            if match:
                sample_names.add(match.group(1))
    return family_types, sorted(sample_names)


def explicit_kind(name: str, family_types: dict[str, str]) -> str | None:
    metric_type = family_types.get(name)
    if metric_type is None and (name.endswith("_sum") or name.endswith("_count")):
        metric_type = family_types.get(name[:-4])
    if metric_type == "counter":
        return "counter"
    if metric_type == "gauge":
        return "gauge"
    if metric_type == "summary":
        return "counter" if (name.endswith("_sum") or name.endswith("_count")) else "gauge"
    return None


def counter_hint(name: str) -> bool:
    pattern = (
        r"(?:_total)$|(?:asserts|counter|counters|opcounters|requests|scanned|"
        r"returned|inserted|updated|deleted|removed|evict|miss|hit|bytesIn|bytesOut|"
        r"numRequests|txn|transaction|committed|aborted|acquireCount|totalTime|"
        r"latenc|created|rollback|retried|checkpoint|reconciliation|spill|written|"
        r"read_into|read_from|conflicts)"
    )
    return bool(re.search(pattern, name, re.IGNORECASE))


def gauge_hint(name: str) -> bool:
    pattern = (
        r"(?:^mongodb_(?:start|end)$|_start$|_end$|localTime$|timestamp|average|"
        r"current|active|available|resident|configured|max(?:imum)?|minimum|percent|"
        r"ratio|size|capacity|memory|uptime|threads|connections|queues|pressure|"
        r"objects|collections|indexes|cached|idle|open_cursor|sessionsCount|version|"
        r"status|open_fds|goroutines|seconds$|bytes$|fsUsedSize)"
    )
    return bool(re.search(pattern, name, re.IGNORECASE))


def query_prometheus_range(query: str, start: int, end: int, step: int) -> dict:
    params = urllib.parse.urlencode({"query": query, "start": start, "end": end, "step": step})
    with urlopen(f"{PROMETHEUS_API}/query_range?{params}", timeout=60) as response:
        return json.load(response)


def infer_untyped_kinds(sample_names: list[str], family_types: dict[str, str]) -> dict[str, str]:
    kinds = {name: explicit_kind(name, family_types) for name in sample_names}
    pending = [name for name, kind in kinds.items() if kind is None]
    start = int(time.time()) - INFER_WINDOW_SECONDS
    end = int(time.time())
    stats: dict[str, Counter] = defaultdict(Counter)

    for index in range(0, len(pending), INFER_BATCH_SIZE):
        batch = pending[index : index + INFER_BATCH_SIZE]
        regex = "|".join(re.escape(name) for name in batch)
        query = f'{{instance="{PROM_INSTANCE}",__name__=~"{regex}"}}'
        payload = query_prometheus_range(query, start, end, INFER_STEP_SECONDS)
        if payload.get("status") != "success":
            raise RuntimeError(f"Prometheus query_range failed: {payload}")
        for series in payload["data"]["result"]:
            name = series["metric"]["__name__"]
            values = [float(value) for _, value in series["values"] if value not in {"NaN", "Inf", "-Inf"}]
            previous = None
            for current in values:
                if previous is None:
                    previous = current
                    if current != 0:
                        stats[name]["non_zero"] += 1
                    continue
                tolerance = max(1.0, abs(previous)) * 1e-9
                delta = current - previous
                if delta > tolerance:
                    stats[name]["positive"] += 1
                    stats[name]["changes"] += 1
                elif delta < -tolerance:
                    stats[name]["changes"] += 1
                    if previous > 0 and current <= previous * 0.2:
                        stats[name]["reset"] += 1
                    else:
                        stats[name]["negative"] += 1
                if current != 0:
                    stats[name]["non_zero"] += 1
                previous = current

    for name in pending:
        current = stats[name]
        positive = current["positive"]
        negative = current["negative"]
        resets = current["reset"]
        changes = current["changes"]
        if gauge_hint(name) and not counter_hint(name):
            kinds[name] = "gauge"
        elif positive > 0 and negative == 0:
            kinds[name] = "counter"
        elif positive > 0 and negative > 0 and negative <= resets and (negative <= 1 or negative * 5 <= positive):
            kinds[name] = "counter"
        elif negative > 0 and negative > resets:
            kinds[name] = "gauge"
        elif changes == 0:
            kinds[name] = "gauge"
        elif counter_hint(name) and negative <= resets:
            kinds[name] = "counter"
        else:
            kinds[name] = "gauge"

    return {name: kinds[name] or "gauge" for name in sample_names}


def humanize(text: str) -> str:
    words = []
    for token in text.split("_"):
        if not token:
            continue
        words.append(token if any(char.isupper() for char in token[1:]) else token.capitalize())
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
    if name.startswith("go_"):
        return "Exporter Runtime", "_".join(parts[:2])
    if name.startswith("process_"):
        return "Exporter Runtime", "_".join(parts[:2])
    if name.startswith("mongodb_transportLayerStats_"):
        return "MongoDB Core", "mongodb_transportLayerStats"
    if name.startswith("mongodb_profile_"):
        return "MongoDB Core", "mongodb_profile"
    if name.startswith("mongodb_fcv_"):
        return "MongoDB Core", "mongodb_fcv"
    if name.startswith("mongodb_pbm_"):
        return "MongoDB Core", "mongodb_pbm"
    if name in {"mongodb_up", "mongodb_start", "mongodb_end"}:
        return "MongoDB Core", "mongodb_status"
    if name.startswith("mongodb_"):
        return "MongoDB Core", "_".join(parts[:2])
    return "Other", "_".join(parts[:2])


def title_for(prefix: str) -> str:
    direct = {
        "mongodb_status": "Status",
        "mongodb_dbstats": "Database Stats",
        "mongodb_transportLayerStats": "Transport Layer Stats",
        "collector_scrape": "Collector Scrape",
    }
    if prefix in direct:
        return direct[prefix]
    markers = (
        "mongodb_ss_metrics_",
        "mongodb_ss_wt_",
        "mongodb_ss_",
        "mongodb_sys_",
        "mongodb_top_",
        "mongodb_",
        "go_",
        "process_",
    )
    for marker in markers:
        if prefix.startswith(marker):
            return humanize(prefix[len(marker) :])
    return humanize(prefix)


def ref_id(index: int) -> str:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    value = ""
    number = index
    while True:
        value = alphabet[number % 26] + value
        number = number // 26 - 1
        if number < 0:
            return value


def target(expr: str, index: int, legend: str = LEGEND_FORMAT) -> dict:
    return {"expr": expr, "legendFormat": legend, "refId": ref_id(index)}


def row_panel(panel_id: int, title: str, y: int) -> dict:
    return {"collapsed": False, "gridPos": {"h": 1, "w": 24, "x": 0, "y": y}, "id": panel_id, "panels": [], "title": title, "type": "row"}


def stat_panel(panel_id: int, title: str, expr: str, x: int, y: int, unit: str = "none") -> dict:
    return {
        "datasource": PROM_DS,
        "fieldConfig": {"defaults": {"unit": unit}, "overrides": []},
        "gridPos": {"h": 4, "w": 4, "x": x, "y": y},
        "id": panel_id,
        "options": {"colorMode": "value", "graphMode": "none", "justifyMode": "auto", "orientation": "auto", "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}, "textMode": "auto"},
        "targets": [target(expr, 0, title)],
        "title": title,
        "type": "stat",
    }


def timeseries_panel(panel_id: int, title: str, targets: list[dict], x: int, y: int, width: int = 8, height: int = 8) -> dict:
    return {
        "datasource": PROM_DS,
        "fieldConfig": {"defaults": {}, "overrides": []},
        "gridPos": {"h": height, "w": width, "x": x, "y": y},
        "id": panel_id,
        "options": {"legend": {"displayMode": "list", "placement": "bottom"}, "tooltip": {"mode": "multi", "sort": "desc"}},
        "targets": targets,
        "title": title,
        "type": "timeseries",
    }


def bargauge_panel(panel_id: int, title: str, expr: str, x: int, y: int, unit: str = "none") -> dict:
    return {
        "datasource": PROM_DS,
        "fieldConfig": {"defaults": {"unit": unit}, "overrides": []},
        "gridPos": {"h": 8, "w": 8, "x": x, "y": y},
        "id": panel_id,
        "options": {"displayMode": "basic", "orientation": "horizontal", "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}, "showUnfilled": True},
        "targets": [target(expr, 0, "{{database}}")],
        "title": title,
        "type": "bargauge",
    }


def chunked(items: list[str], size: int) -> list[list[str]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def regex_for(names: list[str]) -> str:
    return "|".join(re.escape(name) for name in names)


def gauge_targets(names: list[str]) -> list[dict]:
    return [target(f'{{__name__=~"{regex_for(batch)}",instance="$instance"}}', index) for index, batch in enumerate(chunked(names, 40))]


def counter_targets(names: list[str]) -> list[dict]:
    return [target(f'rate({{__name__=~"{regex_for(batch)}",instance="$instance"}}[{RATE_INTERVAL}])', index) for index, batch in enumerate(chunked(names, 40))]


def overview_panels(panel_id: int, y: int) -> tuple[list[dict], int, int]:
    panels = [row_panel(panel_id, "Overview", y)]
    panel_id += 1
    y += 1
    panels.extend(
        [
            stat_panel(panel_id, "MongoDB Up", 'max(mongodb_up{instance="$instance"})', 0, y),
            stat_panel(panel_id + 1, "Uptime", 'max(mongodb_ss_uptime{instance="$instance"})', 4, y, "s"),
            stat_panel(panel_id + 2, "Current Connections", 'max(mongodb_ss_connections{instance="$instance",conn_type="current"})', 8, y),
            stat_panel(panel_id + 3, "Resident Memory", 'max(mongodb_ss_mem_resident{instance="$instance"}) * 1024 * 1024', 12, y, "bytes"),
            stat_panel(panel_id + 4, "WT Cache Used %", '100 * max(mongodb_ss_wt_cache_bytes_currently_in_the_cache{instance="$instance"}) / clamp_min(max(mongodb_ss_wt_cache_maximum_bytes_configured{instance="$instance"}), 1)', 16, y, "percent"),
            stat_panel(panel_id + 5, "Max Scrape Time", 'max(collector_scrape_time_ms{instance="$instance"})', 20, y, "ms"),
        ]
    )
    panel_id += 6
    y += 4
    panels.extend(
        [
            timeseries_panel(panel_id, "Opcounters / sec", [target('sum by (legacy_op_type) (rate(mongodb_ss_opcounters{instance="$instance"}[$__rate_interval]))', 0, "{{legacy_op_type}}")], 0, y),
            timeseries_panel(panel_id + 1, "Network & Requests", [target('rate(mongodb_ss_network_bytesIn{instance="$instance"}[$__rate_interval])', 0, "bytes in"), target('rate(mongodb_ss_network_bytesOut{instance="$instance"}[$__rate_interval])', 1, "bytes out"), target('rate(mongodb_ss_network_numRequests{instance="$instance"}[$__rate_interval])', 2, "requests/sec")], 8, y),
            timeseries_panel(panel_id + 2, "Average Operation Latency", [target('rate(mongodb_ss_opLatencies_latency{instance="$instance",op_type="commands"}[$__rate_interval]) / clamp_min(rate(mongodb_ss_opLatencies_ops{instance="$instance",op_type="commands"}[$__rate_interval]), 1)', 0, "commands"), target('rate(mongodb_ss_opLatencies_latency{instance="$instance",op_type="reads"}[$__rate_interval]) / clamp_min(rate(mongodb_ss_opLatencies_ops{instance="$instance",op_type="reads"}[$__rate_interval]), 1)', 1, "reads"), target('rate(mongodb_ss_opLatencies_latency{instance="$instance",op_type="writes"}[$__rate_interval]) / clamp_min(rate(mongodb_ss_opLatencies_ops{instance="$instance",op_type="writes"}[$__rate_interval]), 1)', 2, "writes"), target('rate(mongodb_ss_opLatencies_latency{instance="$instance",op_type="transactions"}[$__rate_interval]) / clamp_min(rate(mongodb_ss_opLatencies_ops{instance="$instance",op_type="transactions"}[$__rate_interval]), 1)', 3, "transactions")], 16, y),
        ]
    )
    panel_id += 3
    y += 8
    panels.extend(
        [
            bargauge_panel(panel_id, "Database Total Size", 'sum by (database) (mongodb_dbstats_totalSize{instance="$instance"})', 0, y, "bytes"),
            timeseries_panel(panel_id + 1, "Slow Query Rate", [target('sum by (database) (rate(mongodb_profile_slow_query_count{instance="$instance"}[$__rate_interval]))', 0, "{{database}}")], 8, y),
            timeseries_panel(panel_id + 2, "Cursors & Lock Queue", [target('mongodb_ss_metrics_cursor_open{instance="$instance"}', 0, "cursor {{csr_type}}"), target('mongodb_ss_globalLock_currentQueue{instance="$instance"}', 1, "queue {{count_type}}")], 16, y),
        ]
    )
    return panels, panel_id + 3, y + 9


def grouped_panels(sample_names: list[str], kinds: dict[str, str], panel_id: int, y: int) -> tuple[list[dict], int, int]:
    grouped: dict[str, dict[str, list[str]]] = defaultdict(lambda: {"gauge": [], "counter": []})
    for name in sample_names:
        row, prefix = bucket_for(name)
        grouped[f"{row}::{prefix}"][kinds[name]].append(name)

    panels: list[dict] = []
    by_row: dict[str, list[tuple[str, list[str], list[str]]]] = defaultdict(list)
    for key, value in grouped.items():
        row, prefix = key.split("::", 1)
        by_row[row].append((prefix, sorted(value["gauge"]), sorted(value["counter"])))

    for row_name in ROW_ORDER:
        entries = sorted(by_row.get(row_name, []), key=lambda item: item[0])
        if not entries:
            continue
        panels.append(row_panel(panel_id, row_name, y))
        panel_id += 1
        y += 1
        x = 0
        for prefix, gauge_names, counter_names in entries:
            base_title = title_for(prefix)
            panel_defs: list[tuple[str, list[dict]]] = []
            if prefix == "mongodb_ss_connections":
                panel_defs.append((f"{base_title} - Gauges", [target('mongodb_ss_connections{instance="$instance",conn_type!="totalCreated"}', 0)]))
                panel_defs.append((f"{base_title} - Rate", [target('rate(mongodb_ss_connections{instance="$instance",conn_type="totalCreated"}[$__rate_interval])', 0)]))
            else:
                if gauge_names:
                    panel_defs.append((base_title if not counter_names else f"{base_title} - Gauges", gauge_targets(gauge_names)))
                if counter_names:
                    panel_defs.append((f"{base_title} - Rate", counter_targets(counter_names)))
            for title, targets in panel_defs:
                panels.append(timeseries_panel(panel_id, title, targets, x, y))
                panel_id += 1
                x += 8
                if x >= 24:
                    x = 0
                    y += 8
        if x:
            y += 8
        y += 1
    return panels, panel_id, y


def build_dashboard() -> dict:
    family_types, sample_names = parse_exporter_snapshot()
    kinds = infer_untyped_kinds(sample_names, family_types)
    panels, panel_id, y = overview_panels(1, 0)
    extra_panels, _, _ = grouped_panels(sample_names, kinds, panel_id, y)
    panels.extend(extra_panels)
    return {
        "__inputs": [{"name": "DS_PROMETHEUS", "label": "Prometheus", "description": "", "type": "datasource", "pluginId": "prometheus", "pluginName": "Prometheus"}],
        "annotations": {"list": [{"builtIn": 1, "datasource": {"type": "grafana", "uid": "-- Grafana --"}, "enable": True, "hide": True, "iconColor": "rgba(0, 211, 255, 1)", "name": "Annotations & Alerts", "type": "dashboard"}]},
        "description": f"Generated from {EXPORTER_ENDPOINT}; untyped metrics inferred from Prometheus 30-minute history via {PROMETHEUS_API}",
        "editable": True,
        "graphTooltip": 0,
        "links": [],
        "panels": panels,
        "refresh": "30s",
        "schemaVersion": 39,
        "style": "dark",
        "tags": ["mongodb", "mongodb_exporter", "all-metrics"],
        "templating": {"list": [{"current": {"selected": False, "text": "", "value": ""}, "datasource": PROM_DS, "definition": "label_values(mongodb_up, instance)", "hide": 0, "includeAll": False, "label": "Instance", "multi": False, "name": "instance", "options": [], "query": {"query": "label_values(mongodb_up, instance)", "refId": "PrometheusVariableQueryEditor-VariableQuery"}, "refresh": 1, "regex": "", "skipUrlSync": False, "sort": 1, "type": "query"}]},
        "time": {"from": "now-1h", "to": "now"},
        "timezone": "",
        "title": "MongoDB Exporter - All Metrics",
        "uid": "mongodb-exporter-all-metrics",
        "version": 2,
        "weekStart": "",
    }


if __name__ == "__main__":
    OUTPUT.write_text(json.dumps(build_dashboard(), indent=2) + "\n")
    print(f"Wrote {OUTPUT}")