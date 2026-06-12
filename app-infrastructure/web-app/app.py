"""
NetVigil — Вебдодаток з використанням LLM для аналізу мережевого трафіку.
Дипломна робота Курмаша Р.В., 41-К, 2026
Спеціальність 125 Кібербезпека та захист інформації
"""

import os
import io
import json
import time
import struct
import hashlib
import asyncio
from datetime import datetime, timedelta

import httpx
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__, static_folder="static", static_url_path="")
CORS(app)

# ---------------------------------------------------------------------------
# Конфігурація
# ---------------------------------------------------------------------------
LOKI_URL = os.getenv("LOKI_URL", "http://loki:3100")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://ollama:11434")
PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://prometheus:9090")
ALERTMANAGER_URL = os.getenv("ALERTMANAGER_URL", "http://alertmanager:9093")
LLM_MODEL = os.getenv("LLM_MODEL", "llama3")
MAX_PCAP_SIZE = 10 * 1024 * 1024  # 10 MB

# ---------------------------------------------------------------------------
# Допоміжні функції
# ---------------------------------------------------------------------------

def _loki_query(query: str, limit: int = 100, start: str = None, end: str = None):
    """Виконує запит до Loki API."""
    params = {"query": query, "limit": limit, "direction": "backward"}
    if start:
        params["start"] = start
    if end:
        params["end"] = end
    try:
        resp = httpx.get(f"{LOKI_URL}/loki/api/v1/query_range", params=params, timeout=10.0)
        if resp.status_code == 200:
            return resp.json().get("data", {}).get("result", [])
    except Exception as e:
        print(f"[Loki] Error: {e}")
    return []


def _prometheus_query(query: str):
    """Виконує PromQL-запит до Prometheus."""
    try:
        resp = httpx.get(
            f"{PROMETHEUS_URL}/api/v1/query",
            params={"query": query},
            timeout=10.0,
        )
        if resp.status_code == 200:
            return resp.json().get("data", {}).get("result", [])
    except Exception as e:
        print(f"[Prometheus] Error: {e}")
    return []


def _prometheus_query_range(query: str, start: float, end: float, step: str = "60s"):
    """Виконує PromQL range query."""
    try:
        resp = httpx.get(
            f"{PROMETHEUS_URL}/api/v1/query_range",
            params={"query": query, "start": start, "end": end, "step": step},
            timeout=10.0,
        )
        if resp.status_code == 200:
            return resp.json().get("data", {}).get("result", [])
    except Exception as e:
        print(f"[Prometheus] Range query error: {e}")
    return []


def _ollama_generate(prompt: str, format_json: bool = True):
    """Виконує інференс через Ollama LLM."""
    payload = {
        "model": LLM_MODEL,
        "prompt": prompt,
        "stream": False,
    }
    if format_json:
        payload["format"] = "json"
    try:
        resp = httpx.post(
            f"{OLLAMA_URL}/api/generate", json=payload, timeout=120.0
        )
        if resp.status_code == 200:
            raw = resp.json().get("response", "")
            if format_json:
                return json.loads(raw)
            return {"text": raw}
    except json.JSONDecodeError:
        return {"error": "LLM returned invalid JSON", "raw": raw}
    except Exception as e:
        print(f"[Ollama] Error: {e}")
    return None


def _get_alertmanager_alerts():
    """Отримує список алертів з Alertmanager."""
    try:
        resp = httpx.get(f"{ALERTMANAGER_URL}/api/v2/alerts", timeout=10.0)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        print(f"[Alertmanager] Error: {e}")
    return []


def _parse_pcap_bytes(data: bytes):
    """
    Мінімальний парсер PCAP (libpcap формат).
    Повертає список пакетів з базовою інформацією.
    """
    packets = []
    if len(data) < 24:
        return packets

    # Global header
    magic = struct.unpack("<I", data[:4])[0]
    if magic == 0xA1B2C3D4:
        endian = "<"
    elif magic == 0xD4C3B2A1:
        endian = ">"
    else:
        return packets

    version_major, version_minor, _, _, snaplen, network = struct.unpack(
        f"{endian}HHIIII", data[4:24]
    )

    offset = 24
    pkt_num = 0

    while offset + 16 <= len(data) and pkt_num < 500:
        ts_sec, ts_usec, incl_len, orig_len = struct.unpack(
            f"{endian}IIII", data[offset : offset + 16]
        )
        offset += 16

        if offset + incl_len > len(data):
            break

        pkt_data = data[offset : offset + incl_len]
        offset += incl_len
        pkt_num += 1

        pkt_info = {
            "num": pkt_num,
            "timestamp": datetime.utcfromtimestamp(ts_sec + ts_usec / 1e6).isoformat(),
            "length": orig_len,
            "protocol": "Unknown",
            "src": "",
            "dst": "",
            "info": "",
        }

        # Ethernet header
        if len(pkt_data) >= 14 and network == 1:
            eth_type = struct.unpack("!H", pkt_data[12:14])[0]

            if eth_type == 0x0800 and len(pkt_data) >= 34:  # IPv4
                ihl = (pkt_data[14] & 0x0F) * 4
                proto = pkt_data[23]
                src_ip = ".".join(str(b) for b in pkt_data[26:30])
                dst_ip = ".".join(str(b) for b in pkt_data[30:34])
                pkt_info["src"] = src_ip
                pkt_info["dst"] = dst_ip

                ip_start = 14
                if proto == 6 and len(pkt_data) >= ip_start + ihl + 4:  # TCP
                    src_port, dst_port = struct.unpack(
                        "!HH", pkt_data[ip_start + ihl : ip_start + ihl + 4]
                    )
                    pkt_info["protocol"] = "TCP"
                    pkt_info["src"] = f"{src_ip}:{src_port}"
                    pkt_info["dst"] = f"{dst_ip}:{dst_port}"

                    # Визначення відомих сервісів
                    if dst_port == 80 or src_port == 80:
                        pkt_info["protocol"] = "HTTP"
                    elif dst_port == 443 or src_port == 443:
                        pkt_info["protocol"] = "HTTPS/TLS"
                    elif dst_port == 22 or src_port == 22:
                        pkt_info["protocol"] = "SSH"
                    elif dst_port == 3306 or src_port == 3306:
                        pkt_info["protocol"] = "MySQL"
                    elif dst_port == 53 or src_port == 53:
                        pkt_info["protocol"] = "DNS"

                    pkt_info["info"] = f"TCP {src_port} → {dst_port}"

                elif proto == 17 and len(pkt_data) >= ip_start + ihl + 4:  # UDP
                    src_port, dst_port = struct.unpack(
                        "!HH", pkt_data[ip_start + ihl : ip_start + ihl + 4]
                    )
                    pkt_info["protocol"] = "UDP"
                    pkt_info["src"] = f"{src_ip}:{src_port}"
                    pkt_info["dst"] = f"{dst_ip}:{dst_port}"
                    if dst_port == 53 or src_port == 53:
                        pkt_info["protocol"] = "DNS"
                    pkt_info["info"] = f"UDP {src_port} → {dst_port}"

                elif proto == 1:  # ICMP
                    pkt_info["protocol"] = "ICMP"
                    pkt_info["info"] = "ICMP Echo"

                else:
                    pkt_info["protocol"] = f"IPv4 (proto={proto})"

            elif eth_type == 0x0806:  # ARP
                pkt_info["protocol"] = "ARP"
                pkt_info["info"] = "ARP Request/Reply"

            elif eth_type == 0x86DD:  # IPv6
                pkt_info["protocol"] = "IPv6"

        packets.append(pkt_info)

    return packets


# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    """Головна сторінка — SPA Dashboard."""
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/dashboard")
def api_dashboard():
    """
    Агреговані дані для головного дашборду:
    - Загальна кількість подій
    - Розподіл за категоріями загроз
    - Кількість активних алертів
    - Метрики системи
    """
    # Отримуємо логи за останню годину
    now = time.time()
    start_ns = str(int((now - 3600) * 1e9))
    end_ns = str(int(now * 1e9))

    logs = _loki_query(
        '{container=~"wordpress-app|nginx-proxy|mysql-db"}',
        limit=500,
        start=start_ns,
        end=end_ns,
    )

    total_log_lines = 0
    containers = {}
    error_count = 0
    for stream in logs:
        container = stream.get("stream", {}).get("container", "unknown")
        values = stream.get("values", [])
        total_log_lines += len(values)
        containers[container] = containers.get(container, 0) + len(values)
        for val in values:
            line = val[1].lower() if len(val) > 1 else ""
            if any(kw in line for kw in ["error", "fail", "denied", "attack", "inject"]):
                error_count += 1

    # Активні алерти
    alerts = _get_alertmanager_alerts()
    active_alerts = [a for a in alerts if a.get("status", {}).get("state") == "active"]

    # Метрики системи
    cpu_result = _prometheus_query(
        '100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)'
    )
    cpu_usage = round(float(cpu_result[0]["value"][1]), 1) if cpu_result else 0

    mem_result = _prometheus_query(
        '(1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes) * 100'
    )
    mem_usage = round(float(mem_result[0]["value"][1]), 1) if mem_result else 0

    # HTTP rate
    http_result = _prometheus_query(
        'sum(rate(nginx_http_requests_total[5m]))'
    )
    http_rate = round(float(http_result[0]["value"][1]), 2) if http_result else 0

    return jsonify({
        "total_events": total_log_lines,
        "error_events": error_count,
        "active_alerts": len(active_alerts),
        "containers": containers,
        "system": {
            "cpu_usage": cpu_usage,
            "memory_usage": mem_usage,
            "http_rate": http_rate,
        },
        "timestamp": datetime.utcnow().isoformat(),
    })


@app.route("/api/dashboard/timeline")
def api_dashboard_timeline():
    """Дані для графіку timeline подій за останні 6 годин."""
    now = time.time()
    results = _prometheus_query_range(
        'sum(rate(nginx_http_requests_total[5m]))',
        start=now - 21600,
        end=now,
        step="300s",
    )

    timeline = []
    if results:
        for ts, val in results[0].get("values", []):
            timeline.append({
                "time": datetime.utcfromtimestamp(float(ts)).strftime("%H:%M"),
                "value": round(float(val), 4),
            })

    # Error rate timeline
    error_results = _prometheus_query_range(
        'sum(rate(nginx_http_requests_total{status=~"4..|5.."}[5m]))',
        start=now - 21600,
        end=now,
        step="300s",
    )

    error_timeline = []
    if error_results:
        for ts, val in error_results[0].get("values", []):
            error_timeline.append({
                "time": datetime.utcfromtimestamp(float(ts)).strftime("%H:%M"),
                "value": round(float(val), 4),
            })

    return jsonify({
        "requests": timeline,
        "errors": error_timeline,
    })


@app.route("/api/logs")
def api_logs():
    """
    Отримання логів з Loki з фільтрацією.
    Параметри: container, keyword, limit, hours
    """
    container = request.args.get("container", "")
    keyword = request.args.get("keyword", "")
    limit = min(int(request.args.get("limit", 100)), 500)
    hours = min(int(request.args.get("hours", 1)), 24)

    now = time.time()
    start_ns = str(int((now - hours * 3600) * 1e9))
    end_ns = str(int(now * 1e9))

    if container:
        query = f'{{container="{container}"}}'
    else:
        query = '{container=~"wordpress-app|nginx-proxy|mysql-db|ai-adapter|telegram-bot"}'

    if keyword:
        query += f' |~ "(?i){keyword}"'

    results = _loki_query(query, limit=limit, start=start_ns, end=end_ns)

    log_lines = []
    for stream in results:
        container_name = stream.get("stream", {}).get("container", "unknown")
        for val in stream.get("values", []):
            ts_ns = int(val[0])
            dt = datetime.utcfromtimestamp(ts_ns / 1e9)
            line = val[1] if len(val) > 1 else ""

            # Визначення рівня критичності
            level = "info"
            line_lower = line.lower()
            if any(kw in line_lower for kw in ["error", "fail", "fatal", "critical"]):
                level = "error"
            elif any(kw in line_lower for kw in ["warn", "warning"]):
                level = "warning"
            elif any(kw in line_lower for kw in ["attack", "inject", "exploit", "denied", "unauthorized"]):
                level = "danger"

            log_lines.append({
                "timestamp": dt.strftime("%Y-%m-%d %H:%M:%S"),
                "container": container_name,
                "message": line[:1000],
                "level": level,
            })

    # Сортування за часом
    log_lines.sort(key=lambda x: x["timestamp"], reverse=True)

    return jsonify({
        "logs": log_lines[:limit],
        "total": len(log_lines),
        "query": query,
    })


@app.route("/api/analyze", methods=["POST"])
def api_analyze():
    """
    Ручний аналіз вибраних логів через LLM.
    Body: { "logs": "текст логів", "context": "додатковий контекст" }
    """
    data = request.get_json(force=True)
    logs_text = data.get("logs", "")
    context = data.get("context", "network traffic analysis")

    if not logs_text:
        return jsonify({"error": "No logs provided"}), 400

    prompt = f"""You are a cybersecurity expert specializing in network traffic analysis.
Analyze the following logs/traffic data for security anomalies, malicious activity, and threats:

Context: {context}

Data:
{logs_text[:4000]}

Return a JSON object with these fields:
- risk_score: integer 0-10 (0=safe, 10=critical breach)
- threat_found: boolean
- threats: array of objects, each with:
  - type: threat category (SQL Injection, Brute-force, XSS, Port Scan, DDoS, Data Exfiltration, Unauthorized Access, Malware Communication, DNS Tunneling, Other)
  - severity: "low", "medium", "high", "critical"
  - description: what was found
  - affected_hosts: array of IPs/hosts involved
  - recommendations: mitigation steps
- summary: overall assessment in 2-3 sentences
- statistics: object with counts of suspicious_packets, unique_sources, unique_destinations, protocols_observed
"""

    result = _ollama_generate(prompt)
    if result is None:
        return jsonify({"error": "LLM analysis failed"}), 503

    return jsonify({
        "analysis": result,
        "model": LLM_MODEL,
        "analyzed_at": datetime.utcnow().isoformat(),
    })


@app.route("/api/analyze/pcap", methods=["POST"])
def api_analyze_pcap():
    """
    Завантаження та аналіз PCAP-файлу.
    Приймає multipart/form-data з полем 'file'.
    """
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["file"]
    if not file.filename.endswith((".pcap", ".cap")):
        return jsonify({"error": "Only .pcap/.cap files are supported"}), 400

    file_data = file.read()
    if len(file_data) > MAX_PCAP_SIZE:
        return jsonify({"error": f"File too large (max {MAX_PCAP_SIZE // 1024 // 1024} MB)"}), 400

    # Парсинг PCAP
    packets = _parse_pcap_bytes(file_data)
    if not packets:
        return jsonify({"error": "Failed to parse PCAP file or file is empty"}), 400

    # Статистика
    protocols = {}
    src_ips = set()
    dst_ips = set()
    for pkt in packets:
        proto = pkt["protocol"]
        protocols[proto] = protocols.get(proto, 0) + 1
        if pkt["src"]:
            src_ips.add(pkt["src"].split(":")[0])
        if pkt["dst"]:
            dst_ips.add(pkt["dst"].split(":")[0])

    stats = {
        "total_packets": len(packets),
        "protocols": protocols,
        "unique_sources": len(src_ips),
        "unique_destinations": len(dst_ips),
    }

    # Формування тексту для LLM-аналізу
    sample_text = "PCAP Traffic Summary:\n"
    sample_text += f"Total packets: {len(packets)}\n"
    sample_text += f"Protocols: {json.dumps(protocols)}\n"
    sample_text += f"Unique sources: {len(src_ips)}\n"
    sample_text += f"Unique destinations: {len(dst_ips)}\n\n"
    sample_text += "Sample packets (first 50):\n"
    for pkt in packets[:50]:
        sample_text += f"  [{pkt['timestamp']}] {pkt['protocol']} {pkt['src']} -> {pkt['dst']} len={pkt['length']} {pkt['info']}\n"

    prompt = f"""You are a cybersecurity expert. Analyze this network traffic capture (PCAP) for security threats.

{sample_text}

Return a JSON object with:
- risk_score: integer 0-10
- threat_found: boolean
- threats: array of detected threats, each with type, severity, description, affected_hosts, recommendations
- traffic_profile: object with normal_traffic_percent, suspicious_traffic_percent, malicious_traffic_percent
- summary: overall security assessment
- anomalies: array of specific anomalies found (unusual ports, scanning patterns, large data transfers, etc.)
"""

    analysis = _ollama_generate(prompt)

    return jsonify({
        "filename": file.filename,
        "file_hash": hashlib.sha256(file_data).hexdigest(),
        "statistics": stats,
        "packets": packets[:100],  # Перші 100 пакетів для відображення
        "analysis": analysis,
        "model": LLM_MODEL,
        "analyzed_at": datetime.utcnow().isoformat(),
    })


@app.route("/api/alerts")
def api_alerts():
    """Отримання алертів з Alertmanager."""
    alerts = _get_alertmanager_alerts()

    formatted = []
    for alert in alerts:
        labels = alert.get("labels", {})
        annotations = alert.get("annotations", {})
        formatted.append({
            "name": labels.get("alertname", "Unknown"),
            "severity": labels.get("severity", "unknown"),
            "container": labels.get("container", "unknown"),
            "risk_score": labels.get("risk_score", "N/A"),
            "problem_type": labels.get("problem_type", "N/A"),
            "summary": annotations.get("summary", ""),
            "description": annotations.get("description", ""),
            "recommendations": annotations.get("recommendations", ""),
            "status": alert.get("status", {}).get("state", "unknown"),
            "starts_at": alert.get("startsAt", ""),
            "ends_at": alert.get("endsAt", ""),
        })

    return jsonify({"alerts": formatted, "total": len(formatted)})


@app.route("/api/chat", methods=["POST"])
def api_chat():
    """
    Чат-інтерфейс для діалогу з LLM про кібербезпеку.
    Body: { "message": "запитання користувача" }
    """
    data = request.get_json(force=True)
    message = data.get("message", "").strip()

    if not message:
        return jsonify({"error": "Empty message"}), 400

    # Отримуємо контекст логів для відповіді
    logs = _loki_query(
        '{container=~"wordpress-app|nginx-proxy|mysql-db"}',
        limit=20,
    )
    log_context = ""
    for stream in logs:
        for val in stream.get("values", []):
            log_context += val[1][:200] + "\n"

    prompt = f"""You are NetVigil AI — a cybersecurity assistant specializing in network traffic analysis and threat detection.
You have access to real-time system logs and monitoring data.

Current system context (recent logs):
{log_context[:2000]}

User question: {message}

Provide a helpful, detailed answer about cybersecurity, network analysis, or the current system state.
Focus on actionable advice and specific technical details.
Answer in the same language as the user's question."""

    result = _ollama_generate(prompt, format_json=False)

    if result is None:
        return jsonify({"error": "LLM is unavailable"}), 503

    return jsonify({
        "response": result.get("text", "No response from LLM"),
        "model": LLM_MODEL,
        "timestamp": datetime.utcnow().isoformat(),
    })


@app.route("/api/metrics")
def api_metrics():
    """Отримання ключових метрик системи з Prometheus."""
    metrics = {}

    # CPU
    cpu = _prometheus_query('100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)')
    metrics["cpu_percent"] = round(float(cpu[0]["value"][1]), 1) if cpu else 0

    # Memory
    mem = _prometheus_query('(1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes) * 100')
    metrics["memory_percent"] = round(float(mem[0]["value"][1]), 1) if mem else 0

    # Disk
    disk = _prometheus_query('100 - ((node_filesystem_avail_bytes{mountpoint="/"} / node_filesystem_size_bytes{mountpoint="/"}) * 100)')
    metrics["disk_percent"] = round(float(disk[0]["value"][1]), 1) if disk else 0

    # Network I/O
    net_rx = _prometheus_query('rate(node_network_receive_bytes_total{device!="lo"}[5m])')
    net_tx = _prometheus_query('rate(node_network_transmit_bytes_total{device!="lo"}[5m])')
    metrics["network_rx_bps"] = round(float(net_rx[0]["value"][1]), 0) if net_rx else 0
    metrics["network_tx_bps"] = round(float(net_tx[0]["value"][1]), 0) if net_tx else 0

    # HTTP requests
    http = _prometheus_query('sum(rate(nginx_http_requests_total[5m]))')
    metrics["http_rps"] = round(float(http[0]["value"][1]), 2) if http else 0

    # MySQL uptime
    mysql_up = _prometheus_query('mysql_up')
    metrics["mysql_up"] = int(float(mysql_up[0]["value"][1])) if mysql_up else 0

    return jsonify(metrics)


@app.route("/api/health")
def api_health():
    """Перевірка стану сервісу."""
    return jsonify({
        "status": "ok",
        "service": "NetVigil Web App",
        "version": "1.0.0",
        "timestamp": datetime.utcnow().isoformat(),
    })


# ---------------------------------------------------------------------------
# Запуск
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
