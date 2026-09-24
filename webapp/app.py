#!/usr/bin/env python3
"""
Flask-Webanwendung zur Suche/Anzeige der archivierten Mess-Protokolle.

Endpunkte:
  GET  /                     -> HTML-Suchoberflaeche
  GET  /api/search           -> JSON-Suchergebnisse
  GET  /pdf/<id>             -> Liefert das Original-PDF (inline, fuer Vorschau/Druck)
  GET  /thumbnail/<id>       -> Liefert das Vorschaubild (PNG) der ersten Seite
  POST /api/rescan           -> Erzwingt beim naechsten Watcher-Zyklus einen kompletten Re-Scan
  GET  /api/status           -> Status-Info (letzter Lauf, Anzahl Dokumente, ...)
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

def build_query(args):
    """Baut aus den Request-Parametern die MongoDB-Query. Keine Parameter -> {} (alle Dokumente)."""
    query = {}
    # ... (Filter wie gehabt) ...
    return query

def doc_to_json(doc):
    return {
        "id": str(doc["_id"]),
        "filename": doc.get("filename"),
        "year": doc.get("year"),
        "date_folder": doc.get("date_folder"),
        "date": doc["date"].strftime("%Y-%m-%d") if doc.get("date") else None,
        "measurement_datetime": (
            doc["measurement_datetime"].strftime("%Y-%m-%d %H:%M:%S")
            if doc.get("measurement_datetime") else doc.get("measurement_datetime_str")
        ),
        "device_number": doc.get("device_number"),
        "measurement_seq": doc.get("measurement_seq"),
        "measurement_code": doc.get("measurement_code"),
        "measurement_name": doc.get("measurement_name"),
        "full_measurement_label": doc.get("full_measurement_label"),
        "file_size": doc.get("file_size"),
        "has_thumbnail": bool(doc.get("thumbnail_path")),
        "parse_error": doc.get("parse_error", False),
        # --- aus dem PDF-Inhalt extrahierte Zusatzfelder ---
        "benutzer": doc.get("benutzer"),
        "apotheke": doc.get("apotheke"),
        "pruefergebnis": doc.get("pruefergebnis"),
        "stoffklasse": doc.get("stoffklasse"),
        "original_stoffklasse": doc.get("original_stoffklasse"),
        "stoffklassenvalidierung": doc.get("stoffklassenvalidierung"),
        "konformitaetsindex": doc.get("konformitaetsindex"),
        "korrelation": doc.get("korrelation"),
        "geraete_id_pdf": doc.get("geraete_id_pdf"),
        "protokoll_version": doc.get("protokoll_version"),
        "software_version": doc.get("software_version"),
        "datenbank_version": doc.get("datenbank_version"),
        "bemerkung": doc.get("bemerkung"),
        "naechste_nachbarn": doc.get("naechste_nachbarn", []),
    }


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/search")
def api_search():
    query = build_query(request.args)

    skip = max(int(request.args.get("skip", 0)), 0)
    limit = min(max(int(request.args.get("limit", DEFAULT_PAGE_SIZE)), 1), MAX_PAGE_SIZE)

    total_matching = col.count_documents(query)

    cursor = (
        col.find(query)
        .sort([("date", DESCENDING), ("filename", DESCENDING)])
        .skip(skip)
        .limit(limit)
    )
    results = [doc_to_json(d) for d in cursor]

    return jsonify({
        "count": len(results),
        "total_matching": total_matching,
        "skip": skip,
        "limit": limit,
        "results": results,
    })


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
        "status": state.get("status", "unknown"),
        "phase": state.get("phase"),
        "detail": state.get("detail"),
        "current_year": state.get("current_year"),
        "current_date_folder": state.get("current_date_folder"),
        "current_file": state.get("current_file"),
        "total_folders": state.get("total_folders"),
        "folders_done": state.get("folders_done"),
        "new_files_this_run": state.get("new_files_this_run"),
        "last_run": _iso(state.get("last_run")),
        "next_run": _iso(state.get("next_run")),
        "last_full_scan": _iso(state.get("last_full_scan")),
        "updated_at": _iso(state.get("updated_at")),
        "last_error": state.get("last_error"),
        "total_documents": total_docs,
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)