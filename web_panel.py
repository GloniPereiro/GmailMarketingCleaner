from flask import Flask, render_template, jsonify, request, Response
import threading
import json
import os
import csv
import io

from botMailAdds import (
    get_gmail_service,
    is_marketing_message,
    extract_sender,
    parse_email_address,
    get_domain,
    delete_messages_by_ids,
    fetch_messages_batch
)

app = Flask(__name__)

CACHE_FILE = "cache.json"

scan_state = {
    "in_progress": False,
    "total": 0,
    "done": 0,
    "stats": {},          # sender -> {email, domain, count}
    "sender_to_ids": {},  # sender -> [message_ids]
    "days": 30,
    "label": "INBOX"
}

scan_lock = threading.Lock()


def save_cache():
    with scan_lock:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(scan_state, f, ensure_ascii=False, indent=2)


def load_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        with scan_lock:
            scan_state.update(data)


load_cache()

def background_scan(days, label):
    service = get_gmail_service()

    with scan_lock:
        scan_state["in_progress"] = True
        scan_state["done"] = 0
        scan_state["total"] = 0
        scan_state["stats"] = {}
        scan_state["sender_to_ids"] = {}
        scan_state["days"] = days
        scan_state["label"] = label

    msgs = service.users().messages().list(
        userId='me',
        labelIds=[label],
        maxResults=500,
        q=f"newer_than:{days}d"
    ).execute()

    all_ids = [m['id'] for m in msgs.get('messages', [])]
    next_token = msgs.get('nextPageToken')

    while next_token:
        msgs = service.users().messages().list(
            userId='me',
            labelIds=[label],
            maxResults=500,
            pageToken=next_token,
            q=f"newer_than:{days}d"
        ).execute()
        all_ids.extend([m['id'] for m in msgs.get('messages', [])])
        next_token = msgs.get('nextPageToken')

    with scan_lock:
        scan_state["total"] = len(all_ids)

    def callback(request_id, response, exception):
        if exception:
            with scan_lock:
                scan_state["done"] += 1
            return

        headers = response.get('payload', {}).get('headers', [])
        snippet = response.get('snippet', '')

        if is_marketing_message(headers, snippet):
            raw = extract_sender(headers)
            email = parse_email_address(raw)
            domain = get_domain(email)

            with scan_lock:
                if raw not in scan_state["stats"]:
                    scan_state["stats"][raw] = {
                        "email": email,
                        "domain": domain,
                        "count": 0
                    }
                    scan_state["sender_to_ids"][raw] = []

                scan_state["stats"][raw]["count"] += 1
                scan_state["sender_to_ids"][raw].append(response['id'])

        with scan_lock:
            scan_state["done"] += 1

    BATCH_SIZE = 100
    for i in range(0, len(all_ids), BATCH_SIZE):
        batch_ids = all_ids[i:i+BATCH_SIZE]
        fetch_messages_batch(service, batch_ids, callback)
        save_cache()

    with scan_lock:
        scan_state["in_progress"] = False

    save_cache()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/start-scan", methods=["POST"])
def start_scan():
    data = request.get_json()
    days = int(data.get("days", 30))
    label = data.get("label", "INBOX")

    with scan_lock:
        if scan_state["in_progress"]:
            return jsonify({"status": "already_running"})

    t = threading.Thread(target=background_scan, args=(days, label))
    t.start()
    return jsonify({"status": "started"})


@app.route("/progress")
def progress():
    with scan_lock:
        return jsonify(scan_state)


@app.route("/results")
def results():
    with scan_lock:
        stats = scan_state["stats"]

    senders = [
        {"sender": s, "info": info}
        for s, info in sorted(stats.items(), key=lambda x: (-x[1]["count"], x[0]))
    ]

    return jsonify({"senders": senders})


@app.route("/delete", methods=["POST"])
def delete():
    data = request.get_json()
    sender = data.get("sender")

    with scan_lock:
        ids = scan_state["sender_to_ids"].get(sender, [])

    deleted = delete_messages_by_ids(ids)

    with scan_lock:
        if sender in scan_state["stats"]:
            scan_state["stats"][sender]["count"] = 0
            scan_state["sender_to_ids"][sender] = []

    save_cache()

    return jsonify({"deleted": deleted})

@app.route("/delete-progress")
def delete_progress_api():
    from botMailAdds import delete_progress
    return jsonify(delete_progress)



@app.route("/export")
def export():
    with scan_lock:
        stats = scan_state["stats"]

    output = io.StringIO()
    writer = csv.writer(output, delimiter=';')
    writer.writerow(["Sender", "Email", "Domain", "Count"])

    for sender, info in sorted(stats.items(), key=lambda x: (-x[1]["count"], x[0])):
        writer.writerow([sender, info["email"], info["domain"], info["count"]])

    csv_data = output.getvalue()
    output.close()

    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=marketing_senders.csv"}
    )



if __name__ == "__main__":
    app.run(debug=True)
