from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

SOURCE = Path("Monitoring/mongodb_exporter_result.txt")
OUTPUT = Path("Monitoring/mongodb_exporter_all_metrics_dashboard.json")
PROM_DS = {"type": "prometheus", "uid": "${DS_PROMETHEUS}"}
LEGEND_FORMAT = (
    "{{__name__}} {{collector}} {{database}} {{collection}} {{namespace}} "
    "{{op_type}} {{legacy_op_type}} {{conn_type}} {{csr_type}} {{count_type}} "
    "{{type}} {{state}} {{cmd_name}} {{member_state}} {{mountpoint}}"
)


def metric_names() -> list[str]:
    return sorted(
        {
            line.split()[2]
            for line in SOURCE.read_text().splitlines()
            if line.startswith("# HELP ")
        }
    )


def bucket_for(name: str) -> tuple[str, str]:
    parts = name.split("_")
    if name.startswith("collector_"):
        return "Exporter", "collector"
    if name.startswith("go_"):
        return "Exporter", "_".join(parts[:2])
    if name.startswith("process_"):
        return "Exporter", "process"
    if name in {"mongodb_up", "mongodb_start", "mongodb_end"}:
        return "MongoDB Core", "mongodb_status"
    if name.startswith("mongodb_fcv_"):
        return "MongoDB Core", "mongodb_fcv"
    if name.startswith("mongodb_pbm_"):
        return "MongoDB Core", "mongodb_pbm"
    if name.startswith("mongodb_profile_"):
        return "MongoDB Core", "mongodb_profile"
    if name.startswith("mongodb_dbstats_"):
        return "MongoDB Core", "mongodb_dbstats"
    if name.startswith("mongodb_transportLayerStats_"):
        return "MongoDB Core", "mongodb_transportLayerStats"
    if name.startswith("mongodb_top_"):
        return "MongoDB Top", "_".join(parts[:3])
    if name.startswith("mongodb_sys_"):
        prefix_len = 4 if len(parts) >= 4 and parts[2] in {"netstat", "mounts"} else 3
        return "System Metrics", "_".join(parts[:prefix_len])
    if name.startswith("mongodb_ss_"):
        prefix_len = 4 if len(parts) >= 4 and parts[2] in {"metrics", "wt"} else 3
        return "Server Status", "_".join(parts[:prefix_len])
    return "Other", name


def panel_title(prefix: str) -> str:
    title = prefix.replace("mongodb_", "").replace("go_", "Go ")
    title = title.replace("process", "process").replace("collector", "collector")
    title = title.replace("ss_", "serverStatus ").replace("sys_", "system ")
    title = title.replace("transportLayerStats", "transportLayerStats ")
    return " ".join(title.split("_")).strip().title()


def row_panel(panel_id: int, title: str, y: int) -> dict:
    return {
        "collapsed": False,
        "gridPos": {"h": 1, "w": 24, "x": 0, "y": y},
        "id": panel_id,
        "panels": [],
        "title": title,
        "type": "row",
    }


def timeseries_panel(panel_id: int, title: str, prefix: str, x: int, y: int) -> dict:
    expr = f'{{__name__=~"{re.escape(prefix)}($|_.*)",instance="$instance"}}'
    return {
        "datasource": PROM_DS,
        "fieldConfig": {"defaults": {}, "overrides": []},
        "gridPos": {"h": 8, "w": 12, "x": x, "y": y},
        "id": panel_id,
        "options": {
            "legend": {"displayMode": "list", "placement": "bottom"},
            "tooltip": {"mode": "multi", "sort": "desc"},
        },
        "targets": [{"expr": expr, "legendFormat": LEGEND_FORMAT, "refId": "A"}],
        "title": title,
        "type": "timeseries",
    }


def build_dashboard() -> dict:
    grouped: dict[str, set[str]] = defaultdict(set)
    for name in metric_names():
        category, prefix = bucket_for(name)
        grouped[category].add(prefix)

    order = ["MongoDB Core", "MongoDB Top", "Server Status", "System Metrics", "Exporter", "Other"]
    panels: list[dict] = []
    panel_id, y = 1, 0

    panels.append(row_panel(panel_id, "Metric Explorer", y))
    panel_id, y = panel_id + 1, y + 1
    panels.append(
        {
            "datasource": PROM_DS,
            "fieldConfig": {"defaults": {}, "overrides": []},
            "gridPos": {"h": 8, "w": 24, "x": 0, "y": y},
            "id": panel_id,
            "options": {
                "legend": {"displayMode": "list", "placement": "bottom"},
                "tooltip": {"mode": "multi", "sort": "desc"},
            },
            "targets": [{"expr": '{__name__=~"$metric_regex",instance="$instance"}', "legendFormat": LEGEND_FORMAT, "refId": "A"}],
            "title": "Custom Metric Regex Explorer",
            "type": "timeseries",
        }
    )
    panel_id, y = panel_id + 1, y + 9

    for category in order:
        prefixes = sorted(grouped.get(category, ()))
        if not prefixes:
            continue
        panels.append(row_panel(panel_id, category, y))
        panel_id, y = panel_id + 1, y + 1
        x = 0
        for prefix in prefixes:
            panels.append(timeseries_panel(panel_id, panel_title(prefix), prefix, x, y))
            panel_id += 1
            if x == 0:
                x = 12
            else:
                x = 0
                y += 8
        if x == 12:
            y += 8
        y += 1

    return {
        "__inputs": [
            {
                "name": "DS_PROMETHEUS",
                "label": "Prometheus",
                "description": "",
                "type": "datasource",
                "pluginId": "prometheus",
                "pluginName": "Prometheus",
            }
        ],
        "annotations": {
            "list": [
                {
                    "builtIn": 1,
                    "datasource": {"type": "grafana", "uid": "-- Grafana --"},
                    "enable": True,
                    "hide": True,
                    "iconColor": "rgba(0, 211, 255, 1)",
                    "name": "Annotations & Alerts",
                    "type": "dashboard",
                }
            ]
        },
        "editable": True,
        "graphTooltip": 0,
        "panels": panels,
        "refresh": "30s",
        "schemaVersion": 39,
        "style": "dark",
        "tags": ["mongodb", "mongodb_exporter", "all-metrics"],
        "templating": {
            "list": [
                {"current": {"selected": False, "text": "", "value": ""}, "datasource": PROM_DS, "definition": "label_values(mongodb_up, instance)", "hide": 0, "includeAll": False, "label": "Instance", "multi": False, "name": "instance", "options": [], "query": {"query": "label_values(mongodb_up, instance)", "refId": "PrometheusVariableQueryEditor-VariableQuery"}, "refresh": 1, "regex": "", "skipUrlSync": False, "sort": 1, "type": "query"},
                {"current": {"selected": False, "text": "mongodb_.*|go_.*|process_.*|collector_.*", "value": "mongodb_.*|go_.*|process_.*|collector_.*"}, "hide": 0, "label": "Metric regex", "name": "metric_regex", "options": [], "query": "mongodb_.*|go_.*|process_.*|collector_.*", "skipUrlSync": False, "type": "textbox"},
            ]
        },
        "time": {"from": "now-6h", "to": "now"},
        "title": "MongoDB Exporter - All Metrics Explorer",
        "uid": "mongo-exporter-all-metrics",
        "version": 1,
    }


if __name__ == "__main__":
    OUTPUT.write_text(json.dumps(build_dashboard(), indent=2) + "\n")
    print(f"Wrote {OUTPUT}")