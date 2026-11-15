"""Profile photos: check, clean and store an uploaded image (SPEC §10.1, D-038, D-039).

An uploaded file is never trusted and never stored as it came:
- Pillow reads the content to find out what the file really is (the name can lie).
- Only JPEG and PNG are accepted, up to 2 MB and 25 million pixels.
- The image is drawn again into a new file, at most 800 px on the long side, without
  EXIF (camera, GPS position), XMP, text chunks or colour profiles.
- The new file gets a random name inside UPLOAD_DIR/photos; the user's file name is
  never used, so it can't point anywhere else on the disk.
"""

import io
import secrets
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from app.config import get_settings

MAX_BYTES = 2 * 1024 * 1024  # 2 MB (SPEC §10.1)
# The form around the file adds a little (boundary lines, CSRF field). A request
# bigger than this can't hold a valid photo, so it is turned away before it is read.
MAX_REQUEST_BYTES = MAX_BYTES + 64 * 1024
MAX_PIXELS = 25_000_000
MAX_SIDE = 800  # px, long side of the saved photo (D-039)

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png"}
# Pillow's name for the format -> the extension and media type we store it with.
FORMATS = {"JPEG": (".jpg", "image/jpeg"), "PNG": (".png", "image/png")}
PHOTO_FOLDER = "photos"

# Messages shown under the photo (short, in the user's words).
WRONG_TYPE = "Use a JPEG or PNG file."
TOO_BIG = "The photo must be 2 MB or smaller."
UNREADABLE = "We couldn't read this image."


class PhotoError(Exception):
    """The upload can't be used; `str(error)` is the message for the user."""


def upload_root() -> Path:
    return Path(get_settings().upload_dir).resolve()


def save_photo(data: bytes, filename: str) -> str:
    """Check and re-save an uploaded photo. Returns its path inside UPLOAD_DIR
    (e.g. "photos/3f9c...e1.jpg"). Raises PhotoError with a message for the user."""
    if Path(filename or "").suffix.lower() not in ALLOWED_EXTENSIONS:
        raise PhotoError(WRONG_TYPE)
    if len(data) > MAX_BYTES:
        raise PhotoError(TOO_BIG)
    if not data:
        raise PhotoError(UNREADABLE)

    try:
        image = Image.open(io.BytesIO(data))  # reads only the header, not the pixels
    except (UnidentifiedImageError, OSError):
        raise PhotoError(UNREADABLE) from None
    # The content decides, not the name: a GIF or a text file called .jpg is refused.
    # A PNG called .jpg is fine and is saved as .png (D-039).
    if image.format not in FORMATS:
        raise PhotoError(WRONG_TYPE)
    # Checked before the pixels are loaded: a small file can claim a huge image
    # ("decompression bomb") and fill the memory when it is decoded.
    width, height = image.size
    if width * height > MAX_PIXELS:
        raise PhotoError(TOO_BIG)

    try:
        cleaned = _clean(image, image.format)
    except (OSError, ValueError, Image.DecompressionBombError):
        raise PhotoError(UNREADABLE) from None

    extension, _media_type = FORMATS[image.format]
    folder = upload_root() / PHOTO_FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    relative = f"{PHOTO_FOLDER}/{secrets.token_hex(16)}{extension}"
    with open(upload_root() / relative, "wb") as file:
        if image.format == "JPEG":
            # No exif=, icc_profile= or pnginfo= arguments: nothing from the original
            # file's metadata is written into the new one.
            cleaned.save(file, format="JPEG", quality=90)
        else:
            cleaned.save(file, format="PNG", optimize=True)
    return relative


def _clean(image: Image.Image, image_format: str) -> Image.Image:
    """A new image with only the pixels: turned the right way, in a plain colour mode,
    at most MAX_SIDE px on the long side."""
    # Phones store "rotate 90°" in EXIF instead of rotating the pixels. Apply it now,
    # because the EXIF is dropped below. (The result no longer knows its format,
    # which is why the format is passed in.)
    image = ImageOps.exif_transpose(image)
    # Plain colour modes every browser and PDF can show. JPEG has no transparency, so
    # RGB; a PNG keeps its transparency (RGBA). This also turns CMYK, 16-bit and
    # palette images into RGB / RGBA.
    has_alpha = image.mode in ("RGBA", "LA", "PA") or "transparency" in image.info
    mode = "RGBA" if image_format == "PNG" and has_alpha else "RGB"
    if image.mode != mode:
        image = image.convert(mode)
    image.thumbnail((MAX_SIDE, MAX_SIDE))  # keeps the proportions, never enlarges
    # Copy the pixels into a brand-new image: its .info is empty, so no EXIF, XMP,
    # text chunk or colour profile can travel into the saved file.
    clean = Image.new(mode, image.size)
    clean.paste(image)
    return clean


def photo_file(relative: str) -> Path | None:
    """The file of a stored photo, or None if it is missing or the path would lead
    outside UPLOAD_DIR/photos (a broken or changed database value)."""
    folder = upload_root() / PHOTO_FOLDER
    path = (upload_root() / relative).resolve()
    if path.parent != folder or not path.is_file():
        return None
    return path


def media_type(relative: str) -> str:
    return "image/png" if relative.endswith(".png") else "image/jpeg"


def delete_photo_file(relative: str | None) -> None:
    """Remove a stored photo file; nothing happens if it is already gone."""
    if not relative:
        return
    path = photo_file(relative)
    if path is not None:
        path.unlink(missing_ok=True)
