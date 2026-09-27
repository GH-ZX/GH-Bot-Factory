#!/usr/bin/env python3
"""
Script to generate the complete system flows document in Arabic (RTL Word document)
with embedded high-resolution architecture diagrams.
Creates docs/reports/GH_Bot_Factory_Full_Flows_AR.docx
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import nsdecls, qn
from docx.shared import Inches, Pt, RGBColor

from scripts.generate_report_diagrams import create_diagrams


def set_paragraph_rtl(paragraph, align=WD_ALIGN_PARAGRAPH.RIGHT):
    """Set right-to-left direction and alignment on paragraph."""
    paragraph.alignment = align
    pPr = paragraph._p.get_or_add_pPr()
    bidi = OxmlElement('w:bidi')
    pPr.append(bidi)


def add_rtl_run(paragraph, text, font_name="Calibri", size_pt=11, bold=False, italic=False, color_rgb=None):
    """Add run with RTL Arabic typography."""
    run = paragraph.add_run(text)
    run.bold = bold
    run.italic = italic
    run.font.name = font_name
    run.font.size = Pt(size_pt)
    if color_rgb:
        run.font.color.rgb = color_rgb

    # Set complex script font for Arabic
    rPr = run._r.get_or_add_rPr()
    rFonts = OxmlElement('w:rFonts')
    rFonts.set(qn('w:ascii'), font_name)
    rFonts.set(qn('w:hAnsi'), font_name)
    rFonts.set(qn('w:cs'), font_name)
    rPr.append(rFonts)

    rtl = OxmlElement('w:rtl')
    rPr.append(rtl)
    return run


def add_diagram(doc, image_path: Path, caption_text: str):
    """Insert a centered diagram image and an Arabic caption."""
    if not image_path.exists():
        return
    p_img = doc.add_paragraph()
    p_img.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_img.paragraph_format.space_before = Pt(8)
    p_img.paragraph_format.space_after = Pt(4)
    run = p_img.add_run()
    run.add_picture(str(image_path), width=Inches(6.2))

    p_cap = doc.add_paragraph()
    set_paragraph_rtl(p_cap, WD_ALIGN_PARAGRAPH.CENTER)
    p_cap.paragraph_format.space_before = Pt(0)
    p_cap.paragraph_format.space_after = Pt(12)
    add_rtl_run(p_cap, f"مخطط رقمي توضيحي: {caption_text}", font_name="Calibri", size_pt=9.5, italic=True, color_rgb=RGBColor(100, 110, 125))


def create_document():
    # Ensure diagrams are freshly rendered
    create_diagrams()

    output_dir = Path("docs/reports")
    output_dir.mkdir(parents=True, exist_ok=True)
    assets_dir = output_dir / "assets"
    doc_path = output_dir / "GH_Bot_Factory_Full_Flows_AR.docx"

    doc = Document()

    # Set standard margins (1 inch)
    for section in doc.sections:
        section.top_margin = Inches(1)
        section.bottom_margin = Inches(1)
        section.left_margin = Inches(1)
        section.right_margin = Inches(1)

    primary_color = RGBColor(27, 42, 74)     # Deep Navy
    accent_color = RGBColor(46, 91, 255)     # Vibrant Blue
    text_color = RGBColor(40, 44, 52)        # Charcoal
    muted_color = RGBColor(100, 110, 125)    # Slate Gray

    # Document Header / Title
    p_title = doc.add_paragraph()
    set_paragraph_rtl(p_title, WD_ALIGN_PARAGRAPH.RIGHT)
    add_rtl_run(p_title, "دليل مسارات وتشغيل منصة GH-Bot-Factory", font_name="Arial", size_pt=22, bold=True, color_rgb=primary_color)

    p_sub = doc.add_paragraph()
    set_paragraph_rtl(p_sub, WD_ALIGN_PARAGRAPH.RIGHT)
    add_rtl_run(p_sub, "تقرير تنفيذي مصور يوضح المسارات الكاملة لجميع أطراف النظام بالرسومات والمخططات", font_name="Arial", size_pt=12, italic=True, color_rgb=muted_color)

    # Decorative Line
    p_line = doc.add_paragraph()
    set_paragraph_rtl(p_line)
    add_rtl_run(p_line, "—" * 45, color_rgb=accent_color)

    # Overview Section
    p_h1 = doc.add_paragraph()
    set_paragraph_rtl(p_h1)
    add_rtl_run(p_h1, "1. نظرة عامة وهيكلية النظام (Overview)", font_name="Arial", size_pt=16, bold=True, color_rgb=primary_color)

    p_intro = doc.add_paragraph()
    set_paragraph_rtl(p_intro)
    add_rtl_run(p_intro, "مشروع GH-Bot-Factory هو منصة تجارة رقمية متكاملة متعددة المستأجرين (Multi-Tenant) مبنية خصيصاً لمنظومة تيليجرام (Telegram Bots & Mini Apps). ينقسم النظام إلى ثلاثة أطراف فاعلة أساسية، لكل طرف مسار عمل واضح ومستقل:", font_name="Calibri", size_pt=11, color_rgb=text_color)

    # Table of Actors
    table = doc.add_table(rows=1, cols=3)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False

    # Set table direction to RTL
    tblPr = table._tbl.tblPr
    tblBidi = OxmlElement('w:bidiVisual')
    tblPr.append(tblBidi)

    hdr_cells = table.rows[0].cells
    headers = ["الطرف الفاعل", "الدور والصلاحية", "الواجهات المستخدمة"]
    widths = [Inches(1.8), Inches(2.8), Inches(1.8)]

    for idx, text in enumerate(headers):
        hdr_cells[idx].width = widths[idx]
        shd = parse_xml(f'<w:shd {nsdecls("w")} w:fill="1B2A4A"/>')
        hdr_cells[idx]._tc.get_or_add_tcPr().append(shd)
        p = hdr_cells[idx].paragraphs[0]
        set_paragraph_rtl(p, WD_ALIGN_PARAGRAPH.RIGHT)
        add_rtl_run(p, text, font_name="Arial", size_pt=10, bold=True, color_rgb=RGBColor(255, 255, 255))

    actors_data = [
        ("مالك المنصة (Factory Owner)", "التحكم بالمنصة المركزية، استلام طلبات العملاء، إنشاء عروض الأسعار، وإعداد استضافة المتاجر أو تسليم الكود.", "لوحة التحكم (/admin)، أداة الأوامر (platformctl)"),
        ("صاحب المتجر (Tenant Owner)", "إدارة المتجر الخاص به، تحديد الهوية والاسم، إضافة المنتجات المخزنة، ربط المزودين (APIs)، وتفعيل بوابات الدفع.", "لوحة المتجر (/admin)، أمر التيليجرام (/admin)"),
        ("المشتري النهائي (Shopper)", "تصفح المنتجات في تيليجرام، شحن المحفظة الرقمية، الشراء الفوري، واستلام الأكواد/البضاعة الرقمية مباشرة.", "تطبيق تيليجرام المصغر (Mini App) وبوت المتجر")
    ]

    for role, desc, ui in actors_data:
        row_cells = table.add_row().cells
        for idx, text in enumerate([role, desc, ui]):
            row_cells[idx].width = widths[idx]
            p = row_cells[idx].paragraphs[0]
            set_paragraph_rtl(p, WD_ALIGN_PARAGRAPH.RIGHT)
            add_rtl_run(p, text, font_name="Calibri", size_pt=10, color_rgb=text_color)

    # Embed Diagram 1: System Architecture
    add_diagram(doc, assets_dir / "diagram1_system_architecture.png", "هيكلية النظام العامة ومستويات الصلاحيات الثلاثة")

    # Section 2: Platform Owner Flow
    p_h2 = doc.add_paragraph()
    set_paragraph_rtl(p_h2)
    add_rtl_run(p_h2, "2. مسار مالك المنصة (Factory Owner Flow)", font_name="Arial", size_pt=16, bold=True, color_rgb=primary_color)

    owner_steps = [
        ("الخطوة 1: استقبال طلبات العملاء الجدد", "يدخل العميل المحتمل إلى صفحة بناء البوتات (/build) ويحدد نوع البوت (تخزين، مزودين، هجين)، البوابات، ونوع الاستضافة. يُسجل الطلب تلقائياً في قاعدة البيانات كـ (Inquiry)."),
        ("الخطوة 2: مراجعة الطلب وتقديم عرض السعر الرسمي (Commercial Quote)", "يقوم المالك بمراجعة تفاصيل الطلب عبر لوحة 'Sales & Leads' أو عبر الطرفية (platformctl inquiries)، ثم ينشئ عرض سعر غير قابل للتلاعب (Immutable Quote) يحتوي على رسوم التجهيز لمرة واحدة ورسوم الاشتراك الشهري."),
        ("الخطوة 3: التفعيل الفوري للمتجر (1-Click Tenant Onboarding)", "بمجرد موافقة العميل، يضغط المالك على زر '🚀 Onboard Tenant'. يقوم النظام برمشة عين بإنشاء المتجر (Tenant)، تسجيل حساب العميل بصفة (OWNER)، تجهيز قالب البوت، وإصدار رابط دخول فوري للعميل."),
        ("الخطوة 4: التصدير للاستضافة المستقلة (Dedicated / Source License)", "في حال اشترى العميل خدمة الاستضافة على سيرفره الخاص (VPS) أو رخصة الكود: يولد المالك حزمة مشفرة (tenant-bundle) برقم ترخيص مشفر، ويتم إيقاف الربط بالمنصة السحابية لتشغيلها بسلاسة على سيرفر العميل المستقل.")
    ]

    for title, desc in owner_steps:
        p_step = doc.add_paragraph()
        set_paragraph_rtl(p_step)
        add_rtl_run(p_step, f"• {title}: ", font_name="Arial", size_pt=11, bold=True, color_rgb=accent_color)
        add_rtl_run(p_step, desc, font_name="Calibri", size_pt=11, color_rgb=text_color)

    # Embed Diagram 2: Sales and Onboarding Funnel
    add_diagram(doc, assets_dir / "diagram2_sales_onboarding_flow.png", "دورة حياة الطلب من التكوين حتى التسليم والتفعيل")

    # Section 3: Tenant Owner Flow
    p_h3 = doc.add_paragraph()
    set_paragraph_rtl(p_h3)
    add_rtl_run(p_h3, "3. مسار صاحب المتجر (Store Owner Flow)", font_name="Arial", size_pt=16, bold=True, color_rgb=primary_color)

    tenant_steps = [
        ("الدخول السلس والأمن", "يدخل صاحب المتجر عبر إرسال أمر /admin إلى بوت المتجر في تيليجرام ليحصل على رابط دخول مباشر بنقرة واحدة، أو من خلال اسم المستخدم وكلمة المرور في المتصفح."),
        ("إدارة الهوية والتخصيص", "تعديل اسم المتجر، الشعار، الألوان الرئيسية (Brand Accent)، والتصنيفات بما يناسب طبيعة تجارته."),
        ("إضافة المنتجات المخزنة (Stored Products)", "إضافة حسابات جاهزة، أكواد اشتراكات، بطاقات رقمية، أو ملفات، مع تتبع آلي للمخزون ومنع البيع المزدوج."),
        ("ربط المزودين الآليين (API Providers)", "ربط مزودي أرقام SMS، بطاقات الألعاب، والخدمات الآلية عبر إدخال المفاتيح المشفرة مباشرة، مع وضع هوامش الربح التلقائية (Markup Formula)."),
        ("تفعيل طرق الدفع", "تفعيل نجوم تيليجرام (Telegram Stars)، الدفع بالعملات الرقمية المشفرة (Crypto)، أو الدفع المحلي والفيات، مع تحديد رصيد المحافظ وسعر الصرف.")
    ]

    for title, desc in tenant_steps:
        p_step = doc.add_paragraph()
        set_paragraph_rtl(p_step)
        add_rtl_run(p_step, f"• {title}: ", font_name="Arial", size_pt=11, bold=True, color_rgb=accent_color)
        add_rtl_run(p_step, desc, font_name="Calibri", size_pt=11, color_rgb=text_color)

    doc.add_paragraph()

    # Section 4: Shopper Flow
    p_h4 = doc.add_paragraph()
    set_paragraph_rtl(p_h4)
    add_rtl_run(p_h4, "4. مسار المشتري النهائي (Shopper Flow)", font_name="Arial", size_pt=16, bold=True, color_rgb=primary_color)

    shopper_steps = [
        ("1. فتح المتجر", "يضغط المشتري على زر 'فتح المتجر' داخل محادثة البوت، فيفتح تطبيق تيليجرام المصغر (Mini App) فوراً دون الحاجة لتسجيل حساب خارجي."),
        ("2. تصفح واختيار المنتجات", "استعراض الأقسام، رؤية الأسعار المحدثة، واختيار المنتج أو الدولة المطلوبة (في خدمات الأرقام والتطبيقات)."),
        ("3. الدفع وشحن المحفظة", "الدفع عبر الرصيد الداخلي أو شحن المحفظة بنجوم تيليجرام أو الكريبتو. يتم قيد المبلغ بدفتر أستاذ محاسبي مزدوج (Double-Entry Ledger) يضمن عدم ضياع أي قرش."),
        ("4. التنفيذ والتسليم الفوري", "بمجرد إتمام الدفع، تتولى وظيفة خلفية (Background Job) تسليم الكود الرقمي أو سحب الخدمة من المزود وعرضها فوراً في شاشة المشتري مع زر نسخ فوري."),
        ("5. الضمان المالي والاسترجاع الذاتي", "في حال تعطل المزود الخارجي أو انتهاء وقت التفعيل، يقوم النظام تلقائياً وبشكل قطعي بإعادة كامل المبلغ لمحفظة المشتري دون أي تدخل يدوي.")
    ]

    for title, desc in shopper_steps:
        p_step = doc.add_paragraph()
        set_paragraph_rtl(p_step)
        add_rtl_run(p_step, f"• {title}: ", font_name="Arial", size_pt=11, bold=True, color_rgb=accent_color)
        add_rtl_run(p_step, desc, font_name="Calibri", size_pt=11, color_rgb=text_color)

    # Embed Diagram 3: Checkout and Fulfillment Flow
    add_diagram(doc, assets_dir / "diagram3_order_fulfillment_flow.png", "دورة شراء العميل والتنفيذ المضمون عبر العمال الخلفيين والدفتر المزدوج")

    # Section 5: Security and Guarantees
    p_h5 = doc.add_paragraph()
    set_paragraph_rtl(p_h5)
    add_rtl_run(p_h5, "5. القواعد الذهبية والأمان في المنصة (Core Guarantees)", font_name="Arial", size_pt=16, bold=True, color_rgb=primary_color)

    guarantees = [
        ("عزل تام للبيانات (Multi-Tenant Isolation)", "كل متجر معزول كلياً بقاعدة البيانات بواسطة tenant_id، ولا يمكن لأي عميل أو تاجر الاطلاع على بيانات متجر آخر."),
        ("دفتر حسابات مالي لا يخطئ (Double-Entry Ledger)", "لا يتم تعديل أرصدة المحافظ مباشرة، بل عبر قيود مالية غير قابلة للتعديل أو التكرار (Strict Idempotency)."),
        ("حماية المفاتيح والأسرار (Zero Plaintext Secrets)", "مفاتيح التيليجرام والمزودين لا تُخزن في قاعدة البيانات ولا تظهر في المتصفح، بل تُشفر في خزنة محلية مؤمنة (SecretStorage)."),
        ("تنفيذ خلفي مرن ضد الانقطاع (Durable Workers)", "تتم عمليات الشراء ومخاطبة المزودين عبر طوابير مهام خلفية في Redis/DB لضمان عدم تعليق واجهة المستخدم حتى عند بطء الشبكة.")
    ]

    for title, desc in guarantees:
        p_step = doc.add_paragraph()
        set_paragraph_rtl(p_step)
        add_rtl_run(p_step, f"✓ {title}: ", font_name="Arial", size_pt=11, bold=True, color_rgb=RGBColor(40, 167, 69))
        add_rtl_run(p_step, desc, font_name="Calibri", size_pt=11, color_rgb=text_color)

    # Footer note
    doc.add_paragraph()
    p_foot = doc.add_paragraph()
    set_paragraph_rtl(p_foot, WD_ALIGN_PARAGRAPH.CENTER)
    add_rtl_run(p_foot, "تم إنشاء هذا التقرير آلياً ومطابقته برمجياً مع الحالة الفعلية لمنظومة GH-Bot-Factory.", font_name="Calibri", size_pt=9, italic=True, color_rgb=muted_color)

    doc.save(str(doc_path))
    print(f"Document successfully created at: {doc_path}")


if __name__ == "__main__":
    create_document()
