"""Imagenette classes, their queries (Polish, English) and control queries."""

CLASSES = {
    "n01440764": ("ryba", "a fish"), "n02102040": ("pies", "a dog"),
    "n02979186": ("magnetofon kasetowy", "a cassette player"), "n03000684": ("piła łańcuchowa", "a chainsaw"),
    "n03028079": ("kościół", "a church"), "n03394916": ("waltornia", "a french horn"),
    "n03417042": ("śmieciarka", "a garbage truck"), "n03425413": ("dystrybutor paliwa", "a gas pump"),
    "n03445777": ("piłka golfowa", "a golf ball"), "n03888257": ("spadochron", "a parachute"),
}
CONTROL = ["plaża", "a cat", "jedzenie", "zachód słońca", "faktura vat", "def load config"]
PER_CLASS = 30
