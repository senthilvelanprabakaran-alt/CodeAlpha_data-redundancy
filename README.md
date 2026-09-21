# DataGuard — Data Redundancy Removal System

A Flask + Cloud Firestore web application for validating records, blocking exact contact duplicates, flagging possible same-name matches for manual review, and importing CSV files.

## Features
- Web form and CSV upload.
- Required name and at least one contact field (email or phone).
- Normalizes email case/spacing and strips punctuation from phone numbers.
- Atomic Firestore transaction creates the record and its uniqueness keys together, preventing two concurrent requests with the same email/phone from both inserting.
- Exact contact duplicates are blocked.
- Same normalized name with different contact details is flagged in `review_queue` for human review; it is not treated as a confirmed duplicate.
- CSV import downloads a per-row result report.
- Basic dashboard and health endpoint.

## 1. Create Firebase / Firestore
1. Open the [Firebase Console](https://console.firebase.google.com/) and create/select a project.
2. In **Build → Firestore Database**, create a database. Choose a location suitable for your application.
3. Open **Project settings → Service accounts** and generate a private key for a service account.
4. Save the downloaded JSON as `serviceAccountKey.json` in this project folder for local development.
5. Do not commit this JSON or expose it in frontend code. The Flask server uses the privileged Admin SDK. Firebase documents the Python setup and service-account initialization in its [Admin SDK setup guide](https://firebase.google.com/docs/admin/setup).

## 2. Install locally (Windows PowerShell)
```powershell
py -m venv .venv
.\\.venv\\Scripts\\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```
Edit `.env` and set `GOOGLE_APPLICATION_CREDENTIALS` to the correct path. If the key is in the project directory, use:
```text
GOOGLE_APPLICATION_CREDENTIALS=./serviceAccountKey.json
```
If PowerShell blocks activation, run `Set-ExecutionPolicy -Scope Process Bypass` in that terminal and activate again.

## 3. Run
```powershell
python app.py
```
Open http://127.0.0.1:5000

For production, set a strong `FLASK_SECRET_KEY`, set `FLASK_DEBUG=false`, and run with a production WSGI server, for example:
```bash
gunicorn app:app
```

## 4. CSV format
Download `records_template.csv` from the UI. Required headers:
```csv
name,email,phone,notes
Example Person,person@example.com,9876543210,Example only
```
Email and phone may be blank individually, but at least one must be provided.

## Firestore collections
- `records`: accepted, verified records.
- `dedupe_keys`: SHA-256-derived email/phone key documents pointing to a canonical record ID.
- `review_queue`: possible name matches requiring human review.

## Important design notes
- Email and phone are considered exact identity keys after normalization. Shared family phone numbers or shared email addresses can create false positives; adjust the policy to your domain.
- Similar names are only review signals, not proof of duplication. A production version should add a review interface and domain-specific matching rules.
- Dashboard counts stream the collections and are intended for a small internship demo. For large datasets, use maintained aggregate counters or scheduled aggregation instead.
- Add authentication, CSRF protection, rate limiting, structured audit logs, stricter validation, and access controls before exposing this app publicly.
- Firestore security rules do not restrict Admin SDK access. Protect the Flask server and its service-account credentials accordingly.
"# CodeAlpha_data-redundancy" 
"# CodeAlpha_data-redundancy" 
