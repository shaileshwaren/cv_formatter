"""
Oxydata CV template specification.
All formatting constants derived from the 3 sample CVs.
"""

from docx.shared import Pt, Inches, RGBColor, Emu

# ── Page Setup ──────────────────────────────────────────────
PAGE_WIDTH = Emu(7560310)   # A4-ish width from samples
PAGE_HEIGHT = Emu(10692130) # A4-ish height from samples
MARGIN_TOP = Inches(1)
MARGIN_BOTTOM = Inches(1)
MARGIN_LEFT = Inches(1)
MARGIN_RIGHT = Inches(1)

# ── Fonts ───────────────────────────────────────────────────
FONT_FAMILY = "Calibri"

# ── Colors ──────────────────────────────────────────────────
COLOR_DARK_BLUE = RGBColor(0x1F, 0x38, 0x64)   # #1F3864
COLOR_BLACK = RGBColor(0x00, 0x00, 0x00)        # #000000
COLOR_GRAY = RGBColor(0x55, 0x55, 0x55)         # #555555
COLOR_WHITE = RGBColor(0xFF, 0xFF, 0xFF)         # #FFFFFF

# ── Font Sizes ──────────────────────────────────────────────
SIZE_NAME = Pt(18)           # 228600 EMU = 18pt
SIZE_SECTION_HEADER = Pt(11) # 139700 EMU = 11pt
SIZE_BODY = Pt(10)           # 127000 EMU = 10pt

# ── Spacing (in points) ────────────────────────────────────
SPACING_AFTER_NAME = Pt(2)
SPACING_AFTER_PERSONAL_INFO = Pt(1)
# Approx. 4 blank lines between sections
SPACING_BEFORE_SECTION = Pt(40)
SPACING_AFTER_SECTION_HEADER = Pt(4)
SPACING_AFTER_PARAGRAPH = Pt(2)

# ── Footer ──────────────────────────────────────────────────
FOOTER_TEXT = "Oxydata Software Sdn Bhd  |  www.oxydata.my  |  swamy@oxydata.my"

# ── Section titles (order of appearance) ────────────────────
SECTION_PROFESSIONAL_SUMMARY = "PROFESSIONAL SUMMARY"
SECTION_TECHNICAL_SKILLS = "TECHNICAL SKILLS"
SECTION_BUSINESS_SKILLS = "BUSINESS SKILLS"
SECTION_SOFT_SKILLS = "SOFT SKILLS"
SECTION_CERTIFICATIONS = "CERTIFICATIONS"
SECTION_AWARDS = "AWARDS & RECOGNITION"
SECTION_PROFESSIONAL_EXPERIENCE = "PROFESSIONAL EXPERIENCE"
SECTION_PROJECT_EXPERIENCE = "PROJECT EXPERIENCE"
SECTION_EDUCATION = "EDUCATION"
SECTION_HOBBIES = "HOBBIES & INTERESTS"

# ── Table styling ───────────────────────────────────────────
TABLE_HEADER_BG = None  # No background fill in samples
TABLE_COL_WIDTHS = (Inches(2.0), Inches(4.5))
