#!/usr/bin/env python3
"""
Extrahiert strukturierte Metadaten aus dem Text-Layer der NIR-Protokoll-PDFs.

Die PDFs werden offenbar aus einer Vorlage (feste Labels) plus dynamisch
eingefuegten Werten erzeugt. Je nach PDF-Erzeugungs-Tool kann die Reihenfolge
der Textobjekte im Content-Stream von der visuellen Reihenfolge abweichen
(typisch z.B. bei Report-Generatoren, die Label-Layer und Wert-Layer getrennt
zeichnen). Deshalb wird hier NICHT einfach page.extract_text() sequentiell
verwendet, sondern:

  1. Alle Woerter mit ihren (x, y)-Koordinaten auslesen (pdfplumber.extract_words)
  2. Woerter zu visuellen Zeilen clustern (Toleranz auf der y-Achse)
  3. Innerhalb einer Zeile nach der x-Achse sortieren

Dadurch entsteht ein Text, der der tatsaechlichen visuellen Anordnung
entspricht ("Label: Wert" nebeneinander), unabhaengig von der internen
Speicherreihenfolge im PDF. Auf diesem rekonstruierten Text werden dann
robuste Regex-Muster angewendet.
"""

import re
import logging

log = logging.getLogger("pdf_parser")

try:
    import pdfplumber
except ImportError:  # pragma: no cover
    pdfplumber = None


def _cluster_rows(words, tolerance=3.0):
    """Gruppiert Woerter (Liste von dicts mit 'text','x0','top') zu visuellen Zeilen."""
    words_sorted = sorted(words, key=lambda w: w["top"])
    rows = []
    current_row = []
    current_top = None
    for w in words_sorted:
        if current_top is None or abs(w["top"] - current_top) <= tolerance:
            current_row.append(w)
            current_top = sum(x["top"] for x in current_row) / len(current_row)
        else:
            rows.append(current_row)
            current_row = [w]
            current_top = w["top"]
    if current_row:
        rows.append(current_row)

    lines = []
    for row in rows:
        row_sorted = sorted(row, key=lambda w: w["x0"])
        lines.append(" ".join(w["text"] for w in row_sorted))
    return lines


def extract_layout_text(pdf_path: str) -> str:
    """Liest den PDF-Text seitenweise, layout-treu rekonstruiert (siehe Modul-Docstring)."""
    if pdfplumber is None:
        return ""
    all_lines = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                words = page.extract_words(use_text_flow=False, keep_blank_chars=False)
                all_lines.extend(_cluster_rows(words))
    except Exception as exc:
        log.warning("PDF-Text-Extraktion fehlgeschlagen fuer %s: %s", pdf_path, exc)
        return ""
    return "\n".join(all_lines)


# --------------------------------------------------------------------------- #
# Regex-Muster fuer die einzelnen Felder (angewendet auf den layout-treuen Text)
# --------------------------------------------------------------------------- #
_FIELD_PATTERNS = {
    "measurement_datetime_str": r"Datum/Uhrzeit:\s*(\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2}:\d{2})",
    "benutzer": r"Benutzer:\s*(.+?)\s+Ch\.-Bez\.:",
    "apotheke": r"Apotheke:\s*(.+?)\s+MHD/Lieferdatum:",
    "messung_nummer_pdf": r"Messung Nummer:\s*(\d+)",
    "stoffklasse": r"(?<!Original )Stoffklasse:\s*(\d+\s+.+?)(?:\n|$)",
    "original_stoffklasse": r"Original Stoffklasse:\s*(\d+\s+.+?)\s+(?:Durch Nutzer best|$)",
    "pruefergebnis": r"Pr(?:u|ü)fergebnis:\s*(.+?)\s+Konformit",
    "konformitaetsindex": r"Konformit\S*index:\s*([\d.]+)",
    "stoffklassenvalidierung": r"Stoffklassenvalidierung:\s*(\S+)",
    "korrelation": r"(?<!WC1920 \()Korrelation:\s*([\d.]+)",
    "geraete_id_pdf": r"Ger(?:a|ä)te-ID:\s*(\S+)",
    "protokoll_version": r"Protokoll-Version:\s*(\S+)",
    "software_version": r"Software-Version:\s*(\S+)",
    "datenbank_version": r"Datenbank-Version:\s*(\S+)",
    "letzte_systempruefung_datetime_str": r"Letzte Systempr(?:u|ü)fung:\s*(\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2}:\d{2})",
    "letzte_systempruefung_ergebnis": r"Letzte Systempr(?:u|ü)fung:.*?Ergebnis:\s*(\S+)",
    "korrelation_wc1920": r"Korrelation WC1920.*?:\s*([\d.]+)",
    "bemerkung": r"Bemerkung:\s*(.+)",
}

_NEIGHBOR_PATTERN = re.compile(
    r"(\d{6})\s+(.+?)\s*\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*(\d{6})\s*\)"
)


def parse_pdf_fields(text: str) -> dict:
    """Wendet alle Regex-Muster auf den (layout-treuen) Text an."""
    result = {}
    for key, pattern in _FIELD_PATTERNS.items():
        m = re.search(pattern, text)
        result[key] = m.group(1).strip() if m else None

    neighbors = []
    for m in _NEIGHBOR_PATTERN.finditer(text):
        try:
            neighbors.append({
                "code": m.group(1),
                "name": m.group(2).strip(),
                "korrelation": float(m.group(3)),
                "konformitaetsindex": float(m.group(4)),
                "gruppenklasse": m.group(5),
            })
        except ValueError:
            continue
    result["naechste_nachbarn"] = neighbors

    # numerische Felder konvertieren, wo sinnvoll
    for float_field in ("konformitaetsindex", "korrelation", "korrelation_wc1920"):
        if result.get(float_field):
            try:
                result[float_field] = float(result[float_field])
            except ValueError:
                pass

    return result


def extract_pdf_metadata(pdf_path: str) -> dict:
    """Kombiniert Text-Extraktion + Feld-Parsing. Liefert im Fehlerfall leeres dict."""
    text = extract_layout_text(pdf_path)
    if not text:
        return {}
    try:
        return parse_pdf_fields(text)
    except Exception as exc:
        log.warning("Feld-Parsing fehlgeschlagen fuer %s: %s", pdf_path, exc)
        return {}