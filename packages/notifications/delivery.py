from __future__ import annotations

import html
import re
from typing import Any


def normalize_good_item(raw: Any) -> str:
    """Normalize delivered credentials, voucher PINs, tokens, or dictionaries into a clean string."""
    if raw is None:
        return ""
    if isinstance(raw, dict):
        val = (
            raw.get("account_data")
            or raw.get("value")
            or raw.get("data")
            or raw.get("credentials")
            or raw.get("key")
            or raw.get("code")
            or raw.get("token")
            or raw.get("pin")
            or raw.get("account")
            or (f"{raw['email']}:{raw['password']}" if "email" in raw and "password" in raw else None)
            or (f"{raw['username']}:{raw['password']}" if "username" in raw and "password" in raw else None)
            or ""
        )
        return str(val).strip()
    return str(raw).strip()


def extract_clean_goods(raw_goods: Any) -> list[str]:
    """Extract a list of non-empty delivered credential/voucher strings."""
    if not raw_goods:
        return []
    if isinstance(raw_goods, (list, tuple)):
        items = list(raw_goods)
    else:
        items = [raw_goods]
    result: list[str] = []
    for it in items:
        cleaned = normalize_good_item(it)
        if cleaned:
            result.append(cleaned)
    return result


def is_bulk_delivery(goods: list[str]) -> bool:
    """Determine if delivered goods represent bulk delivery requiring a file attachment."""
    return len(goods) >= 2 or (len(goods) == 1 and len(goods[0]) > 300)


def build_order_goods_txt(order_identifier: str, product_name: str, goods: list[str]) -> tuple[str, bytes]:
    """Build a plain UTF-8 text file containing one credential/voucher per line.

    Matches provider manual delivery format (verbatim strings separated by newlines).
    Returns (filename, file_bytes).
    """
    clean_items = extract_clean_goods(goods)
    raw_slug = re.sub(r"[^a-zA-Z0-9_]+", "_", product_name.strip()).strip("_").lower()
    slug = raw_slug[:24] if raw_slug else "goods"
    filename = f"order_{order_identifier}_{slug}.txt"

    content = "\n".join(clean_items)
    if content:
        content += "\n"
    return filename, content.encode("utf-8")


def format_delivery_text(
    order_identifier: str,
    product_name: str,
    total_paid: float | str,
    currency: str,
    goods: list[str],
    instructions: list[str] | None = None,
    is_ar: bool = True,
) -> str:
    """Format delivery message text safely with copyable <code> blocks, avoiding Telegram limits."""
    safe_name = html.escape(product_name or f"Order #{order_identifier}", quote=False)
    clean_items = extract_clean_goods(goods)
    count = len(clean_items)

    lines: list[str] = []
    if is_ar:
        lines.append("🎉 <b>تم تأكيد وتسليم طلبك بنجاح! | Order Confirmed</b>\n")
        lines.append(f"📦 <b>رقم الطلب:</b> <code>#{order_identifier}</code>")
        lines.append(f"🛍️ <b>المنتج:</b> {safe_name}")
        lines.append(f"💰 <b>المبلغ المدفوع:</b> {total_paid} {currency}")
    else:
        lines.append(f"🎉 <b>Your order #{order_identifier} is ready!</b>\n")
        lines.append(f"📦 <b>Order ID:</b> <code>#{order_identifier}</code>")
        lines.append(f"🛍️ <b>Product:</b> {safe_name}")
        lines.append(f"💰 <b>Amount paid:</b> {total_paid} {currency}")

    if count == 0:
        if is_ar:
            lines.append("\n⏳ <b>حالة التسليم:</b> جاري التجهيز والتفعيل، سيتم إرسال البيانات فور اكتمالها.")
        else:
            lines.append("\n⏳ <b>Delivery Status:</b> Activation in progress, delivery shortly.")
    elif count > 3:
        # Bulk delivery: summarize and show a 3-item preview
        if is_ar:
            lines.append(f"\n📦 <b>إجمالي الكمية المسلمة:</b> <code>{count}</code> عنصر / كود")
            lines.append("📄 <b>تم إرفاق جميع الأكواد/البيانات في ملف نصي (TXT) أدناه لسهولة الاستخدام.</b>\n")
            lines.append("🔍 <b>معاينة سريعة (أول 3 عناصر):</b>")
            for i, g in enumerate(clean_items[:3], 1):
                safe_g = html.escape(g, quote=False)
                lines.append(f"{i}. <code>{safe_g}</code>")
            lines.append(f"<i>... وبقية العناصر ({count} إجمالي) داخل الملف المرفق أدناه 📄</i>")
        else:
            lines.append(f"\n📦 <b>Total Delivered Items:</b> <code>{count}</code> items / codes")
            lines.append("📄 <b>All credentials attached as a .txt file below for easy copying.</b>\n")
            lines.append("🔍 <b>Quick preview (first 3 items):</b>")
            for i, g in enumerate(clean_items[:3], 1):
                safe_g = html.escape(g, quote=False)
                lines.append(f"{i}. <code>{safe_g}</code>")
            lines.append(f"<i>... and the remaining items ({count} total) in the attached file below 📄</i>")
    else:
        # 1 to 3 items: display all directly with copyable code blocks
        if is_ar:
            lines.append("\n🔑 <b>بيانات التفعيل / الأكواد:</b>")
            for g in clean_items:
                safe_g = html.escape(g, quote=False)
                lines.append(f"🔑 <code>{safe_g}</code>")
            lines.append("<i>(انقر على أي كود بالأعلى للنسخ الفوري)</i>")
            if count >= 2:
                lines.append("📄 <i>تم إرفاق نسخة كملف نصي (TXT) أدناه أيضاً.</i>")
        else:
            lines.append("\n🔑 <b>Your Voucher / Credentials:</b>")
            for g in clean_items:
                safe_g = html.escape(g, quote=False)
                lines.append(f"🔑 <code>{safe_g}</code>")
            lines.append("<i>(Tap any code above to copy)</i>")
            if count >= 2:
                lines.append("📄 <i>A backup .txt file is also attached below.</i>")

    if instructions:
        clean_steps = [str(s).strip() for s in instructions if str(s).strip()]
        if clean_steps:
            if is_ar:
                lines.append("\n📋 <b>خطوات التفعيل والاستخدام:</b>")
                for s in clean_steps[:4]:
                    lines.append(f"• {html.escape(s, quote=False)}")
            else:
                lines.append("\n📝 <b>Activation Guide:</b>")
                for i, s in enumerate(clean_steps[:4], 1):
                    lines.append(f"{i}. {html.escape(s, quote=False)}")

    if is_ar:
        lines.append("\n💡 <i>يمكنك دائماً مراجعة تفاصيل هذا الطلب وضمانه عبر المتجر السريع (Mini App).</i>")
    else:
        lines.append("\n💡 <i>You can view your full order details and warranty in the Mini App anytime.</i>")

    return "\n".join(lines)
