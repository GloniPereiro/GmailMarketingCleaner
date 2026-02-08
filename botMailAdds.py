from __future__ import print_function
import os.path
import base64
import re
from collections import defaultdict
from urllib.parse import urlparse
import csv
import time
from googleapiclient.errors import HttpError





from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# Zakres: tylko odczyt maili
SCOPES = [ "https://www.googleapis.com/auth/gmail.readonly", "https://www.googleapis.com/auth/gmail.modify"]
3

def get_gmail_service():
    creds = None

    # jeśli istnieje token z poprzednich logowań – użyj go
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)

    # jeśli brak ważnych credów – zaloguj się
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                'credentials.json', SCOPES
            )
            # UWAGA: zmiana tu ↓↓↓
            creds = flow.run_local_server(port=0)

        # zapisz token na przyszłość
        with open('token.json', 'w') as token:
            token.write(creds.to_json())

    service = build('gmail', 'v1', credentials=creds)
    return service


def is_marketing_message(headers, snippet):
    """
    Prosta heurystyka: patrzymy w nagłówki + snippet.
    Możesz rozbudować według potrzeb.
    """
    text = " ".join([str(h.get('value', '')) for h in headers]) + " " + snippet

    marketing_keywords = [
    # Polish
    "newsletter", "oferta", "promocja", "promocje", "wyprzedaż", "wyprzedaz",
    "rabaty", "rabat", "zniżka", "znizka", "zniżki", "znizki",
    "nowości", "nowosci", "subskrypcja", "subskrypcje",
    "okazja", "okazje", "kupon", "kupony", "kody rabatowe",
    "specjalna oferta", "limitowana oferta",

    # English
    "newsletter", "offer", "offers", "promotion", "promotions",
    "sale", "sales", "discount", "discounts",
    "deal", "deals", "special offer", "limited offer",
    "subscribe", "subscription", "subscribed",
    "new arrivals", "new collection", "new products",
    "exclusive", "exclusive offer", "flash sale",
    "coupon", "coupons", "voucher", "vouchers",
    "save", "save now", "save up to",
    "clearance", "bargain", "hot deal", "best price"
]


    list_headers = ["List-Unsubscribe", "List-ID", "Precedence"]

    # Sygnały z nagłówków
    for h in headers:
        name = h.get('name', '').lower()
        value = h.get('value', '').lower()
        if name in [lh.lower() for lh in list_headers]:
            return True
        if any(tool in value for tool in ["mailchimp", "sendgrid", "mailgun", "hubspot", "getresponse"]):
            return True

    # Słowa kluczowe w treści/snippcie
    lower_text = text.lower()
    if any(kw in lower_text for kw in marketing_keywords):
        return True

    return False




def extract_sender(headers):
    for h in headers:
        if h.get('name', '').lower() == 'from':
            return h.get('value')
    return None

def parse_email_address(raw_from):
    # bardzo prosty parser, wystarczy na większość przypadków
    match = re.search(r'<([^>]+)>', raw_from)
    email_addr = match.group(1) if match else raw_from
    email_addr = email_addr.strip().strip('"').strip()
    return email_addr

def get_domain(email_addr):
    parts = email_addr.split('@')
    return parts[1].lower() if len(parts) == 2 else ''

delete_progress = {
    "in_progress": False,
    "done": 0,
    "total": 0
}

def delete_messages_by_ids(ids):
    global delete_progress
    delete_progress["in_progress"] = True
    delete_progress["done"] = 0
    delete_progress["total"] = len(ids)

    service = get_gmail_service()
    batch_size = 50
    deleted = 0

    for i in range(0, len(ids), batch_size):
        chunk = ids[i:i + batch_size]

        # retry + backoff
        for attempt in range(5):
            try:
                service.users().messages().batchModify(
                    userId="me",
                    body={
                        "ids": chunk,
                        "removeLabelIds": ["INBOX"],
                        "addLabelIds": ["TRASH"]
                    }
                ).execute()
                break
            except HttpError as e:
                if e.resp.status == 403 and "rateLimitExceeded" in str(e):
                    time.sleep(1 + attempt * 0.5)
                    continue
                raise

        deleted += len(chunk)
        delete_progress["done"] = deleted

        time.sleep(0.3)

    delete_progress["in_progress"] = False
    return deleted

def fetch_messages_batch(service, ids, callback):
    batch = service.new_batch_http_request()

    for msg_id in ids:
        batch.add(
            service.users().messages().get(
                userId='me',
                id=msg_id,
                format='metadata',
                metadataHeaders=['From', 'Subject', 'List-Unsubscribe', 'List-ID', 'Precedence']
            ),
            callback=callback
        )

    batch.execute()


def main():
    service = get_gmail_service()

    messages = []
    page = service.users().messages().list(
        userId='me',
        labelIds=['INBOX'],
        maxResults=500,
        q="newer_than:60d"
    )

    while page is not None:
        result = page.execute()
        msgs = result.get('messages', [])
        messages.extend(msgs)

        page_token = result.get('nextPageToken')
        if page_token:
            page = service.users().messages().list(
                userId='me',
                labelIds=['INBOX'],
                maxResults=500,
                q="newer_than:60d",
                pageToken=page_token
            )
        else:
            page = None

    print(f"Znaleziono {len(messages)} wiadomości, analizuję...")

    stats = {}  # klucz: pełny From, wartość: dict z info

    for msg_meta in messages:
        msg_id = msg_meta['id']
        msg = service.users().messages().get(
            userId='me',
            id=msg_id,
            format='metadata',
            metadataHeaders=['From', 'Subject', 'List-Unsubscribe', 'List-ID', 'Precedence']
        ).execute()

        headers = msg.get('payload', {}).get('headers', [])
        snippet = msg.get('snippet', '')

        if is_marketing_message(headers, snippet):
            raw_from = extract_sender(headers)
            if not raw_from:
                continue

            email_addr = parse_email_address(raw_from)
            domain = get_domain(email_addr)

            if raw_from not in stats:
                stats[raw_from] = {
                    "email": email_addr,
                    "domain": domain,
                    "count": 0,
                }
            stats[raw_from]["count"] += 1

    print("\nNadawcy reklamowi (heurystycznie wykryci):\n")
    for sender, info in sorted(stats.items(), key=lambda x: (-x[1]["count"], x[0])):
        print(f'{sender}  |  {info["email"]}  |  {info["domain"]}  |  {info["count"]} maili')

    print("\nPropozycje filtrów Gmaila (po domenach):\n")
    queries = generate_gmail_filter_queries(stats, min_count=1)
    for domain, q in queries:
        print(f"{domain}: {q}")


    # zapis do CSV
    csv_filename = "marketing_senders.csv"
    with open(csv_filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter=';')
        writer.writerow(["Sender", "Email", "Domain", "Count"])
        for sender, info in sorted(stats.items(), key=lambda x: (-x[1]["count"], x[0])):
            writer.writerow([sender, info["email"], info["domain"], info["count"]])

    print(f"\nZapisano dane do pliku: {csv_filename}")


if __name__ == '__main__':
    main()
