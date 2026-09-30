# Referral Outreach Bot

This bot emails assisted living facilities, group homes, nursing homes and senior
housing in the Twin Cities to let them know you're taking new home health clients.

- **Email 1:** a short introduction
- **Email 2:** a follow-up 4 days later, in the same email thread
- **Email 3:** a brief last note 7 days after that

It stops emailing anyone who replies, so you can take the conversation from there
yourself. If someone replies "remove", they go on a do-not-contact list and are never
emailed again. It sends at most 30 emails a day, on weekdays only, with a 1–3 minute
pause between emails so your account doesn't get flagged as spam.

---

## One-time setup (about 20 minutes)

### 1. Install Python
If `python3 --version` doesn't work in a terminal, install Python 3 from python.org.
Nothing else needs installing.

### 2. Fill in `config.json`
Replace everything under `"company"` with your agency's real details. **The mailing
address has to be real.** US law (CAN-SPAM) requires a physical postal address in
business emails. A PO box is fine.

Under `"email"`, put the address you'll send from. The settings are already filled in
for Gmail and Google Workspace. For Outlook/Microsoft 365, use:

```
"smtp_host": "smtp.office365.com", "smtp_port": 587, "smtp_starttls": true,
"imap_host": "outlook.office365.com", "imap_port": 993
```

### 3. Create an app password (Gmail / Google Workspace)
1. Turn on 2-Step Verification for the account.
2. Go to https://myaccount.google.com/apppasswords and create one named "Outreach bot".
3. Copy the 16-character password it shows you.

Then set it in the terminal before you run the bot:

- Mac/Linux: `export OUTREACH_EMAIL_PASSWORD="abcd efgh ijkl mnop"`
- Windows (PowerShell): `$env:OUTREACH_EMAIL_PASSWORD="abcd efgh ijkl mnop"`

Never put this password inside `config.json`.

### 4. Build your list in `leads.csv`
Copy `leads_template.csv` to `leads.csv` and add one row per facility. Only the
`email` column is required, but a first name and facility name make the emails
much more personal.

Good places to find facilities and contacts:

- **Minnesota Department of Health** directory of licensed providers (assisted living,
  nursing homes, home care): https://www.health.state.mn.us/facilities/regulation/directory/
- **MN DHS License Lookup** for adult foster care and community residential settings
  (group homes): https://licensinglookup.dhs.state.mn.us/
- The facility's own website ("Contact", "Admissions", or "Our Team" page)
- Google Maps searches like "assisted living Maple Grove" or "group home Burnsville"

Who to email, best to least: Director of Nursing / Health Services Director →
Administrator / Executive Director → Social Worker or Discharge Planner →
Program Director (group homes) → general info@ address.

Only use business addresses facilities publish for professional contact. Don't
buy email lists; they bounce a lot and can get your account blocked.

### 5. Edit the emails
The three emails are in the `templates` folder. Change the wording to sound like you
and your sisters, and make sure every service mentioned is one you actually provide
and are licensed for. Words in `{curly_braces}` are filled in automatically.
Leave the `Subject:` line blank on emails 2 and 3 so they arrive as replies in the
same thread.

---

## Running it

**Always preview first.** This prints every email without sending anything:

```
python3 outreach_bot.py --dry-run
```

When it looks right, send for real:

```
python3 outreach_bot.py
```

Check where everyone stands:

```
python3 outreach_bot.py --status
```

Run it once each weekday. It keeps track of who's due for what, so running it
twice in one day won't double-send.

### Make it run by itself every weekday morning

**Don't put the app password directly in the crontab line or Task Scheduler
command** -- anyone who can read that job (or `ps` the running process) sees
the password in plain text. Keep it in a file instead:

- **Mac/Linux:**
  1. Create a file just for this, e.g. `~/.outreach_env`, containing one line:
     ```
     export OUTREACH_EMAIL_PASSWORD="abcd efgh ijkl mnop"
     ```
  2. Lock it down so only you can read it: `chmod 600 ~/.outreach_env`
  3. Run `crontab -e` and add (change the folder path):
     ```
     0 9 * * 1-5 cd /path/to/outreach-bot && source ~/.outreach_env && /usr/bin/python3 outreach_bot.py >> bot.log 2>&1
     ```
- **Windows:** set `OUTREACH_EMAIL_PASSWORD` as a user environment variable
  in System Settings → Environment Variables (this already keeps it out of
  the visible task command). Then open Task Scheduler → Create Basic Task →
  Daily at 9:00 AM → Start a program: `python`, arguments `outreach_bot.py`,
  start in: the bot's folder.

The computer needs to be on at that time.

---

## When someone replies

The bot marks them "replied" and stops the sequence, but **it does not answer for
you**. Check `--status` (or just your inbox) and call or email them back personally,
ideally the same day. That's where the clients come from.

## Files the bot creates

| File | What it is |
|---|---|
| `state.json` | Where each contact is in the sequence. Don't delete it or the bot starts over. |
| `sent_log.csv` | Every email sent, with date and time. |
| `do_not_contact.csv` | Everyone who asked to be removed. You can add addresses by hand too. |

## Staying safe and compliant

- **No client information in these emails, ever.** Don't mention past or current
  clients by name or details (HIPAA). The templates are written to avoid this.
- Keep the real mailing address and the "reply remove" line in every email (CAN-SPAM).
  The bot adds both automatically.
- Honor removal requests. The bot does this, but if someone phones or emails you
  asking to stop, add them to `do_not_contact.csv`.
- Start slow. `daily_limit` is set to 12 in `config.json` for the first couple of
  weeks on a new or rarely used account. Once replies and bounces look normal,
  you can raise it (30 is a reasonable ceiling for an established account).
- **Test it on yourself first.** Add your own email address as a row in
  `leads.csv`, run the bot for real (not `--dry-run`), and reply "remove" to
  one of the emails to confirm the opt-out and do-not-contact logic works
  before you point it at real facility contacts.
- Using your company domain (you@yourcompany.com) instead of a free Gmail address
  looks more professional and lands in inboxes more reliably.
