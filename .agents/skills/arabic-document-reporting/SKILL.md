---
name: arabic-document-reporting
description: >-
  Specialized skill for creating professional Arabic documents, executive reports,
  and Word/DOCX files with Right-to-Left (RTL) layout, Arabic typography reshaping,
  bidi text support, and visual charts. Use whenever creating Arabic documentation,
  proposals, executive summaries, or localized reports.
---

# Arabic Document & Reporting Skill

This skill provides comprehensive standards and automation for generating high-quality Arabic documents, executive reports, and Word (`.docx`) deliverables with native Right-to-Left (RTL) orientation.

## 1. Core Principles of Arabic Reporting

1. **Executive Tone & Clarity:**
   - Keep technical terms clear and accessible.
   - Include English acronyms or terms alongside the Arabic equivalent when clarifying architecture (e.g. `دفتر أستاذ محاسبي (Double-Entry Ledger)`).
2. **Native RTL Layout in Word (`.docx`):**
   - Paragraph direction must be explicitly set with `<w:bidi/>`.
   - Alignment should default to Right (`WD_ALIGN_PARAGRAPH.RIGHT`).
   - Tables must have `<w:bidiVisual/>` added to `tblPr` to ensure column 1 starts on the right.
   - Set complex-script font attributes (`w:cs`) in run properties (`w:rPr`) targeting Arial or Calibri.
3. **Arabic Text on Image & Diagram Canvases:**
   - Standard PIL/Matplotlib drawing renders raw Arabic characters disconnected and left-to-right by default.
   - Always apply the reshaping pipeline:
     ```python
     import arabic_reshaper
     from bidi.algorithm import get_display

     def format_arabic(text: str) -> str:
         reshaped = arabic_reshaper.reshape(text)
         return get_display(reshaped)
     ```
4. **Visual Hierarchy:**
   - Use distinct header sizes: Title (20-24pt bold), H1 (15-16pt bold), Body (11pt regular).
   - Use corporate color accents (Navy Blue `#1B2A4A`, Royal Blue `#2E5BFF`, Charcoal `#222222`).
   - Accompany key text sections with visual diagram flowcharts.
