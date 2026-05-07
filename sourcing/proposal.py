"""
Proposal PDF generator — B2 of the expansion specialist.

Produces a shareable 1-page (A4 landscape) PDF shortlist:
  - Cover header with project name, date, requirement summary
  - Comparison table: rank, address, sqm, docks, height, price/sqm, SLEX, flood, score
  - Financial projection: 3-year compound escalation at 5%, 7%, 10%
  - Score breakdown bar chart (top 5 listings)

Returns PDF as bytes → use with st.download_button.
"""
from __future__ import annotations

import io
from datetime import datetime, timezone
from typing import List, Optional

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm, mm
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from sourcing.models import ScoredListing
from sourcing.requirements import ExpansionRequirement

# ── Brand colours ─────────────────────────────────────────────────────────────
_NAVY    = colors.HexColor("#1a2744")
_ORANGE  = colors.HexColor("#e67e22")
_GREEN   = colors.HexColor("#27ae60")
_YELLOW  = colors.HexColor("#f39c12")
_RED     = colors.HexColor("#e74c3c")
_GREY    = colors.HexColor("#95a5a6")
_LIGHT   = colors.HexColor("#f8f9fa")
_BORDER  = colors.HexColor("#dee2e6")
_TEXT    = colors.HexColor("#212529")
_SUBTEXT = colors.HexColor("#6c757d")


def _score_color(score: Optional[float]) -> colors.Color:
    if score is None:   return _GREY
    if score >= 70:     return _GREEN
    if score >= 50:     return _YELLOW
    if score >= 30:     return _ORANGE
    return _RED


def _fmt(v, fmt: str, fallback: str = "—") -> str:
    if v is None:
        return fallback
    try:
        return fmt.format(v)
    except Exception:
        return fallback


def _escalation_table(
    listings: List[ScoredListing],
    rates: tuple = (0.05, 0.07, 0.10),
    years: int = 3,
) -> List[List]:
    """
    Build rows for the financial projection table.
    Monthly cost = sqm × price_per_sqm.
    Year N cost = base_monthly × (1 + r)^N  [compound annual]
    3-year total = sum of 36 monthly payments (approximated as 12 × year_cost per year)
    """
    header = ["#", "Property", "Base/mo (PHP)"] + [
        f"Yr {y+1} @ {int(r*100)}%" for r in rates for y in range(years)
    ]
    # Simpler: show Year 1/2/3 monthly cost for each rate
    header = ["#", "Property (truncated)", "Base/mo"]
    for r in rates:
        header.append(f"Yr3 @{int(r*100)}%")
    header.append("3yr Total @7%")

    rows = [header]
    for i, l in enumerate(listings, 1):
        base_mo = l.listing.price_php
        title = (l.listing.title or l.id)[:35]
        if base_mo is None:
            rows.append([str(i), title, "—", "—", "—", "—", "—"])
            continue
        yr3 = [base_mo * (1 + r) ** 3 for r in rates]
        # 3-year total at 7%: sum year1+year2+year3 monthly × 12
        total_7 = sum(base_mo * (1 + 0.07) ** y * 12 for y in range(1, 4))
        rows.append([
            str(i),
            title,
            f"₱{base_mo:,.0f}",
            f"₱{yr3[0]:,.0f}",
            f"₱{yr3[1]:,.0f}",
            f"₱{yr3[2]:,.0f}",
            f"₱{total_7:,.0f}",
        ])
    return rows


def generate_proposal_pdf(
    listings: List[ScoredListing],
    requirement: Optional[ExpansionRequirement] = None,
    project_name: str = "Warehouse Shortlist",
) -> bytes:
    """
    Build the proposal PDF and return it as bytes.

    listings: ordered shortlist (already sorted by score descending)
    requirement: active requirement for the cover header (optional)
    project_name: fallback title if no requirement
    """
    buf = io.BytesIO()
    PAGE = landscape(A4)
    doc = SimpleDocTemplate(
        buf,
        pagesize=PAGE,
        leftMargin=1.5 * cm,
        rightMargin=1.5 * cm,
        topMargin=1.5 * cm,
        bottomMargin=1.5 * cm,
        title=project_name,
        author="SPX Site Sourcing",
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "spx_title",
        parent=styles["Normal"],
        fontSize=18, fontName="Helvetica-Bold",
        textColor=_NAVY, leading=22, spaceAfter=2 * mm,
    )
    subtitle_style = ParagraphStyle(
        "spx_subtitle",
        parent=styles["Normal"],
        fontSize=10, fontName="Helvetica",
        textColor=_SUBTEXT, leading=14, spaceAfter=4 * mm,
    )
    section_style = ParagraphStyle(
        "spx_section",
        parent=styles["Normal"],
        fontSize=11, fontName="Helvetica-Bold",
        textColor=_NAVY, leading=14,
        spaceBefore=6 * mm, spaceAfter=3 * mm,
    )
    cell_style = ParagraphStyle(
        "spx_cell",
        parent=styles["Normal"],
        fontSize=8, fontName="Helvetica",
        textColor=_TEXT, leading=10,
    )
    cell_bold = ParagraphStyle(
        "spx_cell_bold",
        parent=cell_style,
        fontName="Helvetica-Bold",
    )

    story = []
    now = datetime.now(timezone.utc).strftime("%B %d, %Y")

    # ── Cover header ──────────────────────────────────────────────────────────
    title = requirement.project_name if requirement else project_name
    story.append(Paragraph(f"🏭 SPX Warehouse Shortlist — {title}", title_style))

    subtitle_parts = [f"Generated {now}"]
    if requirement:
        if requirement.region_priority:
            subtitle_parts.append("Regions: " + ", ".join(requirement.region_priority))
        if requirement.sqm_min or requirement.sqm_max:
            subtitle_parts.append(
                f"Floor area: {requirement.sqm_min:,.0f}–{requirement.sqm_max:,.0f} sqm"
            )
        if requirement.budget_max_sqm_month > 0:
            subtitle_parts.append(f"Budget: ≤₱{requirement.budget_max_sqm_month:,.0f}/sqm/mo")
        if requirement.dock_doors_min > 0:
            subtitle_parts.append(f"Docks: ≥{requirement.dock_doors_min}")
    subtitle_parts.append(f"{len(listings)} listings shortlisted")
    story.append(Paragraph("  ·  ".join(subtitle_parts), subtitle_style))
    story.append(HRFlowable(width="100%", thickness=2, color=_ORANGE, spaceAfter=4 * mm))

    # ── Comparison table ──────────────────────────────────────────────────────
    story.append(Paragraph("Shortlist Comparison", section_style))

    def _cell(txt: str, bold: bool = False, align: str = "left") -> Paragraph:
        s = cell_bold if bold else cell_style
        if align == "center":
            s = ParagraphStyle("_c", parent=s, alignment=TA_CENTER)
        elif align == "right":
            s = ParagraphStyle("_r", parent=s, alignment=TA_RIGHT)
        return Paragraph(str(txt), s)

    # Header row
    col_headers = ["#", "Property", "Region", "sqm", "Docks", "Ht(m)",
                   "₱/sqm/mo", "SLEX km", "Flood", "Score"]
    tbl_data = [[_cell(h, bold=True, align="center") for h in col_headers]]

    # Col widths (landscape A4 usable ≈ 25.4 cm)
    PAGE_W = PAGE[0] - 3 * cm   # usable width
    col_widths = [
        0.8 * cm,   # #
        6.5 * cm,   # Property
        2.5 * cm,   # Region
        1.8 * cm,   # sqm
        1.5 * cm,   # Docks
        1.5 * cm,   # Ht
        2.2 * cm,   # Price/sqm
        2.0 * cm,   # SLEX km
        1.8 * cm,   # Flood
        1.8 * cm,   # Score
    ]

    for i, l in enumerate(listings, 1):
        corridors = l.enriched.corridor_distances_km or {}
        slex = corridors.get("SLEX")
        # Price per sqm/month
        if l.listing.price_php and l.listing.sqm:
            price_sqm = l.listing.price_php / l.listing.sqm
            price_str = f"₱{price_sqm:,.0f}"
        elif l.listing.price_php:
            price_str = f"₱{l.listing.price_php:,.0f}/mo"
        else:
            price_str = "—"

        score_str = f"{l.score:.0f}" if l.score is not None else "—"
        flood = (l.enriched.flood_risk or "?").upper()
        title_short = (l.listing.title or l.id)[:55]
        region = (l.listing.region or "?")[:14]

        tbl_data.append([
            _cell(str(i), align="center"),
            _cell(title_short),
            _cell(region),
            _cell(_fmt(l.listing.sqm, "{:,.0f}"), align="right"),
            _cell(_fmt(l.listing.dock_doors, "{:.0f}"), align="center"),
            _cell(_fmt(l.listing.clear_height_m, "{:.1f}"), align="center"),
            _cell(price_str, align="right"),
            _cell(_fmt(slex, "{:.1f}"), align="right"),
            _cell(flood, align="center"),
            _cell(score_str, bold=True, align="center"),
        ])

    tbl = Table(tbl_data, colWidths=col_widths, repeatRows=1)

    # Build per-row score background colors
    tbl_style_cmds = [
        # Header
        ("BACKGROUND", (0, 0), (-1, 0), _NAVY),
        ("TEXTCOLOR",  (0, 0), (-1, 0), colors.white),
        ("FONTNAME",   (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",   (0, 0), (-1, 0), 8),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [_LIGHT, colors.white]),
        ("GRID",       (0, 0), (-1, -1), 0.25, _BORDER),
        ("TOPPADDING",  (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING",  (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("VALIGN",     (0, 0), (-1, -1), "MIDDLE"),
    ]

    # Score cell color per data row
    for i, l in enumerate(listings, 1):
        sc = _score_color(l.score)
        row_idx = i  # 0 = header
        tbl_style_cmds.append(("BACKGROUND", (9, row_idx), (9, row_idx), sc))
        tbl_style_cmds.append(("TEXTCOLOR",  (9, row_idx), (9, row_idx), colors.white))

    tbl.setStyle(TableStyle(tbl_style_cmds))
    story.append(tbl)

    # ── Financial projection ──────────────────────────────────────────────────
    story.append(Paragraph("3-Year Financial Projection", section_style))
    story.append(Paragraph(
        "Monthly rent compound-escalated annually at 5%, 7%, 10%. "
        "3yr Total = sum of 36 monthly payments at 7% (Year 1 + Year 2 + Year 3).",
        subtitle_style,
    ))

    fin_data = _escalation_table(listings)
    fin_headers = fin_data[0]
    fin_col_widths = [
        0.8 * cm,   # #
        6.0 * cm,   # Property
        2.8 * cm,   # Base/mo
        2.5 * cm,   # Yr3 @5%
        2.5 * cm,   # Yr3 @7%
        2.5 * cm,   # Yr3 @10%
        3.0 * cm,   # 3yr total
    ]

    fin_tbl_data = []
    for ri, row in enumerate(fin_data):
        if ri == 0:
            fin_tbl_data.append([_cell(c, bold=True, align="center") for c in row])
        else:
            fin_tbl_data.append([
                _cell(row[0], align="center"),
                _cell(row[1]),
                _cell(row[2], align="right"),
                _cell(row[3], align="right"),
                _cell(row[4], align="right"),
                _cell(row[5], align="right"),
                _cell(row[6], bold=True, align="right"),
            ])

    fin_tbl = Table(fin_tbl_data, colWidths=fin_col_widths, repeatRows=1)
    fin_tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0), _NAVY),
        ("TEXTCOLOR",     (0, 0), (-1, 0), colors.white),
        ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, 0), 8),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [_LIGHT, colors.white]),
        ("GRID",          (0, 0), (-1, -1), 0.25, _BORDER),
        ("TOPPADDING",    (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING",   (0, 0), (-1, -1), 4),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        # Highlight 3yr total column
        ("BACKGROUND",    (6, 1), (6, -1), colors.HexColor("#fff3cd")),
        ("FONTNAME",      (6, 1), (6, -1), "Helvetica-Bold"),
    ]))
    story.append(fin_tbl)

    # ── Score breakdown mini-bars ─────────────────────────────────────────────
    scored = [l for l in listings if l.score is not None and l.score_breakdown]
    if scored:
        story.append(Paragraph("Score Breakdown (top 5)", section_style))
        story.append(Paragraph(
            "Points earned per category — max weights shown in parentheses.",
            subtitle_style,
        ))

        breakdown_rows = [["Property", "Total", "sqm", "Docks", "Height",
                           "Region", "Corridor", "PEZA", "Flood"]]
        for l in scored[:5]:
            b = l.score_breakdown
            breakdown_rows.append([
                _cell((l.listing.title or l.id)[:40]),
                _cell(f"{l.score:.0f}", bold=True, align="center"),
                _cell(_fmt(b.sqm, "{:.0f}"), align="center"),
                _cell(_fmt(b.dock_doors, "{:.0f}"), align="center"),
                _cell(_fmt(b.clear_height_m, "{:.0f}"), align="center"),
                _cell(_fmt(b.region, "{:.0f}"), align="center"),
                _cell(_fmt(b.corridor_access, "{:.0f}"), align="center"),
                _cell(_fmt(b.peza_zone, "{:.0f}"), align="center"),
                _cell(_fmt(b.max_flood_risk, "{:.0f}"), align="center"),
            ])

        bd_col_w = [7.0*cm, 1.5*cm, 1.5*cm, 1.5*cm, 1.8*cm, 1.8*cm, 2.2*cm, 1.5*cm, 1.5*cm]
        bd_tbl = Table(breakdown_rows, colWidths=bd_col_w, repeatRows=1)
        bd_tbl.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, 0), _NAVY),
            ("TEXTCOLOR",     (0, 0), (-1, 0), colors.white),
            ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE",      (0, 0), (-1, 0), 8),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [_LIGHT, colors.white]),
            ("GRID",          (0, 0), (-1, -1), 0.25, _BORDER),
            ("TOPPADDING",    (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING",   (0, 0), (-1, -1), 4),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
            ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
            # Score column highlighted
            ("BACKGROUND",    (1, 1), (1, -1), colors.HexColor("#e8f5e9")),
            ("FONTNAME",      (1, 1), (1, -1), "Helvetica-Bold"),
        ]))
        story.append(bd_tbl)

    # ── Footer note ───────────────────────────────────────────────────────────
    story.append(Spacer(1, 6 * mm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=_BORDER))
    story.append(Paragraph(
        f"Generated by SPX Site Sourcing Dashboard · {now} · "
        "Prices and availability subject to verification with listing agents. "
        "Dashboard link: http://localhost:8501",
        ParagraphStyle("footer", parent=styles["Normal"],
                       fontSize=7, textColor=_SUBTEXT, leading=9, spaceBefore=2*mm),
    ))

    doc.build(story)
    buf.seek(0)
    return buf.read()
