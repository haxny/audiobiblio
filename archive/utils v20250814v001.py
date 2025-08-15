# utils.py
import re
import unicodedata
from datetime import datetime

# Station mapping
STATION_MAP = {
    "radiozurnal.rozhlas.cz": "CRo1",
    "dvojka.rozhlas.cz": "CRo2",
    "vltava.rozhlas.cz": "CRo3",
    "plus.rozhlas.cz": "CRo+",
    "junior.rozhlas.cz": "CRoJun",
    "www.radiojunior.cz": "CRoJun",
    "wave.rozhlas.cz": "CRoW",
    "d-dur.rozhlas.cz": "CRoDdur",
    "jazz.rozhlas.cz": "CRoJazz",
    "pohoda.rozhlas.cz": "CRoPohoda",
    "radio.cz": "CRoInt",
    "brno.rozhlas.cz": "CRoBrno",
    "budejovice.rozhlas.cz": "CRoCB",
    "hradec.rozhlas.cz": "CRoHK",
    "vary.rozhlas.cz": "CRoKV",
    "liberec.rozhlas.cz": "CRoLib",
    "olomouc.rozhlas.cz": "CRoOL",
    "ostrava.rozhlas.cz": "CRoOV",
    "pardubice.rozhlas.cz": "CRoPard",
    "plzen.rozhlas.cz": "CRoPlz",
    "praha.rozhlas.cz": "CRoPRG",
    "region.rozhlas.cz": "CRoRegion",
    "sever.rozhlas.cz": "CRoSever",
    "vysocina.rozhlas.cz": "CRoVys",
    "zlin.rozhlas.cz": "CRoZL"
}

def normalize_text(text):
    """Remove diacritics, clean up unsafe filesystem characters."""
    if not text:
        return ""
    text = unicodedata.normalize('NFKD', text)
    text = text.encode('ascii', 'ignore').decode('ascii')
    text = re.sub(r'[<>:"/\\|?*]', '', text)  # remove invalid characters
    text = re.sub(r'\s+', ' ', text)  # collapse multiple spaces
    return text.strip()

def safe_date(date_str, sep=""):
    """
    Convert date to YYYYMMDD (default) or YYYY-MM-DD if sep="-".
    Returns '00000000' or '0000-00-00' if invalid.
    """
    if not date_str:
        return "00000000" if sep == "" else "0000-00-00"
    try:
        if "-" in date_str:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
        elif len(date_str) == 8 and date_str.isdigit():
            dt = datetime.strptime(date_str, "%Y%m%d")
        else:
            return "00000000" if sep == "" else "0000-00-00"
        return dt.strftime(f"%Y{sep}%m{sep}%d")
    except Exception:
        return "00000000" if sep == "" else "0000-00-00"

def extract_station_code(url):
    """Return station code based on URL, or 'Unknown'."""
    for domain, code in STATION_MAP.items():
        if domain in url:
            return code
    return "Unknown"
