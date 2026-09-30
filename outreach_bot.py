#!/usr/bin/env python3
"""
Referral outreach bot for a home health care agency.

What it does each time it runs (once a day is the idea):
  1. Checks your inbox for replies. Anyone who replied is taken out of the
     sequence, and anyone who asked to be removed goes on the do-not-contact list.
  2. Sends the next email in the sequence to every facility that is due,
     up to your daily limit, with a random pause between emails.
  3. Saves progress to state.json and logs every send to sent_log.csv.

Usage:
  python outreach_bot.py --dry-run   # preview emails, sends nothing
  python outreach_bot.py             # real run
  python outreach_bot.py --status    # summary of where every contact stands

Your email password is read from the OUTREACH_EMAIL_PASSWORD environment
variable (for Gmail, use an App Password, not your normal password).
"""
import argparse
import csv
import email
import imaplib
import json
import os
import random
import re
import smtplib
import ssl
import sys
import time
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid, parseaddr
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"
LEADS_PATH = HERE / "leads.csv"
DNC_PATH = HERE / "do_not_contact.csv"
STATE_PATH = HERE / "state.json"
LOG_PATH = HERE / "sent_log.csv"
TEMPLATE_DIR = HERE / "templates"

STOP_WORDS = ["unsubscribe", "remove", "stop emailing", "do not contact",
              "don't contact", "take me off", "opt out", "opt-out"]


class SafeDict(dict):
    """Leaves unknown {placeholders} blank instead of crashing."""
    def __missing__(self, key):
        return ""


# ---------- loading & saving ----------

def load_config():
    if not CONFIG_PATH.exists():
        sys.exit("config.json not found next to outreach_bot.py")
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def load_leads():
    if not LEADS_PATH.exists():
        sys.exit("leads.csv not found. Copy leads_template.csv to leads.csv and fill it in.")
    leads, seen = [], set()
    with LEADS_PATH.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            row = {k.strip(): (v or "").strip() for k, v in row.items() if k}
            addr = row.get("email", "").lower()
            if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", addr):
                if addr:
                    print(f"  skipping invalid email: {addr}")
                continue
            if addr in seen:
                continue
            seen.add(addr)
            row["email"] = addr
            leads.append(row)
    return leads


def load_dnc():
    if not DNC_PATH.exists():
        return set()
    with DNC_PATH.open(newline="", encoding="utf-8-sig") as f:
        return {(r.get("email") or "").strip().lower() for r in csv.DictReader(f)}


def add_to_dnc(addr, reason):
    new = not DNC_PATH.exists()
    with DNC_PATH.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["email", "reason", "date"])
        w.writerow([addr, reason, date.today().isoformat()])


def load_state():
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {}


def save_state(state):
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(STATE_PATH)


def log_send(addr, facility, step, subject):
    new = not LOG_PATH.exists()
    with LOG_PATH.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["timestamp", "email", "facility", "step", "subject"])
        w.writerow([datetime.now().isoformat(timespec="seconds"), addr, facility, step, subject])


# ---------- email content ----------

def render(template_name, lead, cfg):
    text = (TEMPLATE_DIR / template_name).read_text(encoding="utf-8")
    first, _, body = text.partition("\n")
    if not first.lower().startswith("subject:"):
        sys.exit(f"{template_name}: first line must start with 'Subject:'")
    subject = first[len("subject:"):].strip()

    c = cfg["company"]
    first_name = lead.get("contact_first_name", "")
    fields = SafeDict({
        **{f"company_{k}": v for k, v in c.items()},
        **c,
        **lead,
        "greeting": f"Hi {first_name}," if first_name else "Hello,",
        "facility_name": lead.get("facility_name") or "your facility",
    })
    subject = subject.format_map(fields)
    body = body.strip().format_map(fields)

    footer = (
        f"\n\n--\n{c['sender_name']}, {c['sender_title']}\n{c['name']}\n"
        f"{c['phone']}  |  {c['website']}\n{c['mailing_address']}\n\n"
        "If you'd prefer not to hear from us, just reply \"remove\" and we won't email you again."
    )
    return subject, body + footer


def build_message(cfg, lead, entry, subject, body):
    user = cfg["email"]["username"]
    msg = EmailMessage()
    msg["From"] = formataddr((cfg["company"]["sender_name"], user))
    msg["To"] = lead["email"]
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=user.split("@")[-1])
    if not subject and entry["message_ids"]:
        # Follow-up: reply in the same thread as the first email.
        msg["Subject"] = "Re: " + entry["first_subject"]
        msg["In-Reply-To"] = entry["message_ids"][-1]
        msg["References"] = " ".join(entry["message_ids"])
    else:
        msg["Subject"] = subject or "Home health nursing support"
    msg.set_content(body)
    return msg


# ---------- mail servers ----------

def password():
    pw = os.environ.get("OUTREACH_EMAIL_PASSWORD")
    if not pw:
        sys.exit("Set the OUTREACH_EMAIL_PASSWORD environment variable first (see README).")
    return pw


def smtp_send(cfg, msg):
    e = cfg["email"]
    ctx = ssl.create_default_context()
    if e.get("smtp_starttls"):
        with smtplib.SMTP(e["smtp_host"], e["smtp_port"], timeout=60) as s:
            s.starttls(context=ctx)
            s.login(e["username"], password())
            s.send_message(msg)
    else:
        with smtplib.SMTP_SSL(e["smtp_host"], e["smtp_port"], context=ctx, timeout=60) as s:
            s.login(e["username"], password())
            s.send_message(msg)


def body_text(m):
    parts = []
    for p in (m.walk() if m.is_multipart() else [m]):
        if p.get_content_type() == "text/plain" and not p.get_filename():
            payload = p.get_payload(decode=True)
            if payload:
                parts.append(payload.decode(p.get_content_charset() or "utf-8", "replace"))
    return "\n".join(parts)


def new_part_of_reply(text):
    """Drops the quoted original so our own footer's 'remove' isn't counted."""
    lines = []
    for line in text.splitlines():
        if line.startswith(">") or re.match(r"^On .+wrote:\s*$", line) or line.startswith("-----Original"):
            break
        lines.append(line)
    return "\n".join(lines).lower()


def check_inbox(cfg, state):
    active = {a: s for a, s in state.items() if s["status"] == "active" and s["step"] > 0}
    if not active:
        return []
    e = cfg["email"]
    earliest = min(date.fromisoformat(s["first_sent"]) for s in active.values())
    since = (earliest - timedelta(days=1)).strftime("%d-%b-%Y")
    notes = []
    M = imaplib.IMAP4_SSL(e["imap_host"], e["imap_port"])
    try:
        M.login(e["username"], password())
        M.select("INBOX", readonly=True)

        for addr, s in active.items():
            typ, data = M.search(None, "FROM", f'"{addr}"', "SINCE", since)
            nums = data[0].split() if typ == "OK" and data and data[0] else []
            if not nums:
                continue
            typ, msgdata = M.fetch(nums[-1], "(RFC822)")
            m = email.message_from_bytes(msgdata[0][1])
            reply = new_part_of_reply(body_text(m))
            if any(w in reply for w in STOP_WORDS):
                s["status"] = "unsubscribed"
                add_to_dnc(addr, "asked to be removed")
                notes.append(f"REMOVED  {addr} ({s['facility']}) asked not to be contacted")
            else:
                s["status"] = "replied"
                notes.append(f"REPLIED  {addr} ({s['facility']}) -- follow up personally!")

        typ, data = M.search(None, "FROM", '"mailer-daemon"', "SINCE", since)
        nums = data[0].split() if typ == "OK" and data and data[0] else []
        for num in nums:
            typ, msgdata = M.fetch(num, "(RFC822)")
            text = body_text(email.message_from_bytes(msgdata[0][1])).lower()
            for addr, s in active.items():
                if s["status"] == "active" and addr in text:
                    s["status"] = "bounced"
                    notes.append(f"BOUNCED  {addr} ({s['facility']}) -- address looks bad")
    finally:
        try:
            M.logout()
        except Exception:
            pass
    return notes


# ---------- main ----------

def is_due(entry, seq, today):
    step = entry["step"]
    if entry["status"] != "active" or step >= len(seq):
        return False
    if step == 0:
        return True
    return (today - date.fromisoformat(entry["last_sent"])).days >= seq[step]["wait_days"]


def show_status(state):
    counts = {}
    for s in state.values():
        key = s["status"] if s["status"] != "active" else f"active (sent {s['step']})"
        counts[key] = counts.get(key, 0) + 1
    print("\nContacts by status:")
    for k, v in sorted(counts.items()):
        print(f"  {k:<22} {v}")
    replied = [(a, s) for a, s in state.items() if s["status"] == "replied"]
    if replied:
        print("\nReplied (follow up personally):")
        for a, s in replied:
            print(f"  {s['facility']:<40} {a}")


def main():
    ap = argparse.ArgumentParser(description="Referral outreach bot")
    ap.add_argument("--dry-run", action="store_true", help="preview emails without sending")
    ap.add_argument("--status", action="store_true", help="show where every contact stands")
    args = ap.parse_args()

    cfg = load_config()
    seq = cfg["sequence"]
    snd = cfg["sending"]
    state = load_state()

    if args.status:
        show_status(state)
        return

    today = date.today()
    if snd.get("weekdays_only", True) and today.weekday() >= 5 and not args.dry_run:
        print("Weekend -- not sending today.")
        return

    # 1. Replies, removals and bounces
    if not args.dry_run:
        for note in check_inbox(cfg, state):
            print(note)
        save_state(state)

    # 2. Add any new leads
    dnc = load_dnc()
    leads = {l["email"]: l for l in load_leads()}
    for addr, lead in leads.items():
        if addr not in state:
            state[addr] = {"facility": lead.get("facility_name", ""), "step": 0,
                           "status": "dnc" if addr in dnc else "active",
                           "first_sent": None, "last_sent": None,
                           "first_subject": "", "message_ids": []}
        elif addr in dnc and state[addr]["status"] == "active":
            state[addr]["status"] = "dnc"

    # 3. Work out who is due: follow-ups first, then new contacts
    due = [a for a, s in state.items() if a in leads and is_due(s, seq, today)]
    due.sort(key=lambda a: -state[a]["step"])
    sent_today = sum(1 for s in state.values() if s["last_sent"] == today.isoformat())
    room = max(0, snd["daily_limit"] - sent_today)
    batch = due[:room]
    print(f"{len(due)} due, {sent_today} already sent today, sending {len(batch)}"
          + (" (DRY RUN)" if args.dry_run else ""))

    for i, addr in enumerate(batch):
        entry, lead = state[addr], leads[addr]
        step = entry["step"]
        subject, body = render(seq[step]["template"], lead, cfg)
        msg = build_message(cfg, lead, entry, subject, body)

        if args.dry_run:
            print("\n" + "=" * 70)
            print(f"To: {msg['To']}\nSubject: {msg['Subject']}  [email {step + 1} of {len(seq)}]\n")
            print(body)
            continue

        try:
            smtp_send(cfg, msg)
        except Exception as ex:
            print(f"FAILED   {addr}: {ex}")
            continue

        entry["step"] += 1
        entry["last_sent"] = today.isoformat()
        entry["message_ids"].append(msg["Message-ID"])
        if step == 0:
            entry["first_sent"] = today.isoformat()
            entry["first_subject"] = msg["Subject"]
        if entry["step"] >= len(seq):
            entry["status"] = "finished"
        save_state(state)
        log_send(addr, entry["facility"], entry["step"], msg["Subject"])
        print(f"SENT     {addr} ({entry['facility']}) email {entry['step']}/{len(seq)}")

        if i < len(batch) - 1:
            time.sleep(random.randint(snd["min_delay_seconds"], snd["max_delay_seconds"]))

    if not args.dry_run:
        save_state(state)
        show_status(state)


if __name__ == "__main__":
    main()
