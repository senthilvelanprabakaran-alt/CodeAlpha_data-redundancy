import csv
import hashlib
import io
import os
from datetime import datetime, timezone

import firebase_admin
from dotenv import load_dotenv
from firebase_admin import credentials, firestore, initialize_app
from flask import Flask, flash, redirect, render_template, request, send_file, url_for

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "change-this-before-deployment")
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 5 MB upload limit

ALLOWED_EXTENSIONS = {"csv"}


def init_firestore():
    """Initialize Firebase Admin SDK using a service-account file or ADC."""
    if not firebase_admin._apps:
        service_account_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        if service_account_path:
            initialize_app(credentials.Certificate(service_account_path))
        else:
            # On Google Cloud, Application Default Credentials can be used.
            initialize_app()
    return firestore.client()


db = init_firestore()
records = db.collection("records")
dedupe_keys = db.collection("dedupe_keys")
review_queue = db.collection("review_queue")


def normalize(value):
    """Normalize whitespace and case for matching."""
    return " ".join((value or "").strip().lower().split())


def normalize_email(value):
    return normalize(value)


def normalize_phone(value):
    """Keep digits only so formatting differences don't bypass matching."""
    return "".join(ch for ch in (value or "") if ch.isdigit())


def validate_and_prepare(raw):
    name = (raw.get("name") or "").strip()
    email = normalize_email(raw.get("email"))
    phone = normalize_phone(raw.get("phone"))
    notes = (raw.get("notes") or "").strip()

    if not name:
        return None, "Name is required."
    if not email and not phone:
        return None, "Provide an email address or phone number for duplicate checking."
    if email and ("@" not in email or email.startswith("@") or email.endswith("@")):
        return None, "Email address does not look valid."
    if phone and len(phone) < 7:
        return None, "Phone number must contain at least 7 digits."

    return {
        "name": name,
        "name_normalized": normalize(name),
        "email": email,
        "phone": phone,
        "notes": notes,
    }, None


def key_ids(data):
    """Return safe, stable Firestore document IDs for contact uniqueness keys."""
    keys = []
    if data["email"]:
        keys.append("email_" + data["email"])
    if data["phone"]:
        keys.append("phone_" + data["phone"])
    return [hashlib.sha256(key.encode("utf-8")).hexdigest() for key in keys]


def find_possible_name_match(data):
    """
    Flag a same-name/different-contact record for manual review.
    Exact contact duplicates are handled by the atomic uniqueness transaction.
    """
    query = records.where("name_normalized", "==", data["name_normalized"]).limit(5)

    for doc in query.stream():
        old = doc.to_dict() or {}
        same_email = bool(data["email"] and old.get("email") == data["email"])
        same_phone = bool(data["phone"] and old.get("phone") == data["phone"])

        if same_email or same_phone:
            continue
        return doc.id

    return None


def save_unique(data):
    key_refs = [dedupe_keys.document(key) for key in key_ids(data)]
    record_ref = records.document()
    transaction = db.transaction()

    @firestore.transactional
    def commit(txn):
        # transaction.get(reference) returns an iterator in the Firestore
        # Python SDK. Retrieve each DocumentSnapshot before checking .exists.
        snapshots = []
        for ref in key_refs:
            snapshot = next(txn.get(ref), None)
            if snapshot is not None:
                snapshots.append(snapshot)

        existing_record_ids = []
        for snapshot in snapshots:
            if snapshot.exists:
                linked_id = (snapshot.to_dict() or {}).get("record_id")
                if linked_id:
                    existing_record_ids.append(linked_id)

        if existing_record_ids:
            return {
                "status": "duplicate",
                "record_id": existing_record_ids[0],
            }

        now = datetime.now(timezone.utc).isoformat()
        record_data = {
            **data,
            "created_at": now,
            "status": "verified",
        }

        # All reads occur before writes, as required by Firestore transactions.
        txn.create(record_ref, record_data)
        for ref in key_refs:
            txn.create(ref, {"record_id": record_ref.id, "created_at": now})

        return {"status": "inserted", "record_id": record_ref.id}

    return commit(transaction)


def process_record(raw):
    data, error = validate_and_prepare(raw)
    if error:
        return {"status": "invalid", "message": error}

    # The transaction remains the authority for exact email/phone duplicates.
    # This name-based check only flags a possible match for human review.
    possible_id = find_possible_name_match(data)
    if possible_id:
        review_ref = review_queue.document()
        review_ref.set({
            **data,
            "possible_match_record_id": possible_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "needs_review",
        })
        return {
            "status": "needs_review",
            "record_id": possible_id,
            "message": "Similar name found; sent for manual review.",
        }

    try:
        return save_unique(data)
    except Exception:
        app.logger.exception("Firestore write failed")
        return {
            "status": "error",
            "message": "Database operation failed. Check server logs and Firebase configuration.",
        }


@app.route("/")
def index():
    total = sum(1 for _ in records.stream())
    key_count = sum(1 for _ in dedupe_keys.stream())
    pending = sum(
        1 for _ in review_queue.where("status", "==", "needs_review").stream()
    )
    recent = records.order_by(
        "created_at", direction=firestore.Query.DESCENDING
    ).limit(8).stream()

    return render_template(
        "index.html",
        total=total,
        key_count=key_count,
        pending=pending,
        recent=[{"id": doc.id, **(doc.to_dict() or {})} for doc in recent],
    )


@app.route("/add", methods=["POST"])
def add_record():
    result = process_record(request.form)

    if result["status"] == "inserted":
        flash("Verified unique record saved successfully.", "success")
    elif result["status"] == "duplicate":
        flash(
            f"Duplicate blocked. Existing record: {result.get('record_id', 'available')}",
            "warning",
        )
    elif result["status"] == "needs_review":
        flash("Possible match detected and sent to the review queue.", "warning")
    else:
        flash(result.get("message", "Could not process this record."), "danger")

    return redirect(url_for("index"))


def csv_template():
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=["name", "email", "phone", "notes"])
    writer.writeheader()
    writer.writerow({
        "name": "Example Person",
        "email": "person@example.com",
        "phone": "9876543210",
        "notes": "Example only",
    })
    return output.getvalue()


@app.route("/download-template")
def download_template():
    return send_file(
        io.BytesIO(csv_template().encode("utf-8")),
        mimetype="text/csv",
        as_attachment=True,
        download_name="records_template.csv",
    )


@app.route("/upload", methods=["POST"])
def upload_csv():
    uploaded_file = request.files.get("file")

    if not uploaded_file or not uploaded_file.filename:
        flash("Choose a CSV file first.", "danger")
        return redirect(url_for("index"))

    if not uploaded_file.filename.lower().endswith(".csv"):
        flash("Only .csv files are accepted.", "danger")
        return redirect(url_for("index"))

    try:
        text = uploaded_file.stream.read().decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        required = {"name", "email", "phone", "notes"}

        if not reader.fieldnames or not required.issubset(
            {header.strip().lower() for header in reader.fieldnames if header}
        ):
            flash("CSV headers must include: name, email, phone, notes.", "danger")
            return redirect(url_for("index"))

        # Map headers case-insensitively and trim surrounding whitespace.
        header_map = {
            header.strip().lower(): header
            for header in reader.fieldnames
            if header
        }

        counts = {
            "inserted": 0,
            "duplicate": 0,
            "needs_review": 0,
            "invalid": 0,
            "error": 0,
        }
        report_rows = []

        for row_number, row in enumerate(reader, start=2):
            normalized_row = {
                key: row.get(original, "") or ""
                for key, original in header_map.items()
            }
            result = process_record(normalized_row)
            status = result.get("status", "error")
            counts[status] = counts.get(status, 0) + 1

            report_rows.append({
                "row": row_number,
                "name": normalized_row.get("name", ""),
                "status": status,
                "message": result.get("message", result.get("record_id", "")),
            })

        output = io.StringIO()
        writer = csv.DictWriter(
            output,
            fieldnames=["row", "name", "status", "message"],
        )
        writer.writeheader()
        writer.writerows(report_rows)

        response = send_file(
            io.BytesIO(output.getvalue().encode("utf-8")),
            mimetype="text/csv",
            as_attachment=True,
            download_name="import_results.csv",
        )
        response.headers["X-Import-Summary"] = ",".join(
            f"{key}:{value}" for key, value in counts.items()
        )
        return response

    except UnicodeDecodeError:
        flash("CSV must be UTF-8 encoded.", "danger")
        return redirect(url_for("index"))
    except Exception:
        app.logger.exception("CSV import failed")
        flash("CSV import failed. Check the file and server logs.", "danger")
        return redirect(url_for("index"))


@app.route("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    app.run(debug=os.getenv("FLASK_DEBUG", "false").lower() == "true")
