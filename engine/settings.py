"""Split a reference photo's develop settings into what gets copied and what doesn't."""

from .solver import CORRECTIVE

# Settings that belong to one photo's framing, lens or local edits. These are
# never copied from the reference.
PER_PHOTO_KEYS = {
    "Orientation",
    "WhiteBalance",  # "As Shot"/"Custom" label; Temp/Tint are solved instead
    "PaintBasedCorrections",
    "GradientBasedCorrections",
    "CircularGradientBasedCorrections",
    "MaskGroupBasedCorrections",
    "RetouchAreas",
    "RetouchInfo",
    "RedEyeInfo",
    "AutoLateralCA",
    "VignetteAmount",  # lens vignetting; PostCropVignette* is the creative one
    "VignetteMidpoint",
    # Detail settings depend on each photo's ISO and sharpness.
    "Sharpness",
    "SharpenRadius",
    "SharpenDetail",
    "SharpenEdgeMasking",
    "LuminanceSmoothing",
    "LuminanceNoiseReductionDetail",
    "LuminanceNoiseReductionContrast",
    "ColorNoiseReduction",
    "ColorNoiseReductionDetail",
    "ColorNoiseReductionSmoothness",
    "EnhanceDenoise",
}
PER_PHOTO_PREFIXES = (
    "Crop",
    "HasCrop",
    "LensProfile",
    "LensManual",
    "Defringe",
    "Perspective",
    "Upright",
    "Retouch",
    "Enhance",
)

RAW_FORMATS = {"RAW", "DNG"}
JPEG_NEUTRAL_TEMP = 0.0


def is_per_photo(key):
    return key in PER_PHOTO_KEYS or key.startswith(PER_PHOTO_PREFIXES)


def split(settings):
    """Return (creative, corrective) dicts from a full develop-settings dict."""
    creative, corrective = {}, {}
    for key, value in settings.items():
        if key in CORRECTIVE:
            corrective[key] = value
        elif not is_per_photo(key):
            creative[key] = value
    return creative, corrective


def is_raw(photo):
    return photo.get("fileFormat") in RAW_FORMATS


def starting_corrective(ref_corrective, ref_is_raw, target_is_raw):
    """The reference's corrective sliders, made valid for the target's file type.

    Raw files take Temperature in Kelvin; JPEG/TIFF take a -100..100 offset, so
    a Kelvin value can't carry across. The solver finds the right value anyway.
    """
    start = {key: float(ref_corrective.get(key, 0.0)) for key in CORRECTIVE}
    if ref_is_raw != target_is_raw:
        start["Temperature"] = 5500.0 if target_is_raw else JPEG_NEUTRAL_TEMP
        start["Tint"] = 0.0
    elif target_is_raw and start["Temperature"] == 0.0:
        start["Temperature"] = 5500.0
    return start
