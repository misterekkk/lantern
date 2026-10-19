"""When a file was made and where: EXIF (photos, HEIC included), QuickTime (videos, recordings), a date in the file
or folder name, then the file system. Pure Python reading only the file's header (EXIF, HEIF boxes, the `moov` atom),
so the daemon can fill dates for files indexed before this existed, without PyObjC and without re-indexing.

Dates are kept as wall-clock time where the file was made (a photo taken at 14:00 in Tokyo says 14:00), stored as
`calendar.timegm` of that naive time: "from May 2023" means May on the calendar of the person who took it. Only
plausible dates count (1990-01-01 up to tomorrow); camera reset dates (2000-01-01 00:00:00, the epoch) and numbers that
merely look like years are never read as dates.
"""

import calendar
import os
import re
import struct
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from lantern.scanner.walk import is_dataless

EARLIEST = datetime(1990, 1, 1)
RESET_DATES = {datetime(2000, 1, 1, 0, 0, 0), datetime(1970, 1, 1, 0, 0, 0)}
QUICKTIME_EXTENSIONS = {".mov", ".mp4", ".m4v", ".m4a", ".3gp", ".3g2", ".qt"}
HEIF_EXTENSIONS = {".heic", ".heif", ".avif", ".hif"}
MAX_MOOV = 32 << 20  # bytes of a `moov` atom read at most (metadata lives in its first few KB; frame tables follow)
_MAC_EPOCH = datetime(1904, 1, 1)


@dataclass(frozen=True)
class FileMeta:
    date: int | None  # wall-clock seconds (calendar.timegm of the naive local time)
    source: str | None  # exif | quicktime | name | fs
    lat: float | None = None
    lon: float | None = None


def plausible(dt: datetime | None, now: datetime | None = None) -> bool:
    if dt is None or dt in RESET_DATES:
        return False
    return EARLIEST <= dt <= (now or datetime.now()) + timedelta(days=1)


def to_seconds(dt: datetime) -> int:
    return calendar.timegm(dt.timetuple())


def from_seconds(seconds: int) -> datetime:
    return datetime(1970, 1, 1) + timedelta(seconds=seconds)


def _valid_coords(lat: float, lon: float) -> bool:
    return -90 <= lat <= 90 and -180 <= lon <= 180 and not (abs(lat) < 1e-6 and abs(lon) < 1e-6)


# ---------- dates in names ----------

_SEP = r"[-_. ]"
_TIME = r"(?:[ _T-]?(?:at )?(?P<H>[01]\d|2[0-3])[.:_h-]?(?P<M>[0-5]\d)[.:_m-]?(?P<S>[0-5]\d))?"
# 2023-05-14, 2023_05_14 12.34.56, "Screenshot 2023-05-14 at 12.34.56": separated, always with the day
_YMD = re.compile(
    rf"(?<!\d)(?P<y>(?:19|20)\d\d){_SEP}(?P<m>0[1-9]|1[0-2]){_SEP}(?P<d>0[1-9]|[12]\d|3[01])(?!\d){_TIME}"
)
# 14.05.2023, 14-05-2023 (European order; slashes cannot appear in file names)
_DMY = re.compile(r"(?<!\d)(?P<d>0[1-9]|[12]\d|3[01])[-.](?P<m>0[1-9]|1[0-2])[-.](?P<y>(?:19|20)\d\d)(?!\d)")
# 20230514_123456 (with the time: what cameras and phones write)
_COMPACT_TIME = re.compile(
    r"(?<!\d)(?P<y>(?:19|20)\d\d)(?P<m>0[1-9]|1[0-2])(?P<d>0[1-9]|[12]\d|3[01])[_-]"
    r"(?P<H>[01]\d|2[0-3])(?P<M>[0-5]\d)(?P<S>[0-5]\d)(?!\d)"
)
# IMG_20230514.jpg, PXL_20230514…: a bare 8-digit number counts only after a camera or app prefix (else it is an ID)
_COMPACT_PREFIXED = re.compile(
    r"(?:^|[^a-z])(?:img|vid|pxl|mvimg|pano|burst|dsc|photo|video|screenshot|signal|whatsapp image|whatsapp video"
    r"|snapchat|wp|rec|voice)[-_ ]?(?P<y>(?:19|20)\d\d)(?P<m>0[1-9]|1[0-2])(?P<d>0[1-9]|[12]\d|3[01])(?!\d)",
    re.IGNORECASE,
)


def _match_date(m: re.Match) -> datetime | None:
    g = m.groupdict()
    try:
        return datetime(int(g["y"]), int(g["m"]), int(g["d"]), int(g.get("H") or 0), int(g.get("M") or 0),
                        int(g.get("S") or 0))  # fmt: skip
    except ValueError:  # 2023-02-30
        return None


def date_in_name(name: str, now: datetime | None = None) -> datetime | None:
    for pattern in (_COMPACT_TIME, _YMD, _COMPACT_PREFIXED, _DMY):
        for m in pattern.finditer(name):
            dt = _match_date(m)
            if plausible(dt, now):
                return dt
    return None


def date_from_path(path: Path, now: datetime | None = None) -> datetime | None:
    """The file name first, then its folder and the folder above ("2023-05-14 Wakacje/IMG_1234.jpg")."""
    for part in (path.stem, path.parent.name, path.parent.parent.name):
        dt = date_in_name(part, now)
        if dt is not None:
            return dt
    return None


# ---------- EXIF ----------

_EXIF_IFD, _GPS_IFD = 0x8769, 0x8825
_DATE_TAGS = (0x9003, 0x9004)  # DateTimeOriginal, DateTimeDigitized (in the Exif IFD)
_DATETIME = 0x0132  # DateTime (IFD0): last change, often the shot when nothing else is set


def _exif_date(text) -> datetime | None:
    if not isinstance(text, str):
        return None
    try:
        return datetime.strptime(text.strip().rstrip("\x00")[:19], "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None


def _dms(value, ref) -> float | None:
    try:
        d, m, s = (float(x) for x in value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    deg = d + m / 60 + s / 3600
    return -deg if str(ref).upper().startswith(("S", "W")) else deg


def _from_exif(exif, now: datetime | None) -> tuple[datetime | None, float | None, float | None]:
    sub = exif.get_ifd(_EXIF_IFD)
    date = None
    for value in [sub.get(t) for t in _DATE_TAGS] + [exif.get(_DATETIME)]:
        dt = _exif_date(value)
        if plausible(dt, now):
            date = dt
            break
    gps = exif.get_ifd(_GPS_IFD)
    lat, lon = _dms(gps.get(2), gps.get(1)), _dms(gps.get(4), gps.get(3))
    if lat is None or lon is None or not _valid_coords(lat, lon):
        lat = lon = None
    return date, lat, lon


def _boxes(data: bytes, start: int = 0, end: int | None = None):
    """(type, payload start, payload end) of ISO-BMFF boxes in data[start:end]."""
    end = len(data) if end is None else end
    pos = start
    while pos + 8 <= end:
        size, kind = struct.unpack(">I4s", data[pos : pos + 8])
        header = 8
        if size == 1:
            if pos + 16 > end:
                return
            size, header = struct.unpack(">Q", data[pos + 8 : pos + 16])[0], 16
        elif size == 0:
            size = end - pos
        if size < header or pos + size > end:
            return
        yield kind.decode("latin-1"), pos + header, pos + size
        pos += size


def _uint(data: bytes, pos: int, size: int) -> int:
    return int.from_bytes(data[pos : pos + size], "big") if size else 0


def heif_exif(f) -> bytes | None:
    """The Exif block of a HEIF/HEIC/AVIF file (item of type 'Exif' in the `meta` box), as TIFF bytes."""
    head = _top_box(f, b"meta")  # read just that box (tens of KB), not the image data around it
    if head is None:
        return None
    meta = (0, len(head))
    exif_id, locations = None, {}
    for kind, a, b in _boxes(head, meta[0] + 4, meta[1]):  # meta is a full box: skip version + flags
        if kind == "iinf":
            version = head[a]
            pos = a + 4 + (2 if version == 0 else 4)
            for ikind, ia, _ in _boxes(head, pos, b):
                if ikind != "infe" or head[ia] < 2:
                    continue
                id_size = 2 if head[ia] == 2 else 4
                item_id = _uint(head, ia + 4, id_size)
                if head[ia + 4 + id_size + 2 : ia + 4 + id_size + 6] == b"Exif":
                    exif_id = item_id
        elif kind == "iloc":
            version = head[a]
            offset_size, length_size = head[a + 4] >> 4, head[a + 4] & 15
            base_size, index_size = head[a + 5] >> 4, (head[a + 5] & 15) if version in (1, 2) else 0
            pos = a + 6
            count = _uint(head, pos, 2 if version < 2 else 4)
            pos += 2 if version < 2 else 4
            for _ in range(count):
                item_id = _uint(head, pos, 2 if version < 2 else 4)
                pos += 2 if version < 2 else 4
                method = 0
                if version in (1, 2):
                    method = _uint(head, pos, 2) & 15
                    pos += 2
                pos += 2  # data_reference_index
                base = _uint(head, pos, base_size)
                pos += base_size
                extents = _uint(head, pos, 2)
                pos += 2
                spans = []
                for _ in range(extents):
                    pos += index_size
                    off, length = _uint(head, pos, offset_size), _uint(head, pos + offset_size, length_size)
                    pos += offset_size + length_size
                    spans.append((base + off, length))
                if method == 0:
                    locations[item_id] = spans
    if exif_id is None or exif_id not in locations:
        return None
    data = b""
    for off, length in locations[exif_id]:
        f.seek(off)
        data += f.read(min(length, 1 << 20))
    if len(data) < 4:
        return None
    skip = struct.unpack(">I", data[:4])[0]  # offset of the TIFF header after these four bytes
    return data[4 + skip :]


def read_exif(path: Path, now: datetime | None = None) -> tuple[datetime | None, float | None, float | None]:
    from PIL import Image

    try:
        if path.suffix.lower() in HEIF_EXTENSIONS:
            with open(path, "rb") as f:
                tiff = heif_exif(f)
            if not tiff:
                return None, None, None
            exif = Image.Exif()
            exif.load(tiff)
        else:
            with Image.open(path) as im:  # reads the header only
                exif = im.getexif()
        return _from_exif(exif, now)
    except Exception:  # a damaged or unusual file: no date from EXIF, the cascade goes on
        return None, None, None


# ---------- QuickTime ----------

_ISO6709 = re.compile(r"([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)")


def _iso6709(text: str) -> tuple[float | None, float | None]:
    m = _ISO6709.match(text.strip())
    if not m:
        return None, None
    lat, lon = float(m.group(1)), float(m.group(2))
    return (lat, lon) if _valid_coords(lat, lon) else (None, None)


def _qt_date(text: str) -> datetime | None:
    """ "2023-05-14T12:34:56+0200": the wall-clock time where it was recorded."""
    try:
        return datetime.strptime(text.strip()[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


def _top_box(f, wanted: bytes, cap: int = MAX_MOOV) -> bytes | None:
    """Payload of the first top-level box of this type, reading only box headers on the way."""
    f.seek(0, os.SEEK_END)
    end = f.tell()
    pos = 0
    while pos + 8 <= end:
        f.seek(pos)
        head = f.read(16)
        if len(head) < 8:
            return None
        size, kind = struct.unpack(">I4s", head[:8])
        header = 8
        if size == 1:
            size, header = struct.unpack(">Q", head[8:16])[0], 16
        elif size == 0:
            size = end - pos
        if size < header:
            return None
        if kind == wanted:
            f.seek(pos + header)
            return f.read(min(size - header, cap))  # truncated past the cap: the boxes we need come first
        pos += size
    return None


def _meta_items(moov: bytes, a: int, b: int) -> dict[str, bytes]:
    """QuickTime metadata (`meta` with `keys` + `ilst`): key name -> value bytes."""
    # QuickTime's meta is a plain box, MP4's a full box (4 bytes of version and flags first)
    if moov[a + 4 : a + 8] not in (b"hdlr", b"keys", b"ilst"):
        a += 4
    keys: list[str] = []
    out: dict[str, bytes] = {}
    for kind, ka, kb in _boxes(moov, a, b):
        if kind == "keys":
            pos = ka + 8  # version/flags + entry count
            while pos + 8 <= kb:
                size = struct.unpack(">I", moov[pos : pos + 4])[0]
                if size < 8:
                    break
                keys.append(moov[pos + 8 : pos + size].decode("utf-8", "replace"))
                pos += size
        elif kind == "ilst":
            for item, ia, ib in _boxes(moov, ka, kb):
                index = int.from_bytes(item.encode("latin-1"), "big")
                name = keys[index - 1] if 0 < index <= len(keys) else item
                for dkind, da, db in _boxes(moov, ia, ib):
                    if dkind == "data":
                        out[name] = moov[da + 8 : db]  # type indicator + locale, then the value
    return out


def read_quicktime(path: Path, now: datetime | None = None) -> tuple[datetime | None, float | None, float | None]:
    try:
        return _read_quicktime(path, now)
    except Exception:  # a damaged or unusual file: no date from QuickTime, the cascade goes on
        return None, None, None


def _read_quicktime(path: Path, now: datetime | None) -> tuple[datetime | None, float | None, float | None]:
    with open(path, "rb") as f:
        moov = _top_box(f, b"moov")
    if not moov:
        return None, None, None
    date = lat = lon = None
    created = None
    items: dict[str, bytes] = {}
    for kind, a, b in _boxes(moov):
        if kind == "mvhd" and b - a >= 12:
            version = moov[a]
            secs = _uint(moov, a + 4, 8 if version == 1 else 4)
            if secs:
                # mvhd is UTC: shown in this Mac's time zone (the recording's own zone is not stored there)
                utc = _MAC_EPOCH + timedelta(seconds=secs)
                created = datetime.fromtimestamp(calendar.timegm(utc.timetuple())) if utc.year >= 1970 else utc
        elif kind == "meta":
            items.update(_meta_items(moov, a, b))
        elif kind == "udta":
            for ukind, ua, ub in _boxes(moov, a, b):
                if ukind == "\xa9xyz" and ub - ua > 4:  # Apple and Android phones
                    lat, lon = _iso6709(moov[ua + 4 : ub].decode("latin-1", "replace"))
                elif ukind == "loci" and lat is None:  # 3GPP: name, role, then longitude, latitude as 16.16
                    name_end = moov.find(b"\x00", ua + 6, ub)
                    if name_end != -1 and name_end + 9 <= ub:
                        lon_, lat_ = struct.unpack(">ii", moov[name_end + 2 : name_end + 10])
                        if _valid_coords(lat_ / 65536, lon_ / 65536):
                            lat, lon = round(lat_ / 65536, 6), round(lon_ / 65536, 6)
                elif ukind == "meta":
                    items.update(_meta_items(moov, ua, ub))
    text = items.get("com.apple.quicktime.creationdate") or items.get("\xa9day")
    if text:
        date = _qt_date(text.decode("utf-8", "replace"))
    if not plausible(date, now):
        date = created if plausible(created, now) else None
    location = items.get("com.apple.quicktime.location.ISO6709")
    if location and lat is None:
        lat, lon = _iso6709(location.decode("utf-8", "replace"))
    return date, lat, lon


# ---------- the cascade ----------


def fs_date(st: os.stat_result, now: datetime | None = None) -> datetime | None:
    """The earlier of creation and modification time (a copied photo is newer on disk than its content)."""
    times = [t for t in (getattr(st, "st_birthtime", None), st.st_mtime) if t]
    candidates = [datetime.fromtimestamp(t) for t in times]
    candidates = [dt for dt in candidates if plausible(dt, now)]
    return min(candidates) if candidates else None


def read(path: Path, kind: str, places: bool = True, now: datetime | None = None) -> FileMeta:
    """kind: the file's index kind (image, video, audio, …). places: read GPS coordinates too."""
    try:
        st = os.stat(path)
    except OSError:
        return FileMeta(None, None)
    date = lat = lon = None
    source = None
    if not is_dataless(st):  # an iCloud placeholder: reading it would download it
        if kind == "image":
            date, lat, lon = read_exif(path, now)
            source = "exif" if date else None
        elif path.suffix.lower() in QUICKTIME_EXTENSIONS:
            date, lat, lon = read_quicktime(path, now)
            source = "quicktime" if date else None
    if date is None:
        date = date_from_path(path, now)
        source = "name" if date else None
    if date is None:
        date = fs_date(st, now)
        source = "fs" if date else None
    if not places:
        lat = lon = None
    return FileMeta(to_seconds(date) if date else None, source, lat, lon)
