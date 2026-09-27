---
name: diagram-generator
description: >-
  Provides guidelines, python utilities, and templates for generating high-resolution
  diagrams (flowcharts, sequence diagrams, system architecture) and embedding them
  into documentation, reports, and Word/DOCX files. Use whenever creating visual
  diagrams, architecture charts, or embedding graphical workflows.
---

# Diagram Generator Skill

This skill equips Antigravity (`agy`) agents with tools and best practices for creating clear, professional system architecture diagrams, workflow charts, and sequence diagrams, and embedding them into Markdown, HTML, and Word (`.docx`) documents.

## 1. Capabilities & Supported Methods

1. **Python Matplotlib & Pillow (`PIL`):**
   - Ideal for standalone PNG diagrams with custom themes, colors, and layout boxes.
   - Zero external binary dependencies beyond Python packages.
   - High DPI output (`dpi=300`) suitable for corporate executive documents.
2. **Arabic Text Support in Diagrams:**
   - Always reshape Arabic text using `arabic_reshaper.reshape(text)` and `bidi.algorithm.get_display(...)` when drawing onto raster canvases or matplotlib plots to ensure proper letter connection and right-to-left ordering.
3. **Word Document (`.docx`) Embedding:**
   - Save generated diagram as PNG.
   - Embed into document using `doc.add_picture(image_path, width=Inches(6.0))` and center align.

## 2. Standard Visual Styling

- **Canvas Background:** Clean off-white (`#F8F9FD`) or pure white (`#FFFFFF`).
- **Primary Brand / Node Color:** Deep Navy (`#1B2A4A`) with white text.
- **Accent Color:** Royal Blue (`#2E5BFF`) or Cyan (`#00A3FF`).
- **Action / Success Color:** Green (`#28A745`) or Mint.
- **Container Borders:** Subtle gray `#DCE1EA` with rounded corners.
- **Font Selection:** Sans-serif (Arial, Helvetica, or system sans-serif) for clean readability.

## 3. Workflow for Generating & Embedding

1. Define the system nodes, layers, and arrows.
2. Render diagram to a target image path in `docs/reports/assets/` or `artifacts/`.
3. Verify resolution and visual clarity.
4. Insert into the target document (`.docx` or `.md`).
