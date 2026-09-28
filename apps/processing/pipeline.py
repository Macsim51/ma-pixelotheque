"""Pillow work happens in a disposable child, without database access."""

from datetime import datetime, timedelta, timezone
import math
import os
from pathlib import Path
import re
import resource
import warnings
from zoneinfo import ZoneInfo

from PIL import Image, ImageOps


class InvalidImage(ValueError):
    pass


FORMATS = {"JPEG": {".jpg", ".jpeg"}, "PNG": {".png"}, "WEBP": {".webp"}}


def webp_header(path):
    """Read dimensions before any native WebP canvas allocation.

    RIFF layouts: developers.google.com/speed/webp/docs/riff_container.
    Unknown metadata chunks are skipped by offset; at most 64 headers are read.
    This admits a candidate, never certifies its pixel data.
    """
    with open(path, "rb") as stream:
        header = stream.read(12)
        if header[:4] != b"RIFF" or header[8:12] != b"WEBP":
            return None
        total = int.from_bytes(header[4:8], "little") + 8
        if total > os.fstat(stream.fileno()).st_size:
            raise InvalidImage("Le fichier WebP est incomplet.")
        for _ in range(64):
            chunk = stream.read(8)
            if len(chunk) != 8:
                break
            length = int.from_bytes(chunk[4:8], "little")
            following = stream.tell() + length + (length & 1)
            if following > total:
                break
            kind = chunk[:4]
            if kind in (b"VP8X", b"VP8L", b"VP8 "):
                data = stream.read(min(length, 10))
                if kind == b"VP8X" and len(data) == 10 and length == 10:
                    return int.from_bytes(data[4:7], "little") + 1, int.from_bytes(data[7:10], "little") + 1, bool(data[0] & 2)
                if kind == b"VP8L" and len(data) >= 5 and data[0] == 0x2F:
                    bits = int.from_bytes(data[1:5], "little")
                    if bits >> 29 == 0:
                        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1, False
                if kind == b"VP8 " and len(data) == 10 and data[3:6] == b"\x9d\x01\x2a" and not data[0] & 1:
                    return int.from_bytes(data[6:8], "little") & 0x3FFF, int.from_bytes(data[8:10], "little") & 0x3FFF, False
                break
            stream.seek(following)
    raise InvalidImage("L’en-tête WebP n’est pas valide.")


def identify(path, max_pixels, *, extension=None, decode=False):
    """Header checks on receipt; full decode only inside the bounded child."""
    try:
        webp = webp_header(path)
        if webp is not None:
            width, height, animated = webp
            if not width or not height or width * height > max_pixels:
                raise InvalidImage("Cette image dépasse la limite de pixels autorisée.")
            if animated:
                raise InvalidImage("Les images animées ne sont pas acceptées.")
            if extension is not None and extension != ".webp":
                raise InvalidImage("L’extension ne correspond pas au format de l’image.")
            if not decode:
                return
        with Image.open(path, formats=list(FORMATS)) as image:
            if extension is not None and extension not in FORMATS.get(image.format, set()):
                raise InvalidImage("L’extension ne correspond pas au format de l’image.")
            if image.width * image.height > max_pixels or image.width < 1 or image.height < 1:
                raise InvalidImage("Cette image dépasse la limite de pixels autorisée.")
            if getattr(image, "n_frames", 1) != 1 or getattr(image, "is_animated", False):
                raise InvalidImage("Les images animées ne sont pas acceptées.")
            if decode:
                image.load()
                return extract_metadata(image)
            # Receipt reads identification/dimensions only. All decoding and
            # expensive integrity work is deferred to the bounded child.
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise InvalidImage("Cette image dépasse la limite de pixels autorisée.") from None
    except (OSError, SyntaxError, ValueError, EOFError) as error:
        if isinstance(error, InvalidImage):
            raise
        raise InvalidImage("Ce fichier n’est pas une photo JPEG, PNG ou WebP valide.") from None


def clean_text(value, length=120):
    if not isinstance(value, str):
        return ""
    return "".join(char for char in value if char.isprintable()).strip()[:length]


def coordinate(values, reference, positive, negative):
    if reference not in (positive, negative) or len(values) != 3:
        return None
    degrees, minutes, seconds = (float(value) for value in values)
    if not all(math.isfinite(value) for value in (degrees, minutes, seconds)):
        return None
    if degrees < 0 or not 0 <= minutes < 60 or not 0 <= seconds < 60:
        return None
    value = degrees + minutes / 60 + seconds / 3600
    return value if reference == positive else -value


def extract_metadata(image):
    result = {"width": image.width, "height": image.height, "camera": "", "latitude": None, "longitude": None, "date_text": "", "offset_text": "", "exif": {}}
    try:
        exif = image.getexif()
        nested = exif.get_ifd(34665) if exif.get(34665) else {}
        orientation = exif.get(274, 1)
        if orientation not in range(1, 9):
            orientation = 1
        if orientation in (5, 6, 7, 8):
            result["width"], result["height"] = image.height, image.width
        make, model = clean_text(exif.get(271), 80), clean_text(exif.get(272), 80)
        result["camera"] = (model if model.lower().startswith(make.lower()) else f"{make} {model}").strip()[:160]
        result["date_text"] = clean_text(nested.get(36867) or exif.get(36867) or exif.get(306), 32)
        result["offset_text"] = clean_text(nested.get(36881) or exif.get(36881), 6)
        result["exif"] = {"orientation": orientation, "make": make, "model": model, "date_time_original": result["date_text"], "offset_time_original": result["offset_text"]}
        gps = exif.get_ifd(34853) if exif.get(34853) else {}
        latitude = coordinate(gps.get(2, ()), gps.get(1), "N", "S")
        longitude = coordinate(gps.get(4, ()), gps.get(3), "E", "W")
        if latitude is not None and longitude is not None and -90 <= latitude <= 90 and -180 <= longitude <= 180:
            result["latitude"], result["longitude"] = latitude, longitude
    except (TypeError, ValueError, KeyError, OSError, OverflowError, ZeroDivisionError):
        # Broken optional EXIF does not reject a correctly decoded photograph.
        pass
    return result


def captured_at(metadata, zone):
    try:
        value = datetime.strptime(metadata.get("date_text", ""), "%Y:%m:%d %H:%M:%S")
        if not 1900 <= value.year <= 2100:
            return None
        offset = metadata.get("offset_text", "")
        match = re.fullmatch(r"([+-])(\d{2}):(\d{2})", offset)
        tz = ZoneInfo(zone)
        if match:
            hours, minutes = int(match[2]), int(match[3])
            if hours <= 14 and minutes < 60 and (hours < 14 or minutes == 0):
                delta = timedelta(hours=hours, minutes=minutes)
                tz = timezone(delta if match[1] == "+" else -delta)
        return value.replace(tzinfo=tz).isoformat()
    except (ValueError, TypeError):
        return None


def render(original, outputs, options):
    identify(original, options["max_pixels"])
    with Image.open(original) as source:
        source.load()
        ImageOps.exif_transpose(source, in_place=True)
        oriented = source
        try:
            # A fresh image deliberately carries no EXIF, GPS, XMP or comments.
            mode = "RGBA" if "A" in oriented.getbands() or "transparency" in oriented.info else "RGB"
            converted = oriented.convert(mode)
            try:
                # Reduce once before making per-variant copies. At most the
                # source and one full-resolution conversion coexist in memory.
                longest = max(options["thumbnail_size"], options["preview_size"])
                converted.thumbnail((longest, longest), Image.Resampling.LANCZOS)
                for name, size in (("thumbnail", options["thumbnail_size"]), ("preview", options["preview_size"])):
                    resized = converted.copy()
                    try:
                        resized.thumbnail((size, size), Image.Resampling.LANCZOS)
                        resized.info.clear()
                        path = Path(outputs[name])
                        path.parent.mkdir(parents=True, exist_ok=True)
                        temporary = path.with_suffix(".part")
                        try:
                            with temporary.open("xb") as stream:
                                resized.save(stream, format="WEBP", quality=options["quality"], method=3)
                            os.replace(temporary, path)
                        finally:
                            temporary.unlink(missing_ok=True)
                    finally:
                        resized.close()
            finally:
                converted.close()
        finally:
            oriented.close()


def child_main(connection, kind, original, outputs, options):
    try:
        limit = options["memory_mb"] * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
        resource.setrlimit(resource.RLIMIT_CPU, (options["timeout"] + 2, options["timeout"] + 3))
        os.nice(10)
        Image.MAX_IMAGE_PIXELS = 80_000_000
        warnings.filterwarnings("error", category=Image.DecompressionBombWarning)
        if kind == "inspect":
            data = identify(original, options["max_pixels"], decode=True)
            data["captured_at"] = captured_at(data, options["timezone"])
        else:
            render(original, outputs, options)
            data = {}
        connection.send({"ok": True, "data": data})
    except InvalidImage as error:
        connection.send({"ok": False, "code": "invalid_image", "message": str(error), "retryable": False})
    except MemoryError:
        connection.send({"ok": False, "code": "memory_limit", "message": "La photo dépasse la mémoire autorisée pour un traitement.", "retryable": False})
    except OSError:
        connection.send({"ok": False, "code": "storage_error", "message": "Le traitement ne peut pas lire ou écrire ses fichiers.", "retryable": True})
    except Exception:
        connection.send({"ok": False, "code": "processing_error", "message": "Le traitement de cette photo a échoué.", "retryable": False})
    finally:
        connection.close()
