#!/usr/bin/env python3
"""
Flask-Webanwendung zur Suche/Anzeige der archivierten Mess-Protokolle.

Endpunkte:
  GET  /                     -> HTML-Suchoberflaeche
  GET  /api/search           -> JSON-Suchergebnisse (Filter: date_from, date_to,
                                 device, seq, name)
  GET  /pdf/<id>             -> Liefert das Original-PDF (inline, fuer Vorschau/Druck)
  GET  /thumbnail/<id>       -> Liefert das Vorschaubild (PNG) der ersten Seite
  POST /api/rescan           -> Erzwingt beim naechsten Watcher-Zyklus einen kompletten Re-Scan
"""

import os
from datetime import datetime

from flask import Flask, jsonify, request, render_template, send_file, abort
from pymongo import MongoClient, DESCENDING
from bson import ObjectId
from bson.errors import InvalidId

MONGO_URI = os.environ["MONGO_URI"]
MONGO_DB = os.environ.get("MONGO_DB", "protocol_archive")

app = Flask(__name__)
client = MongoClient(MONGO_URI)
db = client[MONGO_DB]
col = db["protocols"]
meta_col = db["scanner_state"]


def doc_to_json(doc):
    return {
        "id": str(doc["_id"]),
        "filename": doc.get("filename"),
        "year": doc.get("year"),
        "date_folder": doc.get("date_folder"),
        "date": doc["date"].strftime("%Y-%m-%d") if doc.get("date") else None,
        "device_number": doc.get("device_number"),
        "measurement_seq": doc.get("measurement_seq"),
        "measurement_code": doc.get("measurement_code"),
        "measurement_name": doc.get("measurement_name"),
        "full_measurement_label": doc.get("full_measurement_label"),
        "file_size": doc.get("file_size"),
        "has_thumbnail": bool(doc.get("thumbnail_path")),
        "parse_error": doc.get("parse_error", False),
    }


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/search")
def api_search():
    query = {}

    date_from = request.args.get("date_from", "").strip()
    date_to = request.args.get("date_to", "").strip()
    device = request.args.get("device", "").strip()
    seq = request.args.get("seq", "").strip()
    name = request.args.get("name", "").strip()

    date_filter = {}
    if date_from:
        try:
            date_filter["$gte"] = datetime.strptime(date_from, "%Y-%m-%d")
        except ValueError:
            pass
    if date_to:
        try:
            date_filter["$lte"] = datetime.strptime(date_to, "%Y-%m-%d")
        except ValueError:
            pass
    if date_filter:
        query["date"] = date_filter

    if device:
        query["device_number"] = {"$regex": device, "$options": "i"}

    if seq:
        query["measurement_seq"] = {"$regex": f"^{seq}$", "$options": "i"}

    if name:
        # Suche in Messungsname, Code UND Dateiname (Freitext-Teilstring, case-insensitive)
        query["$or"] = [
            {"measurement_name": {"$regex": name, "$options": "i"}},
            {"measurement_code": {"$regex": name, "$options": "i"}},
            {"filename": {"$regex": name, "$options": "i"}},
        ]

    limit = min(int(request.args.get("limit", 200)), 1000)

    cursor = col.find(query).sort([("date", DESCENDING), ("filename", DESCENDING)]).limit(limit)
    results = [doc_to_json(d) for d in cursor]
    return jsonify({"count": len(results), "results": results})


def _get_doc_or_404(doc_id):
    try:
        oid = ObjectId(doc_id)
    except InvalidId:
        abort(404)
    doc = col.find_one({"_id": oid})
    if not doc:
        abort(404)
    return doc


@app.route("/pdf/<doc_id>")
def get_pdf(doc_id):
    doc = _get_doc_or_404(doc_id)
    local_path = doc.get("local_path")
    if not local_path or not os.path.exists(local_path):
        abort(404)
    return send_file(
        local_path,
        mimetype="application/pdf",
        as_attachment=False,
        download_name=doc.get("filename", "protokoll.pdf"),
    )


@app.route("/thumbnail/<doc_id>")
def get_thumbnail(doc_id):
    doc = _get_doc_or_404(doc_id)
    thumb_path = doc.get("thumbnail_path")
    if not thumb_path or not os.path.exists(thumb_path):
        abort(404)
    return send_file(thumb_path, mimetype="image/png")


@app.route("/api/rescan", methods=["POST"])
def api_rescan():
    meta_col.update_one(
        {"_id": "scanner"},
        {"$set": {"initial_scan_done": False}},
        upsert=True,
    )
    return jsonify({"status": "ok", "message": "Voller Re-Scan wird beim naechsten Watcher-Zyklus ausgefuehrt."})


@app.route("/api/status")
def api_status():
    state = meta_col.find_one({"_id": "scanner"}) or {}
    total_docs = col.count_documents({})
    return jsonify({
        "initial_scan_done": state.get("initial_scan_done", False),
        "last_run": state.get("last_run").isoformat() if state.get("last_run") else None,
        "total_documents": total_docs,
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)