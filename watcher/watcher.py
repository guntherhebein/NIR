#!/usr/bin/env python3
"""
Watcher-Prozess:
- Verbindet sich per SMB (\\10.139.13.121\Protocols) mit der Netzwerkfreigabe
- Beim allerersten Start: komplette Verzeichnisstruktur (alle Jahre/Tage) durchlaufen
- Danach: nur noch aktuelles Datum (+ Puffer von RECHECK_DAYS Tagen zurueck)
  jede POLL_INTERVAL_SECONDS Sekunden pruefen
- Neue PDFs werden heruntergeladen, lokal abgelegt (persistentes Volume /storage)
  und mitsamt Metadaten (Geraetenummer, laufende Messungsnummer, Messungsname,
  Datum, Dateigroesse, Pfade ...) in MongoDB gespeichert
- Erzeugt zusaetzlich ein PNG-Thumbnail der ersten PDF-Seite fuer die Vorschau
  in der Weboberflaeche
"""

import os
import re
import sys
import time
import logging
import traceback
from datetime import datetime, date, timedelta

import smbclient
from pymongo import MongoClient, ASCENDING
from pymongo.errors import DuplicateKeyError
from pdf2image import convert_from_path

# --------------------------------------------------------------------------- #
# Konfiguration ueber Umgebungsvariablen
# --------------------------------------------------------------------------- #
SMB_SERVER   = os.environ["SMB_SERVER"]
SMB_SHARE    = os.environ["SMB_SHARE"]
SMB_USERNAME = os.environ.get("SMB_USERNAME") or None
SMB_PASSWORD = os.environ.get("SMB_PASSWORD") or None
SMB_DOMAIN   = os.environ.get("SMB_DOMAIN") or None

MONGO_URI = os.environ["MONGO_URI"]
MONGO_DB  = os.environ.get("MONGO_DB", "protocol_archive")

POLL_INTERVAL_SECONDS = int(os.environ.get("POLL_INTERVAL_SECONDS", "60"))
RECHECK_DAYS          = int(os.environ.get("RECHECK_DAYS", "2"))  # Sicherheitspuffer um Mitternacht
STORAGE_PATH          = os.environ.get("STORAGE_PATH", "/storage")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("watcher")

# --------------------------------------------------------------------------- #
# Dateiname-Parser
# Beispiel: "2130000670 61 001073 Gruppe Triglyceride.pdf"
#   device = 2130000670   (Geraetenummer)
#   seq    = 61            (laufende Zahl der Messung)
#   code   = 001073        (Teil des Messungsnamens)
#   name   = Gruppe Triglyceride
# --------------------------------------------------------------------------- #
FILENAME_PATTERN = re.compile(
    r"^(?P<device>\d+)\s+(?P<seq>\d+)\s+(?P<code>\d+)\s+(?P<name>.+)\.pdf$",
    re.IGNORECASE,
)
YEAR_PATTERN = re.compile(r"^\d{4}$")
DATE_PATTERN = re.compile(r"^\d{8}$")


def smb_base_path() -> str:
    return rf"\\{SMB_SERVER}\{SMB_SHARE}"


def connect_smb():
    log.info("Verbinde zu SMB-Server %s (Share: %s) ...", SMB_SERVER, SMB_SHARE)
    smbclient.register_session(
        SMB_SERVER,
        username=SMB_USERNAME,
        password=SMB_PASSWORD,
        # domain wird bei smbprotocol ueber user='DOMAIN\\user' abgebildet,
        # falls SMB_DOMAIN gesetzt ist, hier automatisch voranstellen
    )


def list_dirs(path: str):
    """Liefert Liste von Verzeichnisnamen (nicht Dateien) unter path."""
    entries = []
    try:
        for entry in smbclient.scandir(path):
            if entry.is_dir():
                entries.append(entry.name)
    except Exception as exc:
        log.warning("Konnte Verzeichnis nicht lesen: %s (%s)", path, exc)
    return entries


def list_pdf_files(path: str):
    """Liefert Liste (name, smb_stat) fuer alle *.pdf Dateien in path."""
    files = []
    try:
        for entry in smbclient.scandir(path):
            if entry.is_file() and entry.name.lower().endswith(".pdf"):
                files.append(entry.name)
    except Exception as exc:
        log.warning("Konnte Verzeichnis nicht lesen: %s (%s)", path, exc)
    return files


def parse_filename(filename: str):
    """Zerlegt den Dateinamen in seine Bestandteile. Bei Fehlschlag: parse_error=True."""
    m = FILENAME_PATTERN.match(filename)
    if not m:
        return {
            "device_number": None,
            "measurement_seq": None,
            "measurement_code": None,
            "measurement_name": None,
            "full_measurement_label": None,
            "parse_error": True,
        }
    gd = m.groupdict()
    return {
        "device_number": gd["device"],
        "measurement_seq": gd["seq"],
        "measurement_code": gd["code"],
        "measurement_name": gd["name"].strip(),
        "full_measurement_label": f"{gd['code']} {gd['name'].strip()}",
        "parse_error": False,
    }


def ensure_indexes(col):
    col.create_index([("relative_path", ASCENDING)], unique=True, name="uniq_relative_path")
    col.create_index([("date", ASCENDING)], name="idx_date")
    col.create_index([("device_number", ASCENDING)], name="idx_device")
    col.create_index([("measurement_seq", ASCENDING)], name="idx_seq")
    col.create_index(
        [("measurement_name", "text"), ("filename", "text")],
        name="idx_text_search",
        default_language="german",
    )


def make_thumbnail(local_pdf_path: str, thumb_path: str):
    try:
        os.makedirs(os.path.dirname(thumb_path), exist_ok=True)
        pages = convert_from_path(local_pdf_path, dpi=80, first_page=1, last_page=1)
        if pages:
            pages[0].save(thumb_path, "PNG")
            return True
    except Exception as exc:
        log.warning("Thumbnail-Erzeugung fehlgeschlagen fuer %s: %s", local_pdf_path, exc)
    return False


def process_file(col, year: str, date_folder: str, filename: str):
    relative_path = f"{year}/{date_folder}/{filename}"
    remote_path = f"{smb_base_path()}\\{year}\\{date_folder}\\{filename}"

    # Existiert bereits (per relative_path) und wurde erfolgreich geladen? -> ueberspringen
    existing = col.find_one({"relative_path": relative_path})
    if existing and existing.get("downloaded"):
        return False  # nichts Neues

    try:
        remote_stat = smbclient.stat(remote_path)
    except Exception as exc:
        log.warning("Konnte Remote-Datei nicht stat'en: %s (%s)", remote_path, exc)
        return False

    try:
        parsed_date = datetime.strptime(date_folder, "%Y%m%d")
    except ValueError:
        parsed_date = None

    meta = parse_filename(filename)

    local_dir = os.path.join(STORAGE_PATH, year, date_folder)
    os.makedirs(local_dir, exist_ok=True)
    local_path = os.path.join(local_dir, filename)

    log.info("Lade neue Datei: %s", relative_path)
    try:
        with smbclient.open_file(remote_path, mode="rb") as remote_f:
            data = remote_f.read()
        with open(local_path, "wb") as local_f:
            local_f.write(data)
    except Exception as exc:
        log.error("Download fehlgeschlagen fuer %s: %s", relative_path, exc)
        return False

    thumb_rel = os.path.join(year, date_folder, ".thumbnails", filename[:-4] + ".png")
    thumb_abs = os.path.join(STORAGE_PATH, thumb_rel)
    thumb_ok = make_thumbnail(local_path, thumb_abs)

    doc = {
        "relative_path": relative_path,
        "filename": filename,
        "year": year,
        "date_folder": date_folder,
        "date": parsed_date,
        **meta,
        "file_size": getattr(remote_stat, "st_size", None),
        "local_path": local_path,
        "thumbnail_path": thumb_abs if thumb_ok else None,
        "downloaded": True,
        "downloaded_at": datetime.utcnow(),
    }

    try:
        col.update_one(
            {"relative_path": relative_path},
            {"$set": doc},
            upsert=True,
        )
    except DuplicateKeyError:
        pass

    return True


def scan_date_folder(col, year: str, date_folder: str) -> int:
    path = f"{smb_base_path()}\\{year}\\{date_folder}"
    count = 0
    for filename in list_pdf_files(path):
        try:
            if process_file(col, year, date_folder, filename):
                count += 1
        except Exception:
            log.error("Fehler bei Verarbeitung von %s/%s/%s:\n%s",
                       year, date_folder, filename, traceback.format_exc())
    return count


def full_scan(col) -> int:
    log.info("Starte vollstaendigen Erst-Scan aller Jahre/Tage ...")
    total = 0
    base = smb_base_path()
    for year in sorted(list_dirs(base)):
        if not YEAR_PATTERN.match(year):
            continue
        year_path = f"{base}\\{year}"
        for date_folder in sorted(list_dirs(year_path)):
            if not DATE_PATTERN.match(date_folder):
                continue
            total += scan_date_folder(col, year, date_folder)
    log.info("Erst-Scan abgeschlossen. %d neue Dateien verarbeitet.", total)
    return total


def incremental_scan(col) -> int:
    """Prueft nur die letzten RECHECK_DAYS Tage (Standard: heute + 1 Tag zurueck)."""
    total = 0
    today = date.today()
    for offset in range(RECHECK_DAYS):
        d = today - timedelta(days=offset)
        year = d.strftime("%Y")
        date_folder = d.strftime("%Y%m%d")
        total += scan_date_folder(col, year, date_folder)
    if total:
        log.info("Inkrementeller Scan: %d neue Dateien verarbeitet.", total)
    return total


def main():
    mongo = MongoClient(MONGO_URI)
    db = mongo[MONGO_DB]
    col = db["protocols"]
    meta_col = db["scanner_state"]

    ensure_indexes(col)

    connect_smb()

    state = meta_col.find_one({"_id": "scanner"}) or {}
    initial_scan_done = state.get("initial_scan_done", False)

    while True:
        try:
            connect_smb()  # Session ggf. erneuern (idempotent)
            if not initial_scan_done:
                full_scan(col)
                initial_scan_done = True
                meta_col.update_one(
                    {"_id": "scanner"},
                    {"$set": {"initial_scan_done": True, "last_full_scan": datetime.utcnow()}},
                    upsert=True,
                )
            else:
                incremental_scan(col)

            meta_col.update_one(
                {"_id": "scanner"},
                {"$set": {"last_run": datetime.utcnow()}},
                upsert=True,
            )

        except Exception:
            log.error("Unerwarteter Fehler im Scan-Zyklus:\n%s", traceback.format_exc())

        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()