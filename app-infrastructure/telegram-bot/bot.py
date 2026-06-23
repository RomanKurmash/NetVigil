import os
import asyncio
import httpx
import logging
import html
import json
import hashlib
from datetime import datetime, timedelta
from fastapi import FastAPI, Request
from dotenv import load_dotenv


load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
app = FastAPI()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
LOKI_URL = os.getenv("LOKI_URL")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://ollama:11434")
LLM_MODEL = os.getenv("LLM_MODEL", "llama3")
TELEGRAM_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

if not BOT_TOKEN or not CHAT_ID:
    logger.error("Error: Telegram credentials not found in env.")
else:
    logger.info(f"Telegram Bot initialized. Chat ID: {CHAT_ID}")

active_alerts = set()
HISTORY_FILE = "notifications_history.json"
notifications_history = []

def load_history():
    global notifications_history
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                notifications_history = json.load(f)
                logger.info(f"Loaded {len(notifications_history)} entries from history.")
        except Exception as e:
            logger.error(f"Error loading history file: {e}")
            notifications_history = []
    else:
        notifications_history = []

def save_history():
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(notifications_history, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Error saving history file: {e}")

def format_iso_timestamp(ts_str):
    if not ts_str or ts_str.startswith("0001-"):
        return None
    try:
        # e.g., 2026-06-23T09:45:00.123Z or 2026-06-23T09:45:00Z
        clean_ts = ts_str.replace("Z", "+00:00")
        if "." in clean_ts:
            parts = clean_ts.split(".")
            subparts = parts[1].split("+")
            usec = subparts[0][:6]
            clean_ts = parts[0] + "." + usec + "+" + subparts[1]
        dt = datetime.fromisoformat(clean_ts)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ts_str

async def query_loki_timeframe(start_time_str, duration_seconds=600):
    if not LOKI_URL:
        return []
    try:
        dt = datetime.strptime(start_time_str, "%Y-%m-%d %H:%M:%S")
        start_dt = dt - timedelta(seconds=duration_seconds // 2)
        end_dt = dt + timedelta(seconds=duration_seconds // 2)
        
        # Loki epoch nanoseconds
        start_ns = str(int(start_dt.timestamp() * 1e9))
        end_ns = str(int(end_dt.timestamp() * 1e9))
        
        query = '{container=~"wordpress-app|nginx-proxy|mysql-db|ai-adapter|telegram-bot"} |~ "(?i)error|fail|exception|critical|fatal|attack|warn"'
        
        async with httpx.AsyncClient() as client:
            params = {"query": query, "limit": 20, "start": start_ns, "end": end_ns, "direction": "forward"}
            resp = await client.get(LOKI_URL, params=params, timeout=5.0)
            if resp.status_code != 200:
                logger.error(f"Loki timeframe query returned HTTP {resp.status_code}")
                return []
            
            results = resp.json().get("data", {}).get("result", [])
            correlated_logs = []
            for res in results:
                container = res.get("stream", {}).get("container", "unknown")
                for val in res.get("values", []):
                    ts_ns = int(val[0])
                    log_dt = datetime.utcfromtimestamp(ts_ns / 1e9)
                    message = val[1]
                    correlated_logs.append({
                        "timestamp": log_dt.strftime("%Y-%m-%d %H:%M:%S"),
                        "container": container,
                        "message": message[:150].strip()
                    })
            correlated_logs.sort(key=lambda x: x["timestamp"])
            return correlated_logs
    except Exception as e:
        logger.error(f"Loki timeframe query error: {e}")
        return []

load_history()



def safe_escape(value):
    if isinstance(value, list):
        value = "\n".join(f"- {item}" for item in value)
    return html.escape(str(value))

async def send_to_telegram(message: str):
    async with httpx.AsyncClient() as client:
        try:
            payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}
            response = await client.post(TELEGRAM_URL, json=payload, timeout=10.0)
            if response.status_code != 200:
                logger.error(f"Telegram error: {response.text}")
        except Exception as e:
            logger.error(f"Telegram connection error: {e}")

async def get_loki_logs(container_name: str):
    if not LOKI_URL:
        return "LOKI_URL not configured."
    query = f'{{container="{container_name}"}} |~ "(?i)error|fail|exception|critical|fatal"'
    async with httpx.AsyncClient() as client:
        try:
            params = {"query": query, "limit": 10, "direction": "backward"}
            response = await client.get(LOKI_URL, params=params, timeout=5.0)
            results = response.json().get("data", {}).get("result", [])
            if not results:
                params["query"] = f'{{container="{container_name}"}}'
                response = await client.get(LOKI_URL, params=params)
                results = response.json().get("data", {}).get("result", [])
            lines = []
            for res in results:
                for val in res.get("values", []):
                    safe_log = html.escape(val[1][:200])
                    lines.append(f"<code>{safe_log}</code>")
            return "\n".join(lines) if lines else "No logs found in Loki."
        except Exception as e:
            return f"Failed to fetch logs: {html.escape(str(e))}"

async def analyze_with_llm(container_name: str, logs_text: str):
    if not OLLAMA_URL or not logs_text or "No logs found" in logs_text:
        return None
    clean_logs = logs_text.replace("<code>", "").replace("</code>", "")
    prompt = f"""Analyze the following network logs and traffic records to identify any network security threats, malicious payloads, anomalous traffic patterns, or service failures.
Container: {container_name}
Logs/Traffic:
{clean_logs}

Return a JSON object with the following fields:
- problem_type: choose the most appropriate category from the following 10 types:
  1. "SQL Injection"
  2. "Remote Code Execution"
  3. "Brute-force Attack"
  4. "Cross-Site Scripting"
  5. "Unauthorized Access"
  6. "Database Connection Failure"
  7. "Out of Memory Crash"
  8. "High Error Rate"
  9. "Service Timeout"
  10. "Resource Exhaustion"
  11. "DDoS Attack"
- extended_summary: a concise but detailed explanation of what happened based on the logs/traffic analysis
- recommendations: what network/security actions should be taken to mitigate this issue
"""
    payload = {
        "model": LLM_MODEL,
        "prompt": prompt,
        "format": "json",
        "stream": False
    }
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(f"{OLLAMA_URL}/api/generate", json=payload, timeout=90.0)
            if response.status_code == 200:
                result = json.loads(response.json().get("response", "{}"))
                return result
        except Exception as e:
            logger.error(f"Error calling LLM: {e}")
        return None

async def heartbeat_loop():
    while True:
        await asyncio.sleep(7200)
        now = datetime.now().strftime("%d-%m-%Y %H:%M:%S")
        await send_to_telegram(f"🛡 <b>NetVigil Heartbeat [{now}]</b>\nВсі системи мережевого аналізу працюють у штатному режимі.")

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(heartbeat_loop())

@app.get("/history")
async def get_history():
    return {"history": notifications_history}

@app.post("/history/{event_id}/retrospective")
async def generate_retrospective(event_id: str):
    global notifications_history
    target_event = None
    for ev in notifications_history:
        if ev.get("id") == event_id:
            target_event = ev
            break
            
    if not target_event:
        return {"error": "Event not found"}, 404
        
    if target_event.get("retrospective"):
        return {"status": "success", "retrospective": target_event["retrospective"]}
        
    try:
        prompt = f"""You are a senior SecOps analyst conducting an incident retrospective (post-mortem).
Analyze the following security incident event:

Incident: {target_event['alert_name']} (Type: {target_event['problem_type']})
Target Container: {target_event['container']}
Severity: {target_event['severity']} (Risk Score: {target_event['risk_score']}/10)
Timeline: Started at {target_event['start_time']}, Ended at {target_event['end_time']}

Triggering Logs:
{target_event['logs']}

Write a professional cybersecurity retrospective report in JSON format with these exact keys:
- root_cause: analysis of why the alert triggered and the probable attack vector/system issue.
- timeline: list of key steps (e.g. Alert fired, logs analyzed, resolution detected).
- impact_assessment: what was affected (confidentiality, integrity, availability).
- preventative_actions: bullet points of specific architectural, network, or policy changes to prevent recurrence.
- correlation_summary: how this event relates to typical threat patterns.

Your response MUST be valid JSON only. Answer in Ukrainian language. Do not output markdown code blocks wrapper, just output the raw JSON text.
"""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{OLLAMA_URL}/api/generate",
                json={
                    "model": LLM_MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.2}
                },
                timeout=90.0
            )
            if resp.status_code == 200:
                response_text = resp.json().get("response", "").strip()
                if response_text.startswith("```json"):
                    response_text = response_text.split("```json")[1]
                if response_text.endswith("```"):
                    response_text = response_text.rsplit("```", 1)[0]
                response_text = response_text.strip()
                
                try:
                    report = json.loads(response_text)
                    target_event["retrospective"] = report
                    save_history()
                    return {"status": "success", "retrospective": report}
                except Exception as parse_err:
                    logger.error(f"Failed to parse LLM JSON retrospective: {parse_err}. Response was: {response_text}")
                    report = {
                        "root_cause": "Не вдалося розпарсити автоматичний звіт. Див. сирий опис.",
                        "timeline": ["Початок: " + target_event['start_time'], "Кінець: " + target_event['end_time']],
                        "impact_assessment": "Рівень загрози: " + target_event['severity'],
                        "preventative_actions": ["Перевірте логи контейнера вручну."],
                        "correlation_summary": "Спроба аналізу завершилась помилкою."
                    }
                    target_event["retrospective"] = report
                    save_history()
                    return {"status": "success", "retrospective": report}
            else:
                return {"error": f"Ollama returned HTTP {resp.status_code}"}, 502
    except Exception as e:
        logger.error(f"Retrospective generation error: {e}")
        return {"error": str(e)}, 500

@app.post("/history/{event_id}/correlate")
async def run_correlation(event_id: str):
    global notifications_history
    target_event = None
    for ev in notifications_history:
        if ev.get("id") == event_id:
            target_event = ev
            break
            
    if not target_event:
        return {"error": "Event not found"}, 404
        
    correlated_events = []
    target_dt = None
    try:
        target_dt = datetime.strptime(target_event["start_time"], "%Y-%m-%d %H:%M:%S")
    except Exception:
        pass
        
    if target_dt:
        for ev in notifications_history:
            if ev.get("id") == target_event["id"]:
                continue
            try:
                ev_dt = datetime.strptime(ev["start_time"], "%Y-%m-%d %H:%M:%S")
                diff = abs((target_dt - ev_dt).total_seconds())
                if diff <= 600:
                    correlated_events.append({
                        "id": ev["id"],
                        "alert_name": ev["alert_name"],
                        "container": ev["container"],
                        "start_time": ev["start_time"],
                        "status": ev["status"],
                        "time_diff_seconds": int(diff)
                    })
            except Exception:
                pass
                
    correlated_logs = []
    if target_dt:
        correlated_logs = await query_loki_timeframe(target_event["start_time"])
        
    target_event["correlated_events"] = {
        "events": correlated_events,
        "logs": correlated_logs
    }
    save_history()
    
    return {
        "status": "success",
        "correlated_events": target_event["correlated_events"]
    }



@app.post("/alert")
async def handle_alert(request: Request):
    global notifications_history
    try:
        data = await request.json()
        alerts = data if isinstance(data, list) else data.get('alerts', [])
        for alert in alerts:
            status = alert.get('status', 'firing')
            labels = alert.get('labels', {})
            annotations = alert.get('annotations', {})
            alert_name = safe_escape(labels.get('alertname', 'Unknown Alert'))
            
            if alert_name == "Watchdog":
                continue
                
            container = safe_escape(labels.get('container', labels.get('service', 'unknown')))
            severity = safe_escape(labels.get('severity', 'warning'))
            risk_score = safe_escape(labels.get('risk_score', '0'))
            
            starts_at_raw = alert.get('startsAt')
            ends_at_raw = alert.get('endsAt')
            
            start_time = format_iso_timestamp(starts_at_raw) or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            end_time = format_iso_timestamp(ends_at_raw)
            if not end_time and status == 'resolved':
                end_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            elif not end_time:
                end_time = "Active"
            
            problem_type_val = "System Alert"
            summary_val = ""
            recommendations_val = ""
            logs_val = ""
            
            if status == 'firing':
                active_alerts.add(alert_name)
                logs = await get_loki_logs(container)
                logs_val = logs
                
                if alert_name == "SecOpsAIAnomalyDetected":
                    problem_type = safe_escape(labels.get('problem_type', 'AI Anomaly Detected'))
                    description = safe_escape(annotations.get('description', 'No description provided'))
                    recommendations = safe_escape(annotations.get('recommendations', 'No recommendations provided'))
                    
                    problem_type_val = problem_type
                    summary_val = description
                    recommendations_val = recommendations
                    
                    message = (
                        f"🚨 <b>[NetVigil AI — Network Threat Alert]</b>\n"
                        f"🛡️ <b>Threat Type:</b> <code>{problem_type}</code>\n"
                        f"📦 <b>Target Container:</b> <code>{container}</code>\n"
                        f"⚠️ <b>Severity:</b> {severity} (Risk: {risk_score}/10)\n\n"
                        f"📖 <b>Threat Analysis:</b>\n{description}\n\n"
                        f"💡 <b>Recommended Actions:</b>\n{recommendations}"
                    )
                else:
                    ai_analysis = await analyze_with_llm(container, logs)
                    if ai_analysis:
                        problem_type = safe_escape(ai_analysis.get('problem_type', 'System Anomaly'))
                        extended_summary = safe_escape(ai_analysis.get('extended_summary', 'No summary provided'))
                        recommendations = safe_escape(ai_analysis.get('recommendations', 'No recommendations provided'))
                        
                        problem_type_val = problem_type
                        summary_val = extended_summary
                        recommendations_val = recommendations
                        
                        message = (
                            f"🚨 <b>[NetVigil AI — Network Incident Detected]</b>\n"
                            f"🛡️ <b>Incident Type:</b> <code>{problem_type}</code>\n"
                            f"📦 <b>Target Container:</b> <code>{container}</code>\n"
                            f"⚠️ <b>Severity:</b> {severity}\n\n"
                            f"📖 <b>Traffic Analysis:</b>\n{extended_summary}\n\n"
                            f"💡 <b>Recommended Actions:</b>\n{recommendations}\n\n"
                            f"📄 <b>Observed Traffic Logs:</b>\n{logs}"
                        )
                    else:
                        summary = safe_escape(annotations.get('summary', 'No summary'))
                        
                        problem_type_val = alert_name
                        summary_val = summary
                        recommendations_val = "N/A"
                        
                        message = (
                            f"🚨 <b>[NetVigil — Unanalyzed Alert]</b>\n"
                            f"🔔 <b>Alert Name:</b> {alert_name}\n"
                            f"📦 <b>Target Container:</b> <code>{container}</code>\n"
                            f"⚠️ <b>Severity:</b> {severity}\n"
                            f"📝 <b>Summary:</b> {summary}\n\n"
                            f"📄 <b>Observed Traffic Logs:</b>\n{logs}"
                        )
            else:
                active_alerts.discard(alert_name)
                problem_type_val = "Alert Resolved"
                summary_val = f"Мережевий трафік стабілізовано. Алерт {alert_name} деактивовано."
                recommendations_val = "No action required."
                logs_val = ""
                message = f"✅ <b>[NetVigil — RESOLVED]</b>\n🔔 <b>Alert Name:</b> {alert_name}\n📦 <b>Container:</b> <code>{container}</code>\n🟢 Мережевий трафік стабілізовано."
                
            await send_to_telegram(message)
            
            # Find and update existing active event or create a new one
            existing_event = None
            for ev in notifications_history:
                if ev.get("alert_name") == alert_name and ev.get("container") == container and ev.get("status") == "firing":
                    existing_event = ev
                    break
            
            if existing_event:
                if status == 'resolved':
                    existing_event["status"] = "resolved"
                    existing_event["end_time"] = end_time
                    existing_event["messages"].append({
                        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "status": "resolved",
                        "message": message
                    })
                else:
                    # Update active event logs or details if needed
                    existing_event["logs"] = logs_val
                    existing_event["risk_score"] = risk_score
                    existing_event["severity"] = severity
                    existing_event["problem_type"] = problem_type_val
                    existing_event["summary"] = summary_val
                    existing_event["recommendations"] = recommendations_val
            else:
                event_id = hashlib.md5(f"{alert_name}_{container}_{start_time}".encode()).hexdigest()
                new_event = {
                    "id": event_id,
                    "timestamp": start_time,
                    "alert_name": alert_name,
                    "container": container,
                    "severity": severity,
                    "risk_score": risk_score,
                    "status": status,
                    "start_time": start_time,
                    "end_time": end_time,
                    "problem_type": problem_type_val,
                    "summary": summary_val,
                    "recommendations": recommendations_val,
                    "logs": logs_val,
                    "messages": [
                        {
                            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            "status": status,
                            "message": message
                        }
                    ],
                    "retrospective": None,
                    "correlated_events": None
                }
                notifications_history.insert(0, new_event)
                
            if len(notifications_history) > 200:
                notifications_history = notifications_history[:200]
            save_history()

        return {"status": "success"}
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return {"status": "error", "message": str(e)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)