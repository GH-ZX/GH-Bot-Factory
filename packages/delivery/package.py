"""Build a bounded, secret-free wrapper around an already encrypted tenant export."""
from __future__ import annotations

import hashlib
import html
import io
import json
import re
import zipfile
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_mock_engine

from packages.core.models import Base
from packages.marketplace.tenant_bundle import schema_fingerprint

IMAGE = re.compile(r"^[a-z0-9][a-z0-9./:_-]{0,175}@sha256:[a-f0-9]{64}$")
ROOT = Path(__file__).resolve().parents[2]


def fresh_database_sql() -> tuple[str, str]:
    """Current schema bootstrap, not a replacement for incremental Alembic upgrades."""
    revision = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini"))).get_current_head()
    if not revision or not re.fullmatch(r"[a-zA-Z0-9_]+", revision):
        raise ValueError("A single migration head is required for packaging.")
    statements = ["-- Fresh, empty dedicated database only. Never run on an existing installation.", "BEGIN;"]
    def collect(sql, *args, **kwargs):
        statements.append(str(sql.compile(dialect=engine.dialect)).strip() + ";")
    engine = create_mock_engine("postgresql://", collect)
    Base.metadata.create_all(engine, checkfirst=False)
    statements.extend([
        "INSERT INTO system_install_state (id, is_initialized) VALUES (1, false);",
        "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY);",
        f"INSERT INTO alembic_version (version_num) VALUES ('{revision}');",
    ])
    # Supabase REST roles never receive access to backend-owned commerce tables.
    # Runtime connections must use a server-only owner role, never anon/service API keys.
    for name in sorted(Base.metadata.tables):
        if not re.fullmatch(r"[a-z_]+", name):
            raise ValueError("Unexpected database identifier.")
        statements.append(f'ALTER TABLE "{name}" ENABLE ROW LEVEL SECURITY;')
        for role in ("anon", "authenticated"):
            statements.append(
                f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN "
                f'REVOKE ALL ON TABLE "{name}" FROM {role}; END IF; END $$;'
            )
    statements.append("COMMIT;")
    return "\n\n".join(statements) + "\n", revision


def report(name: str, language: str, image: str, revision: str, summary: dict | None = None) -> str:
    arabic = language == "ar"
    title = "تقرير تسليم المتجر" if arabic else "Your store handoff"
    paragraphs = ([
        "تملك تشغيل هذا المتجر وبياناته. لا يلزم اشتراك أو بوت خاص بمالك المصنع.",
        "تحتوي الحزمة على إعداد Docker وملف قاعدة البيانات ونسخة مشفرة من بيانات المتجر. يجب حفظ كلمة مرور النسخة في مكان منفصل وآمن.",
        "جهّز قاعدة PostgreSQL أو Supabase مخصصة وفارغة. شغّل ملف 01-database.sql مرة واحدة فقط. استخدم اتصال قاعدة بيانات خلفي؛ لا تضع مفاتيح قاعدة البيانات في المتصفح.",
        "أكمل الإعدادات المحلية في ملف .env، ثم استورد النسخة المشفرة بعد إيقاف النسخة القديمة. لا تشغّل البوت في خادمين في الوقت نفسه.",
        "تتضمن الواجهة اختيار المظهر واللغة والمنتجات والطلبات وشحن المحفظة وتذاكر الدعم. تحتاج الموردات وطرق الدفع إلى إعداد صحيح قبل البيع.",
        "الدعم اختياري: يمكنك منح وصول تشخيصي محدود ومؤقت وسحبه. لا يشمل كلمات المرور أو معلومات المشترين. تُسجل المشكلات والإصلاحات في لوحة الإدارة.",
        "قبل التحديث، وافق على الإصدار واحفظ نسخة من قاعدة البيانات وخزنة الأسرار المشفرة. قد يتطلب الرجوع استعادة قاعدة البيانات والخزنة مع الصورة السابقة.",
        "هذه حزمة تسليم وليست شهادة جاهزية للإنتاج. يلزم التحقق من التثبيت والشراء والدفع والاستعادة بإذن المالك قبل الإطلاق.",
    ] if arabic else [
        "You own this installation and its data. No subscription or factory-owner Telegram bot is required.",
        "Included: Docker configuration, a database initialization file, and encrypted tenant data. Keep the export passphrase separately and securely.",
        "Use a dedicated empty PostgreSQL or Supabase database. Run 01-database.sql once only. Use a server-side database connection, never a browser database key.",
        "Complete the destination .env, stop the old runtime, and import the encrypted bundle. Never run the same bot on two servers simultaneously.",
        "The storefront includes appearance and language choices, products, orders, wallet funding and support tickets. Suppliers and payment methods must be configured before selling.",
        "Support is optional. You can authorize and revoke temporary limited diagnostics. Access excludes passwords and shopper information. Problems and fixes have an admin history.",
        "Before updating, approve the release and back up both database and encrypted vault. Rolling back may require restoring both alongside the previous image.",
        "This package is not evidence of production readiness. Installation, purchase, payment and restore acceptance remain required before launch, when authorized by the owner.",
    ])
    labels = {"release_version": "الإصدار" if arabic else "Release", "destination_label": "الوجهة" if arabic else "Destination", "release_notes": "محتويات الإصدار" if arabic else "Release notes", "installation_notes": "تعليمات التثبيت" if arabic else "Installation notes"}
    details = "".join(f"<h3>{label}</h3><p style='white-space:pre-wrap'>{html.escape(str((summary or {}).get(key, '')))}</p>" for key, label in labels.items() if (summary or {}).get(key))
    return f'''<!doctype html><html lang="{language}" dir="{'rtl' if arabic else 'ltr'}"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title><style>body{{font-family:Arial,sans-serif;background:#f3f6f2;color:#19392e;line-height:1.9;margin:0}}main{{max-width:800px;margin:40px auto;padding:36px;background:white;border-top:6px solid #166b52;border-radius:16px}}h1{{line-height:1.4}}p{{margin:20px 0}}code{{direction:ltr;display:block;overflow-wrap:anywhere;font-size:12px}}@media print{{body{{background:white}}main{{margin:0}}}}</style><main><small>GH STORE · BOT FACTORY</small><h1>{title}</h1><h2>{html.escape(name)}</h2>{''.join('<p>'+html.escape(p)+'</p>' for p in paragraphs)}<code>{html.escape(image)}</code><code>Schema: {html.escape(revision)}</code>{details}</main></html>'''


def build_package(*, ciphertext: bytes, tenant_name: str, tenant_id: str, image: str, language: str, delivery_summary: dict | None = None) -> bytes:
    if not IMAGE.fullmatch(image) or language not in {"en", "ar"}:
        raise ValueError("A digest-pinned image and English or Arabic report are required.")
    sql, revision = fresh_database_sql()
    files = {
        "01-database.sql": sql.encode(),
        "tenant.ghbf.enc": ciphertext,
        "REPORT.html": report(tenant_name, language, image, revision, delivery_summary).encode(),
        "DELIVERY.json": json.dumps(delivery_summary or {}, ensure_ascii=False, indent=2).encode(),
    }
    for name in ("compose.yaml", ".env.example", "SETUP.md", "UPDATE-RESTORE.md", "configure.py", "check_release.py", "install.sh", "update.sh", "preflight.py", "lifecycle.py", "update-approval.example.json", "backup.sh"):
        files[name] = (ROOT / "packages/delivery/templates" / name).read_bytes()
    files[".env.example"] = files[".env.example"].replace(b"IMAGE_PLACEHOLDER", image.encode())
    files["manifest.json"] = json.dumps({
        "format": "ghbf-customer-package-v1", "tenant_id": tenant_id, "image": image,
        "schema_revision": revision, "schema_fingerprint": schema_fingerprint(),
        "report_language": language, "verification": "pending-owner-authorized-acceptance",
        "files": {name: hashlib.sha256(content).hexdigest() for name, content in files.items()},
    }, indent=2).encode()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            info = zipfile.ZipInfo(name)
            info.external_attr = (0o100700 if name.endswith(".sh") else 0o100600) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content)
    return output.getvalue()
