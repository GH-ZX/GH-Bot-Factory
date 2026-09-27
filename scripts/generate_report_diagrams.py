#!/usr/bin/env python3
"""
Generate high-resolution architecture and workflow diagrams for GH-Bot-Factory reports.
Uses Pillow with native HarfBuzz/FriBidi RAQM complex text layout for genuine Arabic RTL rendering.
Saves images to docs/reports/assets/
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def get_arabic_fonts():
    """Locate system Arabic fonts that support full Arabic + Latin typography."""
    reg_candidates = [
        "/usr/share/fonts/opentype/fonts-hosny-amiri/Amiri-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
    ]
    bld_candidates = [
        "/usr/share/fonts/opentype/fonts-hosny-amiri/Amiri-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf",
    ]

    reg_path = next((c for c in reg_candidates if Path(c).exists()), None)
    bld_path = next((c for c in bld_candidates if Path(c).exists()), None)
    return reg_path, bld_path


REG_FONT_PATH, BLD_FONT_PATH = get_arabic_fonts()


def get_font(bold: bool = False, size: int = 20) -> ImageFont.FreeTypeFont:
    """Instantiate font with HarfBuzz/FriBidi RAQM layout engine."""
    p = BLD_FONT_PATH if bold else REG_FONT_PATH
    if p and Path(p).exists():
        return ImageFont.truetype(p, size, layout_engine=ImageFont.Layout.RAQM)
    return ImageFont.load_default()


def draw_arrow_down(draw: ImageDraw.ImageDraw, x: int, y1: int, y2: int, color: str = "#2E5BFF", width: int = 4, head_size: int = 12):
    """Draw a vertical downward arrow with a filled triangular arrowhead."""
    draw.line([(x, y1), (x, y2)], fill=color, width=width)
    draw.polygon(
        [(x, y2), (x - head_size, y2 - int(head_size * 1.5)), (x + head_size, y2 - int(head_size * 1.5))],
        fill=color,
    )


def draw_arrow_left(draw: ImageDraw.ImageDraw, x1: int, x2: int, y: int, color: str = "#2E5BFF", width: int = 4, head_size: int = 12):
    """Draw a horizontal leftward arrow (RTL flow) with a filled triangular arrowhead."""
    draw.line([(x1, y), (x2, y)], fill=color, width=width)
    draw.polygon(
        [(x2, y), (x2 + int(head_size * 1.5), y - head_size), (x2 + int(head_size * 1.5), y + head_size)],
        fill=color,
    )


def create_diagram1(assets_dir: Path):
    """Generate Diagram 1: Three-Tier Architecture & Actors."""
    img = Image.new("RGB", (2000, 1100), color="#F8F9FD")
    draw = ImageDraw.Draw(img)

    # Title & Subtitle
    draw.text((1000, 45), "هيكلية النظام الشاملة والأطراف الثلاثة الفاعلة", fill="#1B2A4A", font=get_font(True, 38), direction="rtl", anchor="mm")
    draw.text((1000, 95), "Three-Tier Architecture & Authority Boundaries", fill="#64748B", font=get_font(False, 22), direction="ltr", anchor="mm")

    # Node 1: Platform Owner
    draw.rounded_rectangle([(450, 150), (1550, 320)], radius=20, fill="#1B2A4A", outline="#2E5BFF", width=3)
    draw.text((1000, 195), "1. مالك المنصة", fill="#FFFFFF", font=get_font(True, 30), direction="rtl", anchor="mm")
    draw.text((1000, 235), "Factory Platform Owner", fill="#93C5FD", font=get_font(False, 20), direction="ltr", anchor="mm")
    draw.text((1000, 280), "إدارة عروض الأسعار • تفعيل المستأجرين • إصدار التراخيص المستقلة", fill="#E2E8F0", font=get_font(False, 21), direction="rtl", anchor="mm")

    # Arrow 1 to 2
    draw_arrow_down(draw, 1000, 320, 430, color="#2E5BFF", width=5, head_size=14)
    draw.text((1035, 375), "تفعيل فوري للمتجر ومنح الصلاحيات", fill="#2E5BFF", font=get_font(True, 20), direction="rtl", anchor="lm")

    # Node 2: Tenant Store Owner
    draw.rounded_rectangle([(450, 430), (1550, 600)], radius=20, fill="#1E3A8A", outline="#3B82F6", width=3)
    draw.text((1000, 475), "2. صاحب المتجر والعميل", fill="#FFFFFF", font=get_font(True, 30), direction="rtl", anchor="mm")
    draw.text((1000, 515), "Tenant Store Owner", fill="#93C5FD", font=get_font(False, 20), direction="ltr", anchor="mm")
    draw.text((1000, 560), "إدارة المنتجات • ربط مزودي الخدمات APIs • تحديد الأسعار والمحفظة", fill="#E2E8F0", font=get_font(False, 21), direction="rtl", anchor="mm")

    # Arrow 2 to 3
    draw_arrow_down(draw, 1000, 600, 710, color="#10B981", width=5, head_size=14)
    draw.text((1035, 655), "عرض متجر تيليجرام Mini App", fill="#059669", font=get_font(True, 20), direction="rtl", anchor="lm")

    # Node 3: End Shopper
    draw.rounded_rectangle([(450, 710), (1550, 880)], radius=20, fill="#065F46", outline="#10B981", width=3)
    draw.text((1000, 755), "3. المشتري النهائي", fill="#FFFFFF", font=get_font(True, 30), direction="rtl", anchor="mm")
    draw.text((1000, 795), "End Shopper", fill="#A7F3D0", font=get_font(False, 20), direction="ltr", anchor="mm")
    draw.text((1000, 840), "تصفح المنتجات • الشحن والدفع الآمن • استلام الطلب الفوري", fill="#E2E8F0", font=get_font(False, 21), direction="rtl", anchor="mm")

    # Right Card: Financial Security
    draw.rounded_rectangle([(1590, 250), (1940, 770)], radius=18, fill="#FFFFFF", outline="#E2E8F0", width=2)
    draw.text((1765, 300), "الأمان المالي", fill="#1E293B", font=get_font(True, 26), direction="rtl", anchor="mm")
    draw.text((1765, 370), "عزل بيانات تام", fill="#334155", font=get_font(True, 21), direction="rtl", anchor="mm")
    draw.text((1765, 405), "Multi-Tenant Isolation", fill="#64748B", font=get_font(False, 17), direction="ltr", anchor="mm")
    draw.text((1765, 480), "دفتر أستاذ مزدوج", fill="#334155", font=get_font(True, 21), direction="rtl", anchor="mm")
    draw.text((1765, 515), "Double-Entry Ledger", fill="#64748B", font=get_font(False, 17), direction="ltr", anchor="mm")
    draw.text((1765, 590), "استرجاع قطعي", fill="#334155", font=get_font(True, 21), direction="rtl", anchor="mm")
    draw.text((1765, 625), "Idempotent Refund", fill="#64748B", font=get_font(False, 17), direction="ltr", anchor="mm")

    # Left Card: Infrastructure
    draw.rounded_rectangle([(60, 250), (410, 770)], radius=18, fill="#FFFFFF", outline="#E2E8F0", width=2)
    draw.text((235, 300), "البنية التحتية", fill="#1E293B", font=get_font(True, 26), direction="rtl", anchor="mm")
    draw.text((235, 370), "قاعدة PostgreSQL", fill="#334155", font=get_font(True, 21), direction="rtl", anchor="mm")
    draw.text((235, 405), "معزولة برقم المستأجر", fill="#64748B", font=get_font(False, 18), direction="rtl", anchor="mm")
    draw.text((235, 480), "طوابير مهام Redis", fill="#334155", font=get_font(True, 21), direction="rtl", anchor="mm")
    draw.text((235, 515), "وعمال خلفيون متينون", fill="#64748B", font=get_font(False, 18), direction="rtl", anchor="mm")
    draw.text((235, 590), "خزنة المفاتيح المشفرة", fill="#334155", font=get_font(True, 21), direction="rtl", anchor="mm")
    draw.text((235, 625), "SecretStorage Vault", fill="#64748B", font=get_font(False, 17), direction="ltr", anchor="mm")

    out_path = assets_dir / "diagram1_system_architecture.png"
    img.save(out_path, dpi=(300, 300))
    print(f"Generated: {out_path}")


def create_diagram2(assets_dir: Path):
    """Generate Diagram 2: Sales Funnel & Onboarding Pipeline (RTL flow)."""
    img = Image.new("RGB", (2000, 750), color="#F8F9FD")
    draw = ImageDraw.Draw(img)

    # Title & Subtitle
    draw.text((1000, 45), "مسار مبيعات وتجهيز المتاجر للمستأجرين", fill="#1B2A4A", font=get_font(True, 36), direction="rtl", anchor="mm")
    draw.text((1000, 95), "Sales & Onboarding Pipeline • From Inquiry to Independent Deployment", fill="#64748B", font=get_font(False, 20), direction="ltr", anchor="mm")

    steps = [
        ("1. تقديم الطلب", ["اختيار النشاط والقالب", "تحديد المتطلبات", "عبر رابط /build"], "#2563EB"),
        ("2. عرض السعر", ["حساب رسمي معتمد", "تسعير فوري ومجمد", "Immutable Quote"], "#1D4ED8"),
        ("3. تفعيل المتجر", ["إنشاء بيئة المتجر", "تفعيل بنقرة زر واحدة", "1-Click Onboard"], "#0D9488"),
        ("4. تسليم الإدارة", ["رابط دخول مباشر", "صلاحية المالك الكاملة", "لوحة تحكم OWNER"], "#059669"),
        ("5. ترخيص السيرفر", ["حزمة مشفرة اختيارية", "نقل ذاتي إلى VPS", "تشغيل مستقل تماماً"], "#047857"),
    ]

    card_w = 320
    card_h = 360
    spacing = 50
    start_x = 100

    # Draw steps reversed for RTL reading (Step 1 is on the right, Step 5 on the left)
    for i, (title, lines, color) in enumerate(reversed(steps)):
        x = start_x + i * (card_w + spacing)
        y = 160
        draw.rounded_rectangle([(x, y), (x + card_w, y + card_h)], radius=16, fill="#FFFFFF", outline=color, width=3)
        draw.rounded_rectangle([(x, y), (x + card_w, y + 80)], radius=14, fill=color, outline=color)
        draw.text((x + card_w // 2, y + 40), title, fill="#FFFFFF", font=get_font(True, 24), direction="rtl", anchor="mm")

        line_y = y + 140
        for j, line in enumerate(lines):
            is_sub = (j == 2)
            draw.text(
                (x + card_w // 2, line_y),
                line,
                fill="#64748B" if is_sub else "#1E293B",
                font=get_font(not is_sub, 18 if is_sub else 20),
                direction="ltr" if is_sub and ("Quote" in line or "Onboard" in line or "OWNER" in line) else "rtl",
                anchor="mm",
            )
            line_y += 55

        # Arrow pointing LEFT towards next sequential step
        if i > 0:
            prev_card_right = start_x + (i - 1) * (card_w + spacing) + card_w
            curr_card_left = x
            arrow_y = y + card_h // 2
            draw_arrow_left(draw, curr_card_left - 10, prev_card_right + 10, arrow_y, color="#0D9488", width=4, head_size=12)

    out_path = assets_dir / "diagram2_sales_onboarding_flow.png"
    img.save(out_path, dpi=(300, 300))
    print(f"Generated: {out_path}")


def create_diagram3(assets_dir: Path):
    """Generate Diagram 3: Shopper Checkout & Fulfillment Lifecycle (RTL flow)."""
    img = Image.new("RGB", (2000, 850), color="#F8F9FD")
    draw = ImageDraw.Draw(img)

    # Title & Subtitle
    draw.text((1000, 45), "مسار الشراء والتنفيذ الفوري المضمون للمشتري", fill="#1B2A4A", font=get_font(True, 36), direction="rtl", anchor="mm")
    draw.text((1000, 95), "Checkout & Fulfillment Engine • Automated Delivery & Instant Ledger Refund", fill="#64748B", font=get_font(False, 20), direction="ltr", anchor="mm")

    f_steps = [
        ("1. تصفح المتجر", ["اختيار المنتجات", "أو أرقام التفعيل", "Telegram Mini App"], "#6366F1"),
        ("2. الدفع المالي", ["نجوم تيليجرام Stars", "عملات رقمية Crypto", "أو دفع فيات مباشر"], "#4F46E5"),
        ("3. قيد الدفتر المزدوج", ["خصم محاسبي آمن", "معاملة مالية موثقة", "Ledger Invariant"], "#0284C7"),
        ("4. وظيفة خلفية", ["تنفيذ غير متزامن", "إعادة محاولة مرنة", "Durable Worker"], "#0D9488"),
        ("5. تسليم فوري", ["إرسال الكود مباشرة", "أو شحن الحساب فوراً", "إشعار فوري للمشتري"], "#059669"),
    ]

    card_w = 320
    card_h = 360
    spacing = 50
    start_x = 100

    for i, (title, lines, color) in enumerate(reversed(f_steps)):
        x = start_x + i * (card_w + spacing)
        y = 150
        draw.rounded_rectangle([(x, y), (x + card_w, y + card_h)], radius=16, fill="#FFFFFF", outline=color, width=3)
        draw.rounded_rectangle([(x, y), (x + card_w, y + 80)], radius=14, fill=color, outline=color)
        draw.text((x + card_w // 2, y + 40), title, fill="#FFFFFF", font=get_font(True, 22), direction="rtl", anchor="mm")

        line_y = y + 140
        for j, line in enumerate(lines):
            is_sub = (j == 2)
            draw.text(
                (x + card_w // 2, line_y),
                line,
                fill="#64748B" if is_sub else "#1E293B",
                font=get_font(not is_sub, 18 if is_sub else 20),
                direction="ltr" if is_sub and ("Telegram" in line or "Ledger" in line or "Worker" in line) else "rtl",
                anchor="mm",
            )
            line_y += 55

        if i > 0:
            prev_card_right = start_x + (i - 1) * (card_w + spacing) + card_w
            curr_card_left = x
            arrow_y = y + card_h // 2
            draw_arrow_left(draw, curr_card_left - 10, prev_card_right + 10, arrow_y, color="#0284C7", width=4, head_size=12)

    # Bottom guarantee banner
    draw.rounded_rectangle([(200, 680), (1800, 770)], radius=16, fill="#ECFDF5", outline="#10B981", width=3)
    draw.text(
        (1000, 725),
        "ضمان قطعي: في حال تعطل المزود أو نفاد المخزون، يُعاد المبلغ للمحفظة فوراً دون أي تدخل يدوي",
        fill="#065F46",
        font=get_font(True, 23),
        direction="rtl",
        anchor="mm",
    )

    out_path = assets_dir / "diagram3_order_fulfillment_flow.png"
    img.save(out_path, dpi=(300, 300))
    print(f"Generated: {out_path}")


def create_diagrams():
    """Generate all diagrams into docs/reports/assets/."""
    assets_dir = Path("docs/reports/assets")
    assets_dir.mkdir(parents=True, exist_ok=True)
    create_diagram1(assets_dir)
    create_diagram2(assets_dir)
    create_diagram3(assets_dir)


if __name__ == "__main__":
    create_diagrams()
