#!/usr/bin/env python3
"""
Watcher-Prozess:
- Liest die per HTTP erreichbare Verzeichnisliste (Apache/nginx Autoindex-Stil)
  unter http://10.139.13.121/Protocols/ aus
- Struktur: <BASE_URL>/<Jahr>/<JJJJMMTT>/<Datei>.pdf
- Beim allerersten Start: komplette Verzeichnisstruktur (alle Jahre/Tage) durchlaufen
- Danach: nur noch aktuelles Datum (+ Puffer von RECHECK_DAYS Tagen zurueck)
  jede POLL_INTERVAL_SECONDS Sekunden pruefen
- Neue PDFs werden heruntergeladen, lokal abgelegt (persistentes Volume /storage),
  ein Thumbnail der ersten Seite erzeugt, der PDF-Text-Inhalt strukturiert
  ausgelesen (siehe pdf_parser.py) und alles zusammen in MongoDB gespeichert
"""

import os
import re
import time
import logging
import traceback
from datetime import datetime, date, timedelta
from urllib.parse import urljoin, unquote

import requests
from requests.auth import HTTPBasicAuth
from bs4 import BeautifulSoup
from pymongo import MongoClient, ASCENDING
from pymongo.errors import DuplicateKeyError
from pdf2image import convert_from_path

from pdf_parser import extract_pdf_metadata

# --------------------------------------------------------------------------- #
# Konfiguration ueber Umgebungsvariablen
# --------------------------------------------------------------------------- #
BASE_URL = os.environ.get("BASE_URL", "http://10.139.13.121/Protocols/").rstrip("/") + "/"
HTTP_AUTH_USER = os.environ.get("HTTP_AUTH_USER") or None
HTTP_AUTH_PASS = os.environ.get("HTTP_AUTH_PASS") or None
HTTP_VERIFY_SSL = os.environ.get("HTTP_VERIFY_SSL", "true").lower() not in ("0", "false", "no")
HTTP_TIMEOUT = int(os.environ.get("HTTP_TIMEOUT_SECONDS", "30"))

MONGO_URI = os.environ["MONGO_URI"]
MONGO_DB = os.environ.get("MONGO_DB", "protocol_archive")

POLL_INTERVAL_SECONDS = int(os.environ.get("POLL_INTERVAL_SECONDS", "60"))
RECHECK_DAYS = int(os.environ.get("RECHECK_DAYS", "2"))  # Sicherheitspuffer um Mitternacht
STORAGE_PATH = os.environ.get("STORAGE_PATH", "/storage")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("watcher")

session = requests.Session()
if HTTP_AUTH_USER:
    session.auth = HTTPBasicAuth(HTTP_AUTH_USER, HTTP_AUTH_PASS or "")

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

def update_status(meta_col, **fields):
    """Schreibt/aktualisiert einzelne Felder im Status-Dokument 'scanner'."""
    fields["updated_at"] = datetime.utcnow()
    meta_col.update_one({"_id": "scanner"}, {"$set": fields}, upsert=True)

    def scan_date_folder(col, year: str, date_folder: str, meta_col=None) -> int:
    url = f"{BASE_URL}{year}/{date_folder}/"
    count = 0
    files = list_pdf_files(url)
    for filename, file_url in files:
        if meta_col is not None:
            update_status(meta_col, current_file=filename)
        try:
            if process_file(col, year, date_folder, filename, file_url):
                count += 1
        except Exception:
            log.error("Fehler bei Verarbeitung von %s/%s/%s:\n%s",
                       year, date_folder, filename, traceback.format_exc())
    if meta_col is not None:
        update_status(meta_col, current_file=None)
    return count

def scan_date_folder(col, year: str, date_folder: str, meta_col=None) -> int:
    url = f"{BASE_URL}{year}/{date_folder}/"
    count = 0
    files = list_pdf_files(url)
    for filename, file_url in files:
        if meta_col is not None:
            update_status(meta_col, current_file=filename)
        try:
            if process_file(col, year, date_folder, filename, file_url):
                count += 1
        except Exception:
            log.error("Fehler bei Verarbeitung von %s/%s/%s:\n%s",
                       year, date_folder, filename, traceback.format_exc())
    if meta_col is not None:
        update_status(meta_col, current_file=None)
    return count

def full_scan(col, meta_col) -> int:
    log.info("Starte vollstaendigen Erst-Scan aller Jahre/Tage unter %s ...", BASE_URL)
    update_status(
        meta_col,
        phase="initial_scan",
        status="running",
        detail="Ermittle Verzeichnisstruktur (Jahre/Tage) ...",
        current_year=None,
        current_date_folder=None,
        current_file=None,
    )

    targets = []
    for year in sorted(list_dirs(BASE_URL)):
        if not YEAR_PATTERN.match(year):
            continue
        year_url = f"{BASE_URL}{year}/"
        for date_folder in sorted(list_dirs(year_url)):
            if not DATE_PATTERN.match(date_folder):
                continue
            targets.append((year, date_folder))

    total_folders = len(targets)
    update_status(meta_col, total_folders=total_folders, folders_done=0, new_files_this_run=0)

    total_new = 0
    for idx, (year, date_folder) in enumerate(targets, start=1):
        update_status(
            meta_col,
            current_year=year,
            current_date_folder=date_folder,
            detail=f"Erst-Scan: {year}/{date_folder} (Ordner {idx}/{total_folders})",
        )
        total_new += scan_date_folder(col, year, date_folder, meta_col=meta_col)
        update_status(meta_col, folders_done=idx, new_files_this_run=total_new)

    update_status(
        meta_col,
        detail=f"Erst-Scan abgeschlossen ({total_new} neue Datei(en))",
        current_year=None, current_date_folder=None, current_file=None,
    )
    log.info("Erst-Scan abgeschlossen. %d neue Dateien verarbeitet.", total_new)
    return total_new

def incremental_scan(col, meta_col) -> int:
    update_status(
        meta_col, phase="incremental_scan", status="running",
        detail="Pruefe aktuelle(s) Datum/Tage auf neue Dateien ...", new_files_this_run=0,
    )
    total = 0
    today = date.today()
    for offset in range(RECHECK_DAYS):
        d = today - timedelta(days=offset)
        year = d.strftime("%Y")
        date_folder = d.strftime("%Y%m%d")
        update_status(
            meta_col, current_year=year, current_date_folder=date_folder,
            detail=f"Pruefe {year}/{date_folder} auf neue Dateien ...",
        )
        total += scan_date_folder(col, year, date_folder, meta_col=meta_col)
        update_status(meta_col, new_files_this_run=total)

    update_status(
        meta_col, detail=f"Letzter Scan abgeschlossen ({total} neue Datei(en))",
        current_year=None, current_date_folder=None, current_file=None,
    )
    return total



def parse_filename(filename: str):
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


# --------------------------------------------------------------------------- #
# HTTP-Verzeichnis-Crawling (Apache/nginx Autoindex)
# --------------------------------------------------------------------------- #
def fetch_listing(url: str):
    """Liefert Liste von (name_decoded, absolute_url, is_dir) fuer ein Verzeichnis."""
    try:
        resp = session.get(url, timeout=HTTP_TIMEOUT, verify=HTTP_VERIFY_SSL)
        resp.raise_for_status()
    except Exception as exc:
        log.warning("Konnte Verzeichnis nicht laden: %s (%s)", url, exc)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    entries = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href or href in ("../", "./") or href.startswith("?") or href.startswith("#"):
            continue
        if href.startswith("http://") or href.startswith("https://"):
            # absolute Links auf andere Hosts/Pfade ignorieren
            if not href.startswith(BASE_URL):
                continue
        absolute_url = urljoin(url, href)
        # 'Parent Directory'-Links (zeigen auf das aktuelle oder ein uebergeordnetes
        # Verzeichnis) sowie Selbstverweise ausschliessen
        if absolute_url.rstrip("/") == url.rstrip("/"):
            continue
        if not absolute_url.startswith(url):
            continue
        name_decoded = unquote(href.rstrip("/").split("/")[-1])
        if not name_decoded:
            continue
        is_dir = href.endswith("/")
        entries.append((name_decoded, absolute_url, is_dir))
    return entries


def list_dirs(url: str):
    return [name for name, _, is_dir in fetch_listing(url) if is_dir]


def list_pdf_files(url: str):
    """Liefert Liste (filename_decoded, absolute_url) aller *.pdf in einem Verzeichnis."""
    return [
        (name, abs_url)
        for name, abs_url, is_dir in fetch_listing(url)
        if not is_dir and name.lower().endswith(".pdf")
    ]


# --------------------------------------------------------------------------- #
# MongoDB
# --------------------------------------------------------------------------- #
def ensure_indexes(col):
    col.create_index([("relative_path", ASCENDING)], unique=True, name="uniq_relative_path")
    col.create_index([("date", ASCENDING)], name="idx_date")
    col.create_index([("device_number", ASCENDING)], name="idx_device")
    col.create_index([("measurement_seq", ASCENDING)], name="idx_seq")
    col.create_index([("benutzer", ASCENDING)], name="idx_benutzer")
    col.create_index([("apotheke", ASCENDING)], name="idx_apotheke")
    col.create_index([("pruefergebnis", ASCENDING)], name="idx_pruefergebnis")
    col.create_index([("stoffklassenvalidierung", ASCENDING)], name="idx_validierung")
    col.create_index(
        [("measurement_name", "text"), ("filename", "text"), ("apotheke", "text"), ("benutzer", "text")],
        name="idx_text_search",
        default_language="german",
    )


def make_thumbnail(local_pdf_path: str, thumb_path: str) -> bool:
    try:
        os.makedirs(os.path.dirname(thumb_path), exist_ok=True)
        pages = convert_from_path(local_pdf_path, dpi=80, first_page=1, last_page=1)
        if pages:
            pages[0].save(thumb_path, "PNG")
            return True
    except Exception as exc:
        log.warning("Thumbnail-Erzeugung fehlgeschlagen fuer %s: %s", local_pdf_path, exc)
    return False


def download_file(url: str, local_path: str) -> bool:
    try:
        with session.get(url, timeout=HTTP_TIMEOUT, stream=True, verify=HTTP_VERIFY_SSL) as resp:
            resp.raise_for_status()
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            with open(local_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1024 * 64):
                    if chunk:
                        f.write(chunk)
        return True
    except Exception as exc:
        log.error("Download fehlgeschlagen fuer %s: %s", url, exc)
        return False


def process_file(col, year: str, date_folder: str, filename: str, file_url: str):
    relative_path = f"{year}/{date_folder}/{filename}"

    existing = col.find_one({"relative_path": relative_path})
    if existing and existing.get("downloaded"):
        return False  # bereits vollstaendig verarbeitet

    try:
        parsed_date = datetime.strptime(date_folder, "%Y%m%d")
    except ValueError:
        parsed_date = None

    meta = parse_filename(filename)

    local_path = os.path.join(STORAGE_PATH, year, date_folder, filename)

    log.info("Lade neue Datei: %s", relative_path)
    if not download_file(file_url, local_path):
        return False

    file_size = os.path.getsize(local_path) if os.path.exists(local_path) else None

    thumb_abs = os.path.join(STORAGE_PATH, year, date_folder, ".thumbnails", filename[:-4] + ".png")
    thumb_ok = make_thumbnail(local_path, thumb_abs)

    pdf_fields = extract_pdf_metadata(local_path)

    # 'measurement_datetime' (aus dem PDF-Inhalt, inkl. Uhrzeit) zusaetzlich als
    # echtes datetime-Objekt ablegen, falls parsebar -- fuer exakte Sortierung/Filterung
    measurement_datetime = None
    dt_str = pdf_fields.get("measurement_datetime_str")
    if dt_str:
        try:
            measurement_datetime = datetime.strptime(dt_str, "%d.%m.%Y %H:%M:%S")
        except ValueError:
            pass

    doc = {
        "relative_path": relative_path,
        "filename": filename,
        "year": year,
        "date_folder": date_folder,
        "date": parsed_date,
        "measurement_datetime": measurement_datetime,
        **meta,
        "file_size": file_size,
        "local_path": local_path,
        "thumbnail_path": thumb_abs if thumb_ok else None,
        "source_url": file_url,
        **pdf_fields,
        "downloaded": True,
        "downloaded_at": datetime.utcnow(),
    }

    try:
        col.update_one({"relative_path": relative_path}, {"$set": doc}, upsert=True)
    except DuplicateKeyError:
        pass

    return True



def main():
    mongo = MongoClient(MONGO_URI)
    db = mongo[MONGO_DB]
    col = db["protocols"]
    meta_col = db["scanner_state"]

    ensure_indexes(col)

    state = meta_col.find_one({"_id": "scanner"}) or {}
    initial_scan_done = state.get("initial_scan_done", False)

    while True:
        try:
            if not initial_scan_done:
                full_scan(col, meta_col)
                initial_scan_done = True
                update_status(meta_col, initial_scan_done=True, last_full_scan=datetime.utcnow())
            else:
                incremental_scan(col, meta_col)

            next_run = datetime.utcnow() + timedelta(seconds=POLL_INTERVAL_SECONDS)
            update_status(
                meta_col, status="idle", last_run=datetime.utcnow(),
                next_run=next_run, last_error=None,
            )

        except Exception:
            err_text = traceback.format_exc()
            log.error("Unerwarteter Fehler im Scan-Zyklus:\n%s", err_text)
            update_status(
                meta_col, status="error", detail="Fehler im Scan-Zyklus (siehe Logs)",
                last_error=str(err_text).splitlines()[-1] if err_text else "unbekannter Fehler",
            )

        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()