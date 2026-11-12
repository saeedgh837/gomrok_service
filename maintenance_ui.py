"""Pure, shared maintenance controls for authenticated panel renderers.

Call with_maintenance_ui before with_csrf_bootstrap. Each page must contain one
__MAINTENANCE_ACTIONS__ marker inside its body. The header action container can
use irica-maint-host to opt into scoped wrapping without changing its theme.
This module performs no I/O and does not import the application runtime.
"""
from __future__ import annotations

import re


_MARKER = "__MAINTENANCE_ACTIONS__"

_UPDATE_ICON = '<svg class="irica-maint-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M20 7v5h-5M4 17v-5h5"/><path d="M6.1 7a7 7 0 0 1 11.6-2L20 8M4 16l2.3 3A7 7 0 0 0 17.9 17"/></svg>'
_HEALTH_ICON = '<svg class="irica-maint-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M3 12h4l3-8 4 16 3-8h4"/></svg>'
_CLOSE_ICON = '<svg class="irica-maint-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true" focusable="false"><path d="m6 6 12 12M18 6 6 18"/></svg>'


def actions_html() -> str:
    """Return compact secondary header controls, with no inline handlers."""
    return (
        '<div class="irica-maint-actions" lang="fa" dir="rtl">'
        '<button type="button" id="irica-maint-open-updates" class="irica-maint-button irica-maint-launcher" '
        'aria-haspopup="dialog" aria-controls="irica-maint-updates" aria-expanded="false">'
        + _UPDATE_ICON + '<span>به‌روزرسانی‌ها</span></button>'
        '<button type="button" id="irica-maint-open-health" class="irica-maint-button irica-maint-launcher" '
        'aria-haspopup="dialog" aria-controls="irica-maint-health" aria-expanded="false">'
        + _HEALTH_ICON + '<span>سلامت سرویس‌ها</span></button>'
        '<span id="irica-maint-indicator" class="irica-maint-indicator" dir="rtl" '
        'role="status" aria-live="polite" aria-atomic="true">بررسی نشده</span>'
        '<p id="irica-maint-release-feedback" class="irica-maint-note irica-maint-release-feedback" role="status" '
        'aria-live="polite" aria-atomic="true" hidden></p></div>'
    )


def _health_row(service: str, title: str, scope: str) -> str:
    return (
        f'<section id="irica-maint-health-{service}" class="irica-maint-health-row" '
        f'aria-labelledby="irica-maint-health-{service}-title">'
        '<div class="irica-maint-product-head">'
        f'<h3 id="irica-maint-health-{service}-title" class="irica-maint-section-title">{title}</h3>'
        f'<span id="irica-maint-health-{service}-status" class="irica-maint-badge">بررسی نشده</span></div>'
        f'<p id="irica-maint-health-{service}-message" class="irica-maint-copy">برای دیدن نتیجه، بررسی را شروع کنید.</p>'
        f'<p id="irica-maint-health-{service}-scope" class="irica-maint-note">{scope}</p>'
        f'<p id="irica-maint-health-{service}-readiness" class="irica-maint-note irica-maint-readiness">آمادگی سرویس تأیید نشده است.</p>'
        '</section>'
    )


def dialogs_html() -> str:
    """Return closed dialogs that remain direct body children."""
    return (
        '<dialog id="irica-maint-updates" class="irica-maint-dialog" lang="fa" dir="rtl" '
        'aria-modal="true" tabindex="-1" aria-labelledby="irica-maint-updates-title" aria-describedby="irica-maint-updates-description">'
        '<div class="irica-maint-dialog-header"><div class="irica-maint-heading"><span class="irica-maint-emblem">'
        + _UPDATE_ICON + '</span><h2 id="irica-maint-updates-title" class="irica-maint-title">به‌روزرسانی‌ها</h2></div>'
        '<button type="button" id="irica-maint-updates-close" class="irica-maint-button irica-maint-quiet">'
        + _CLOSE_ICON + '<span>بستن</span></button></div>'
        '<div class="irica-maint-body">'
        '<p id="irica-maint-updates-description" class="irica-maint-note">بررسی نسخه، نصب را شروع نمی‌کند. پیش از تأیید نصب، نسخهٔ پیشنهادی را بررسی کنید.</p>'
        '<p id="irica-maint-notice" class="irica-maint-notice" role="status" aria-live="polite" aria-atomic="true" hidden></p>'
        '<div id="irica-maint-auth" class="irica-maint-auth" hidden><p class="irica-maint-copy">نشست ورود پایان یافته یا دسترسی رد شده است. برای ادامه دوباره وارد شوید.</p>'
        '<a id="irica-maint-sign-in" class="irica-maint-button" href="/login">ورود دوباره</a></div>'
        '<div id="irica-maint-products" class="irica-maint-products">'
        '<section class="irica-maint-product" aria-labelledby="irica-maint-app-title">'
        '<div class="irica-maint-product-head"><div><h3 id="irica-maint-app-title" class="irica-maint-section-title"><bdi dir="ltr">IRICA</bdi></h3>'
        '<p class="irica-maint-note">نرم‌افزار ایریکا</p></div><span id="irica-maint-app-status" class="irica-maint-badge">بررسی نشده</span></div>'
        '<dl class="irica-maint-versions"><div class="irica-maint-version-item"><dt class="irica-maint-label">نسخهٔ نصب‌شده</dt><dd class="irica-maint-value"><bdi id="irica-maint-app-current" dir="ltr">نامشخص</bdi></dd></div>'
        '<div class="irica-maint-version-item"><dt class="irica-maint-label">نسخهٔ پیشنهادی</dt><dd class="irica-maint-value"><bdi id="irica-maint-app-offer" dir="ltr">نامشخص</bdi></dd></div></dl>'
        '<p id="irica-maint-app-message" class="irica-maint-copy">وضعیت به‌روزرسانی هنوز بررسی نشده است.</p>'
        '<progress id="irica-maint-app-progress" class="irica-maint-progress" max="100" value="0" aria-label="پیشرفت دریافت برنامه" hidden></progress>'
        '<p id="irica-maint-app-progress-label" class="irica-maint-note" hidden></p>'
        '<p id="irica-maint-app-manual" class="irica-maint-notice" hidden>در این نسخه، نصب خودکار در دسترس نیست. نسخهٔ رسمی برنامه را دستی نصب کنید؛ این بخش هیچ نصابی را اجرا نمی‌کند.</p>'
        '<p id="irica-maint-app-cleanup" class="irica-maint-note" hidden>پاک‌سازی فایل‌های موقت به‌روزرسانی هنوز تمام نشده است. این وضعیت نشانهٔ نصب نسخهٔ جدید نیست.</p>'
        '<button type="button" id="irica-maint-app-install" class="irica-maint-button irica-maint-review" disabled>بررسی به‌روزرسانی برنامه</button></section>'
        '<section class="irica-maint-product" aria-labelledby="irica-maint-driver-title">'
        '<div class="irica-maint-product-head"><div><h3 id="irica-maint-driver-title" class="irica-maint-section-title"><bdi dir="ltr">ChromeDriver</bdi></h3>'
        '<p class="irica-maint-note">درایور مرورگر</p></div><span id="irica-maint-driver-status" class="irica-maint-badge">بررسی نشده</span></div>'
        '<dl class="irica-maint-versions"><div class="irica-maint-version-item"><dt class="irica-maint-label">درایور نصب‌شده</dt><dd class="irica-maint-value"><bdi id="irica-maint-driver-current" dir="ltr">نامشخص</bdi></dd></div>'
        '<div class="irica-maint-version-item"><dt class="irica-maint-label">نسخهٔ کروم</dt><dd class="irica-maint-value"><bdi id="irica-maint-browser-version" dir="ltr">نامشخص</bdi></dd></div>'
        '<div class="irica-maint-version-item"><dt class="irica-maint-label">درایور پیشنهادی</dt><dd class="irica-maint-value"><bdi id="irica-maint-driver-offer" dir="ltr">نامشخص</bdi></dd></div></dl>'
        '<p id="irica-maint-driver-message" class="irica-maint-copy">وضعیت درایور هنوز بررسی نشده است.</p>'
        '<button type="button" id="irica-maint-driver-install" class="irica-maint-button irica-maint-review" disabled>بررسی به‌روزرسانی درایور</button></section></div>'
        '<section id="irica-maint-confirmation" class="irica-maint-confirmation" aria-labelledby="irica-maint-confirm-title" hidden>'
        '<h3 id="irica-maint-confirm-title" class="irica-maint-section-title">تأیید نصب</h3>'
        '<p class="irica-maint-copy">نسخهٔ <bdi id="irica-maint-confirm-version" dir="ltr">نامشخص</bdi> از <bdi id="irica-maint-confirm-product" dir="ltr">IRICA</bdi> نصب شود؟</p>'
        '<p id="irica-maint-confirm-detail" class="irica-maint-note">با انصراف، هیچ تغییری در نرم‌افزار ایجاد نمی‌شود.</p>'
        '<div class="irica-maint-dialog-footer"><button type="button" id="irica-maint-confirm-cancel" class="irica-maint-button">انصراف</button>'
        '<button type="button" id="irica-maint-confirm-install" class="irica-maint-button irica-maint-primary" disabled>نصب این نسخه</button></div></section>'
        '<p id="irica-maint-background" class="irica-maint-note" hidden>با بستن این پنجره، نصب در پس‌زمینه ادامه پیدا می‌کند و کار سرور لغو نمی‌شود.</p></div>'
        '<div id="irica-maint-update-tools" class="irica-maint-dialog-footer irica-maint-toolbar">'
        '<button type="button" id="irica-maint-refresh" class="irica-maint-button irica-maint-quiet">تازه‌سازی وضعیت</button>'
        '<button type="button" id="irica-maint-release-open" class="irica-maint-button irica-maint-quiet" '
        'aria-haspopup="dialog" aria-controls="irica-maint-release" aria-expanded="false">تغییرات این نسخه</button>'
        '<button type="button" id="irica-maint-check" class="irica-maint-button irica-maint-primary">بررسی نسخهٔ جدید</button></div></dialog>'
        '<dialog id="irica-maint-health" class="irica-maint-dialog" lang="fa" dir="rtl" '
        'aria-modal="true" tabindex="-1" aria-labelledby="irica-maint-health-title" aria-describedby="irica-maint-health-description">'
        '<div class="irica-maint-dialog-header"><div class="irica-maint-heading"><span class="irica-maint-emblem">'
        + _HEALTH_ICON + '</span><h2 id="irica-maint-health-title" class="irica-maint-title">سلامت سرویس‌ها</h2></div>'
        '<button type="button" id="irica-maint-health-close" class="irica-maint-button irica-maint-quiet">'
        + _CLOSE_ICON + '<span>بستن</span></button></div>'
        '<div class="irica-maint-body"><p id="irica-maint-health-description" class="irica-maint-note">بررسی‌ها فقط با انتخاب «شروع بررسی» اجرا می‌شوند و پیامی در تلگرام ارسال نمی‌شود. در دسترس بودن سرویس، ثبت اطلاعات یا آمادگی ارسال پیام را تأیید نمی‌کند.</p>'
        '<p id="irica-maint-health-notice" class="irica-maint-notice" role="status" aria-live="polite" aria-atomic="true" hidden></p>'
        '<div id="irica-maint-health-auth" class="irica-maint-auth" hidden><p class="irica-maint-copy">برای بررسی سرویس‌ها دوباره وارد شوید.</p>'
        '<a id="irica-maint-health-sign-in" class="irica-maint-button" href="/login">ورود دوباره</a></div>'
        '<div class="irica-maint-services">'
        + _health_row("cloud", "سرور ابری", "فقط ارتباط با سرور ابری بررسی می‌شود؛ ورود و همگام‌سازی بررسی نمی‌شوند.")
        + _health_row("telegram", "ارتباط تلگرام", "فقط ارتباط با تلگرام بررسی می‌شود؛ آمادگی ارسال پیام یا درستی تنظیمات ربات و گفت‌وگو تأیید نمی‌شود.")
        + _health_row("proxy", "پروکسی اختیاری", "پروکسی اختیاری است و فقط ارتباط آن بررسی می‌شود. تنظیم نبودن آن خطا نیست.")
        + '</div></div><div id="irica-maint-health-tools" class="irica-maint-dialog-footer irica-maint-toolbar">'
        '<p class="irica-maint-note">آخرین بررسی: <time id="irica-maint-health-time" dir="rtl">بررسی نشده</time></p>'
        '<button type="button" id="irica-maint-health-run" class="irica-maint-button irica-maint-primary">شروع بررسی</button></div></dialog>'
        '<dialog id="irica-maint-release" class="irica-maint-dialog irica-maint-release" lang="fa" dir="rtl" '
        'aria-modal="true" tabindex="-1" aria-labelledby="irica-maint-release-title" aria-describedby="irica-maint-release-intro">'
        '<div class="irica-maint-dialog-header"><div><h2 id="irica-maint-release-title" class="irica-maint-title">تازه‌های ایریکا</h2>'
        '<p class="irica-maint-note irica-maint-release-meta">نسخهٔ نصب‌شده <bdi id="irica-maint-release-version" dir="ltr">—</bdi></p></div>'
        '<button type="button" id="irica-maint-release-close" class="irica-maint-button irica-maint-quiet">'
        + _CLOSE_ICON + '<span>بستن</span></button></div>'
        '<div class="irica-maint-body"><p id="irica-maint-release-intro" class="irica-maint-copy">در حال آماده‌سازی خلاصهٔ تغییرات…</p>'
        '<div id="irica-maint-release-content" class="irica-maint-release-content"></div>'
        '<p id="irica-maint-release-storage" class="irica-maint-notice" hidden></p>'
        '<p id="irica-maint-release-footer" class="irica-maint-note"></p></div>'
        '<div class="irica-maint-dialog-footer irica-maint-release-footer">'
        '<button type="button" id="irica-maint-release-ack" class="irica-maint-button irica-maint-primary" disabled>متوجه شدم</button></div>'
        '</dialog>'
    )


_STYLE = r"""<style id="irica-maint-style">
.irica-maint-actions,.irica-maint-dialog{
  --irica-maint-surface:#FFFFFF;--irica-maint-canvas:#F4F7FB;--irica-maint-text:#172A43;--irica-maint-muted:#53657D;
  --irica-maint-line:#D7E1EF;--irica-maint-control-border:#7A8CA5;
  --irica-maint-accent:#2458C6;--irica-maint-accent-hover:#1947AA;--irica-maint-on-accent:#FFFFFF;--irica-maint-accent-soft:#ECF2FE;
  --irica-maint-success:#0D6B52;--irica-maint-success-soft:#EAF6F0;--irica-maint-warning:#885600;--irica-maint-warning-soft:#FFF6E6;
  --irica-maint-danger:#AF2F45;--irica-maint-danger-soft:#FFF0F2;--irica-maint-neutral:#53657D;--irica-maint-neutral-soft:#EEF2F7;
}
.irica-maint-actions:where(html[data-theme="dark"] *),.irica-maint-dialog:where(html[data-theme="dark"] *){
  --irica-maint-surface:#19263B;--irica-maint-canvas:#121D30;--irica-maint-text:#EAF0FA;--irica-maint-muted:#A9B8CE;
  --irica-maint-line:#33465F;--irica-maint-control-border:#7087A4;
  --irica-maint-accent:#9DBFFF;--irica-maint-accent-hover:#BDD3FF;--irica-maint-on-accent:#11213A;--irica-maint-accent-soft:#24395C;
  --irica-maint-success:#8ADFC0;--irica-maint-success-soft:#173C34;--irica-maint-warning:#F5CD84;--irica-maint-warning-soft:#3E321D;
  --irica-maint-danger:#FFB0BF;--irica-maint-danger-soft:#482936;--irica-maint-neutral:#B4C2D6;--irica-maint-neutral-soft:#26354D;
}
.irica-maint-host{flex-wrap:wrap;min-inline-size:0;max-inline-size:100%}
.irica-maint-actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap;min-inline-size:0;max-inline-size:100%}
.irica-maint-button{box-sizing:border-box;display:inline-flex;align-items:center;justify-content:center;gap:8px;min-block-size:44px;min-inline-size:44px;max-inline-size:100%;padding:10px 16px;border:1px solid var(--irica-maint-control-border);border-radius:8px;background:var(--irica-maint-surface);color:var(--irica-maint-text);font:inherit;font-size:13px;font-weight:600;line-height:1.6;text-decoration:none;text-align:center;white-space:normal;overflow-wrap:anywhere;cursor:pointer;touch-action:manipulation;transition:background .15s,border-color .15s,color .15s}
.irica-maint-button:hover:not(:disabled){border-color:var(--irica-maint-accent);background:var(--irica-maint-accent-soft)}
.irica-maint-button:active:not(:disabled){background:var(--irica-maint-canvas)}
.irica-maint-button:focus-visible{outline:3px solid var(--irica-maint-accent);outline-offset:3px;box-shadow:none}
.irica-maint-launcher{background:var(--irica-maint-accent-soft);border-color:transparent;color:var(--irica-maint-accent)}
.irica-maint-review{border-color:var(--irica-maint-accent);color:var(--irica-maint-accent)}
.irica-maint-quiet{background:transparent;border-color:transparent;color:var(--irica-maint-muted)}
.irica-maint-quiet:hover:not(:disabled){background:var(--irica-maint-canvas);border-color:transparent;color:var(--irica-maint-text)}
.irica-maint-primary{background:var(--irica-maint-accent);border-color:var(--irica-maint-accent);color:var(--irica-maint-on-accent)}
.irica-maint-primary:hover:not(:disabled){background:var(--irica-maint-accent-hover);color:var(--irica-maint-on-accent)}
.irica-maint-primary:active:not(:disabled){background:var(--irica-maint-accent-hover);color:var(--irica-maint-on-accent)}
.irica-maint-button:disabled{background:var(--irica-maint-canvas);border-color:var(--irica-maint-line);color:var(--irica-maint-muted);opacity:1;cursor:not-allowed}
.irica-maint-icon{flex:none;inline-size:16px;block-size:16px}
.irica-maint-indicator{display:inline-flex;align-items:center;gap:6px;max-inline-size:18ch;padding:4px 9px;border-radius:999px;background:var(--irica-maint-neutral-soft);color:var(--irica-maint-neutral);font-size:12px;line-height:1.6;overflow-wrap:anywhere}
.irica-maint-dialog{box-sizing:border-box;display:flex;flex-direction:column;position:fixed;inset:0;margin:auto;block-size:fit-content;inline-size:min(700px,calc(100% - 24px));max-inline-size:calc(100vw - 24px);max-block-size:calc(100vh - 32px);max-block-size:calc(100dvh - 32px);padding:0;overflow:hidden;border:1px solid var(--irica-maint-line);border-radius:18px;background:var(--irica-maint-surface);color:var(--irica-maint-text);font:inherit;font-size:14px;line-height:1.8;text-align:start;box-shadow:0 24px 80px rgba(8,20,42,.24)}
.irica-maint-dialog *{box-sizing:border-box}
.irica-maint-dialog:not([open]){display:none}
.irica-maint-dialog::backdrop{background:rgba(10,20,38,.58)}
.irica-maint-dialog [hidden],.irica-maint-actions [hidden]{display:none!important}
.irica-maint-dialog-header{display:flex;flex:none;align-items:center;justify-content:space-between;gap:16px;padding:20px 24px;border-block-end:1px solid var(--irica-maint-line);min-inline-size:0}
.irica-maint-heading{display:flex;align-items:center;gap:12px;min-inline-size:0}
.irica-maint-emblem{display:grid;place-items:center;flex:none;inline-size:40px;block-size:40px;border-radius:12px;background:var(--irica-maint-accent-soft);color:var(--irica-maint-accent)}
.irica-maint-emblem .irica-maint-icon{inline-size:22px;block-size:22px}
.irica-maint-title{margin:0;padding:0;font-size:21px;font-weight:750;line-height:1.6;overflow-wrap:anywhere}
.irica-maint-body{display:grid;flex:0 1 auto;gap:16px;min-block-size:0;min-inline-size:0;padding:20px 24px;overflow:auto;overscroll-behavior:contain;scroll-padding-block:16px;background:var(--irica-maint-canvas)}
.irica-maint-products,.irica-maint-services{display:grid;gap:0;min-inline-size:0;border:1px solid var(--irica-maint-line);border-radius:12px;background:var(--irica-maint-surface)}
.irica-maint-product{display:grid;gap:12px;padding:20px;min-inline-size:0;border-block-end:1px solid var(--irica-maint-line)}
.irica-maint-product:last-child,.irica-maint-health-row:last-child{border-block-end:0}
.irica-maint-product>.irica-maint-button{justify-self:start;margin-block-start:4px}
.irica-maint-product-head{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;min-inline-size:0}
.irica-maint-section-title{margin:0;padding:0;color:var(--irica-maint-text);font-size:16px;font-weight:700;line-height:1.6;overflow-wrap:anywhere}
.irica-maint-badge{display:inline-flex;align-items:center;gap:6px;max-inline-size:100%;padding:4px 10px;border:0;border-radius:999px;background:var(--irica-maint-neutral-soft);color:var(--irica-maint-neutral);font-size:12px;font-weight:600;line-height:1.7;overflow-wrap:anywhere}
.irica-maint-badge::before,.irica-maint-indicator::before{content:"";flex:none;inline-size:6px;block-size:6px;border-radius:50%;background:currentColor}
.irica-maint-badge[data-tone="success"],.irica-maint-indicator[data-tone="success"]{background:var(--irica-maint-success-soft);color:var(--irica-maint-success)}
.irica-maint-badge[data-tone="info"],.irica-maint-indicator[data-tone="info"]{background:var(--irica-maint-accent-soft);color:var(--irica-maint-accent)}
.irica-maint-badge[data-tone="warning"],.irica-maint-indicator[data-tone="warning"]{background:var(--irica-maint-warning-soft);color:var(--irica-maint-warning)}
.irica-maint-badge[data-tone="danger"],.irica-maint-indicator[data-tone="danger"]{background:var(--irica-maint-danger-soft);color:var(--irica-maint-danger)}
.irica-maint-versions{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin:0;padding:12px 14px;min-inline-size:0;border-radius:8px;background:var(--irica-maint-canvas)}
.irica-maint-version-item{min-inline-size:0}
.irica-maint-label{font-size:12px;color:var(--irica-maint-muted)}
.irica-maint-value{margin:4px 0 0;color:var(--irica-maint-text);font-size:16px;font-weight:650;line-height:1.6;overflow-wrap:anywhere;font-variant-numeric:tabular-nums}
.irica-maint-copy{margin:0;color:var(--irica-maint-text);overflow-wrap:anywhere}
.irica-maint-note{margin:0;color:var(--irica-maint-muted);font-size:13px;line-height:1.8;overflow-wrap:anywhere;max-inline-size:75ch}
.irica-maint-notice{margin:0;padding:12px 14px;border:1px solid var(--irica-maint-line);border-radius:8px;background:var(--irica-maint-accent-soft);color:var(--irica-maint-accent);font-size:13px;line-height:1.8;overflow-wrap:anywhere}
.irica-maint-notice[data-tone="danger"]{background:var(--irica-maint-danger-soft);color:var(--irica-maint-danger)}
.irica-maint-notice[data-tone="warning"]{background:var(--irica-maint-warning-soft);color:var(--irica-maint-warning)}
.irica-maint-auth{display:grid;justify-items:start;gap:12px}
.irica-maint-progress{inline-size:100%;block-size:8px;margin:0;accent-color:var(--irica-maint-accent)}
.irica-maint-confirmation{display:grid;gap:16px;min-inline-size:0;padding:20px;border:1px solid var(--irica-maint-line);border-radius:12px;background:var(--irica-maint-surface)}
.irica-maint-dialog-footer{display:flex;gap:8px;flex-wrap:wrap;align-items:center;min-inline-size:0}
.irica-maint-toolbar{flex:none;padding:16px 24px;border-block-start:1px solid var(--irica-maint-line);background:var(--irica-maint-surface)}
.irica-maint-toolbar>.irica-maint-primary{margin-inline-start:auto}
.irica-maint-health-row{display:grid;gap:8px;min-inline-size:0;padding:18px 20px;border-block-end:1px solid var(--irica-maint-line)}
.irica-maint-readiness{padding-inline-start:10px;border-inline-start:2px solid var(--irica-maint-line)}
.irica-maint-release{inline-size:min(640px,calc(100% - 24px))}
.irica-maint-release .irica-maint-body{gap:20px;background:var(--irica-maint-surface)}
.irica-maint-release .irica-maint-copy{line-height:1.9}
.irica-maint-release-meta{margin-block-start:6px;display:flex;align-items:center;gap:8px}
.irica-maint-release-meta bdi{color:var(--irica-maint-accent);font-weight:700;font-variant-numeric:tabular-nums}
.irica-maint-release-content{display:grid;gap:24px;min-inline-size:0}
.irica-maint-release-section{display:grid;gap:14px;min-inline-size:0}
.irica-maint-release-section-label{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin:0;color:var(--irica-maint-muted);font-size:12px;font-weight:600;line-height:1.8}
.irica-maint-release-list{display:grid;gap:16px;margin:0;padding:0;list-style:none;min-inline-size:0}
.irica-maint-release-item{display:grid;gap:5px;padding-inline-start:14px;border-inline-start:2px solid var(--irica-maint-line);min-inline-size:0}
.irica-maint-release-section:first-child .irica-maint-release-item{border-color:var(--irica-maint-accent)}
.irica-maint-release-item h4{margin:0;color:var(--irica-maint-text);font-size:15px;font-weight:700;line-height:1.6}
.irica-maint-release-item p{margin:0;color:var(--irica-maint-muted);line-height:1.9;overflow-wrap:anywhere}
.irica-maint-release-footer{flex:none;padding:16px 24px;background:var(--irica-maint-surface);border-block-start:1px solid var(--irica-maint-line);justify-content:flex-end}
.irica-maint-release-footer .irica-maint-primary{min-inline-size:148px}
.irica-maint-release-feedback{flex-basis:100%}
@media(max-width:480px){
.irica-maint-dialog{max-block-size:calc(100dvh - 16px);inline-size:calc(100% - 16px);max-inline-size:calc(100vw - 16px);border-radius:14px}
.irica-maint-dialog-header{padding:16px;gap:8px}
.irica-maint-heading{gap:8px}
.irica-maint-emblem{inline-size:32px;block-size:32px;border-radius:10px}
.irica-maint-title{font-size:18px}
.irica-maint-body{padding:16px}
.irica-maint-product,.irica-maint-health-row{padding:16px}
.irica-maint-versions{grid-template-columns:repeat(2,minmax(0,1fr));padding:12px;gap:12px}
.irica-maint-version-item:last-child:nth-child(odd){grid-column:1 / -1}
.irica-maint-dialog-footer .irica-maint-button{flex:1 1 130px}
.irica-maint-toolbar,.irica-maint-release-footer{padding:12px 16px}
.irica-maint-toolbar>.irica-maint-primary{flex-basis:100%;margin-inline-start:0}
.irica-maint-toolbar>.irica-maint-note{flex-basis:100%}
}
@media(max-height:480px){
.irica-maint-dialog{display:block;overflow:auto;overscroll-behavior:contain;scroll-padding-block:16px}
.irica-maint-body{overflow:visible}
}
@media(prefers-reduced-motion:reduce){
.irica-maint-button{transition:none}
}
</style>"""


_CONTROLLER = r"""<script id="irica-maint-controller">
(() => {
  'use strict';
  const byId = suffix => document.getElementById('irica-maint-' + suffix);
  const updates = byId('updates');
  const health = byId('health');
  const release = byId('release');
  if (!updates || !health || !release) return;
  const dialogControls = new Map([
    [updates, {trigger: 'open-updates', close: 'updates-close'}],
    [health, {trigger: 'open-health', close: 'health-close'}],
    [release, {trigger: 'release-open', close: 'release-close'}]
  ]);
  const progressStates = new Set(['downloading', 'verifying', 'waiting_for_idle', 'installing']);
  const knownStates = new Set(['not_checked', 'checking', 'current', 'available', 'downloading', 'verifying', 'waiting_for_idle', 'installing', 'failed']);
  const labels = {not_checked: 'بررسی نشده', checking: 'در حال بررسی', current: 'به‌روز', available: 'نسخهٔ جدید', downloading: 'در حال دریافت', verifying: 'در حال اعتبارسنجی', waiting_for_idle: 'منتظر پایان کارها', installing: 'در حال نصب', failed: 'نیاز به بررسی'};
  const updateTones = {not_checked: 'neutral', checking: 'info', current: 'success', available: 'info', downloading: 'info', verifying: 'info', waiting_for_idle: 'info', installing: 'info', failed: 'danger'};
  const descriptions = {
    not_checked: 'وضعیت به‌روزرسانی هنوز بررسی نشده است.',
    checking: 'فقط اطلاعات نسخه بررسی می‌شود؛ هیچ نرم‌افزاری نصب نمی‌شود.',
    current: 'نسخهٔ بررسی‌شده به‌روز است.',
    available: 'نسخهٔ جدید در دسترس است. پیش از نصب، نسخهٔ پیشنهادی را بررسی کنید.',
    downloading: 'نسخهٔ تأییدشده در حال دریافت است. کارهای جاری متوقف نمی‌شوند.',
    verifying: 'فایل دریافت‌شده پیش از نصب اعتبارسنجی می‌شود.',
    waiting_for_idle: 'نصب تا پایان کارهای جاری منتظر می‌ماند.',
    installing: 'نسخهٔ تأییدشده در حال نصب است.',
    failed: 'به‌روزرسانی کامل نشد. پیش از تلاش دوباره، وضعیت را تازه‌سازی کنید.'
  };
  const serviceScopes = {
    cloud: 'فقط ارتباط با سرور ابری بررسی می‌شود؛ ورود و همگام‌سازی بررسی نمی‌شوند.',
    telegram: 'فقط ارتباط با تلگرام بررسی می‌شود؛ آمادگی ارسال پیام یا درستی تنظیمات ربات و گفت‌وگو تأیید نمی‌شود.',
    proxy: 'پروکسی اختیاری است و فقط ارتباط آن بررسی می‌شود. تنظیم نبودن آن خطا نیست.'
  };
  const serviceReadiness = {
    cloud: 'آمادگی ورود و همگام‌سازی تأیید نشده است.',
    telegram: 'آمادگی برای ارسال پیام بررسی نشده است.',
    proxy: 'آمادگی سرویس‌های وابسته تأیید نشده است.'
  };
  let snapshot = null;
  let pending = null;
  const approved = {app: null, driver: null};
  const uncertain = {app: false, driver: false};
  let activeDialog = null;
  const returnFocus = new Map();
  let mutationBusy = false;
  let installRequest = null;
  let readBusy = false;
  let visibleRefreshPending = false;
  let healthBusy = false;
  let authExpired = false;
  let readError = false;
  let pageStopped = false;
  let automaticCheckSent = false;
  let releaseData = null;
  let releaseAutoPending = true;
  let releaseDeferred = false;
  let releaseReadBusy = false;
  let releaseEpoch = 0;
  let releaseToUpdates = false;
  let releasePresented = false;
  let releaseWakeQueued = false;
  let revision = 0;
  let pollTimer = null;
  let pollCount = 0;
  let pollDeadline = Date.now() + 15 * 60 * 1000;
  const reads = new Set();

  function text(suffix, value) {
    const element = byId(suffix);
    const next = String(value);
    if (element.textContent !== next) element.textContent = next;
  }
  function notice(suffix, value, tone = 'info') {
    text(suffix, value);
    byId(suffix).hidden = !value;
    byId(suffix).dataset.tone = tone;
  }
  function persianDigits(value) {
    return String(value).replace(/[0-9]/g, digit => '۰۱۲۳۴۵۶۷۸۹'[Number(digit)]);
  }
  function displayDate(timestamp) {
    if (!timestamp) return 'نامشخص';
    return new Intl.DateTimeFormat('fa-IR', {calendar: 'persian', numberingSystem: 'arabext', dateStyle: 'medium', timeStyle: 'short'}).format(new Date(timestamp));
  }
  function version(value) {
    return typeof value === 'string' && /^[vV]?\d[0-9A-Za-z.+_-]{0,79}$/.test(value) ? value : 'نامشخص';
  }
  function offerFor(kind) {
    const data = snapshot && snapshot[kind];
    const offer = data && data.offer;
    if (!offer || typeof offer.offer_id !== 'string' || !offer.offer_id || offer.offer_id.length > 1024 || /[\x00-\x20\x7f]/.test(offer.offer_id) || version(offer.version) === 'نامشخص') return null;
    return {offer_id: offer.offer_id, version: offer.version};
  }
  function sameOffer(first, second) {
    return Boolean(first && second && first.offer_id === second.offer_id && first.version === second.version);
  }
  function stateFor(kind) {
    const data = snapshot && snapshot[kind];
    if (!data || (kind === 'app' && data.code === 'not_checked' && data.state !== 'checking' && !progressStates.has(data.state)) || (data.state === 'current' && data.checked !== true)) return 'not_checked';
    return knownStates.has(data.state) ? data.state : 'not_checked';
  }
  function candidate(kind) {
    const data = snapshot && snapshot[kind];
    const state = stateFor(kind);
    if (!data || data.checked !== true || (kind === 'app' && data.can_install !== true)) return null;
    if (!['available', 'failed', 'waiting_for_idle'].includes(state)) return null;
    return offerFor(kind);
  }
  function working() {
    return Boolean(snapshot && (snapshot.checking === true || stateFor('app') === 'checking' || stateFor('driver') === 'checking' || progressStates.has(stateFor('app')) || ['waiting_for_idle', 'installing'].includes(stateFor('driver'))));
  }
  function stopPolling() {
    if (pollTimer !== null) clearTimeout(pollTimer);
    pollTimer = null;
  }
  function abortReads() {
    for (const controller of reads) controller.abort();
  }
  function hideConfirmation(restore) {
    const old = pending;
    pending = null;
    byId('confirmation').hidden = true;
    byId('products').hidden = false;
    byId('update-tools').hidden = false;
    byId('confirm-install').disabled = true;
    render();
    if (restore && old && updates.open) byId(old.kind + '-install').focus();
  }
  function render() {
    const app = snapshot && snapshot.app;
    const driver = snapshot && snapshot.driver;
    for (const kind of ['app', 'driver']) {
      const data = kind === 'app' ? app : driver;
      const state = stateFor(kind);
      const offer = offerFor(kind);
      text(kind + '-status', installRequest === kind ? 'در حال شروع نصب' : labels[state]);
      byId(kind + '-status').dataset.tone = installRequest === kind ? 'info' : updateTones[state];
      text(kind + '-current', version(data && data[kind === 'app' ? 'current_version' : 'installed_version']));
      text(kind + '-offer', offer ? offer.version : 'نامشخص');
      text(kind + '-message', descriptions[state]);
      const button = byId(kind + '-install');
      button.disabled = authExpired || mutationBusy || readBusy || readError || uncertain[kind] || !candidate(kind);
      const canRetry = state === 'waiting_for_idle' && sameOffer(approved[kind], offer);
      text(kind + '-install', canRetry ? (kind === 'app' ? 'تلاش دوباره برای نصب' : 'تلاش دوباره برای نصب درایور') : kind === 'app' ? 'بررسی به‌روزرسانی برنامه' : 'بررسی به‌روزرسانی درایور');
    }
    text('browser-version', version(driver && driver.browser_version));
    byId('app-manual').hidden = !app || app.can_install === true || stateFor('app') === 'not_checked';
    byId('app-cleanup').hidden = !(app && app.cleanup_pending === true);
    const downloading = app && stateFor('app') === 'downloading';
    const total = downloading && Number.isFinite(app.total) && app.total > 0 ? app.total : 0;
    const downloaded = downloading && Number.isFinite(app.downloaded) && app.downloaded >= 0 ? app.downloaded : 0;
    byId('app-progress').hidden = !downloading;
    byId('app-progress-label').hidden = !downloading;
    if (downloading && total) {
      const percent = Math.max(0, Math.min(100, Math.floor(downloaded / total * 100)));
      byId('app-progress').value = percent;
      text('app-progress-label', persianDigits(percent) + '٪ از نسخهٔ تأییدشده دریافت شده است.');
    } else if (downloading) {
      byId('app-progress').removeAttribute('value');
      text('app-progress-label', 'نسخهٔ تأییدشده در حال دریافت است؛ حجم کل هنوز مشخص نیست.');
    }
    byId('background').hidden = !installRequest && !(snapshot && (progressStates.has(stateFor('app')) || ['waiting_for_idle', 'installing'].includes(stateFor('driver'))));
    byId('auth').hidden = !authExpired;
    byId('health-auth').hidden = !authExpired;
    byId('check').disabled = authExpired || mutationBusy || readBusy || working();
    byId('refresh').disabled = authExpired || readBusy || mutationBusy;
    byId('health-run').disabled = authExpired || healthBusy;
    byId('release-open').disabled = authExpired || mutationBusy || Boolean(pending);
    text('check', mutationBusy && !installRequest ? 'در حال بررسی نسخه‌ها' : 'بررسی نسخهٔ جدید');
    text('health-run', healthBusy ? 'در حال بررسی' : 'شروع بررسی');
    byId('check').setAttribute('aria-busy', String(mutationBusy && !installRequest));
    byId('health-run').setAttribute('aria-busy', String(healthBusy));
    byId('confirm-install').disabled = !pending || authExpired || mutationBusy || readError || !sameOffer(pending, candidate(pending.kind));
    let indicator = 'بررسی نشده';
    let indicatorTone = 'neutral';
    if (authExpired) { indicator = 'نیاز به ورود'; indicatorTone = 'warning'; }
    else if (readError) { indicator = 'ارتباط قطع است'; indicatorTone = 'danger'; }
    else if (installRequest || (snapshot && (progressStates.has(stateFor('app')) || ['waiting_for_idle', 'installing'].includes(stateFor('driver'))))) { indicator = 'در حال انجام'; indicatorTone = 'info'; }
    else if (mutationBusy || (snapshot && (snapshot.checking || stateFor('app') === 'checking' || stateFor('driver') === 'checking'))) { indicator = 'در حال بررسی'; indicatorTone = 'info'; }
    else if (snapshot && (stateFor('app') === 'available' || stateFor('driver') === 'available')) { indicator = 'نسخهٔ جدید'; indicatorTone = 'info'; }
    else if (snapshot && (stateFor('app') === 'failed' || stateFor('driver') === 'failed')) { indicator = 'نیاز به بررسی'; indicatorTone = 'danger'; }
    else if (snapshot && stateFor('app') === 'current' && stateFor('driver') === 'current') { indicator = 'به‌روز'; indicatorTone = 'success'; }
    text('indicator', indicator);
    byId('indicator').dataset.tone = indicatorTone;
    wakeRelease();
  }
  function expireAuth() {
    authExpired = true;
    releaseAutoPending = false;
    releaseEpoch += 1;
    releaseReadBusy = false;
    releasePresented = false;
    if (release.open) closeDialog(release, true);
    notice('release-feedback', 'نشست ورود پایان یافته است. برای دیدن خلاصهٔ تغییرات دوباره وارد شوید.');
    approved.app = null;
    approved.driver = null;
    stopPolling();
    abortReads();
    hideConfirmation(false);
    notice('notice', 'نشست ورود پایان یافته یا دسترسی رد شده است. برای ادامه دوباره وارد شوید.');
    notice('health-notice', 'نشست ورود پایان یافته یا دسترسی رد شده است. برای ادامه دوباره وارد شوید.');
    render();
    if (activeDialog === updates && updates.open) byId('sign-in').focus();
    else if (activeDialog === health && health.open) byId('health-sign-in').focus();
  }
  function failure(kind) { return {maintenanceFailure: kind}; }
  function cookieToken() {
    const match = document.cookie.split(';').map(value => value.trim()).find(value => value.startsWith('irica_csrf='));
    if (!match) return '';
    try { return decodeURIComponent(match.slice('irica_csrf='.length)); }
    catch (_) { return ''; }
  }
  async function api(path, method, payload, cancelWhenHidden, keepalive = false) {
    const controller = new AbortController();
    let timedOut = false;
    if (cancelWhenHidden) reads.add(controller);
    const timeout = setTimeout(() => { timedOut = true; controller.abort(); }, 15000);
    const headers = {Accept: 'application/json'};
    if (method === 'POST') {
      headers['Content-Type'] = 'application/json';
      const token = cookieToken();
      if (token) headers['X-CSRF-Token'] = token;
    }
    try {
      const response = await fetch(path, {method, credentials: 'same-origin', mode: 'same-origin', headers, signal: controller.signal, ...(keepalive ? {keepalive: true} : {}), ...(method === 'POST' ? {body: JSON.stringify(payload)} : {})});
      if (response.status === 401 || response.status === 403) throw failure('auth');
      if (!response.ok) throw failure('service');
      const body = await response.json();
      if (!body || typeof body !== 'object' || Array.isArray(body) || body.ok !== true) throw failure('service');
      return body;
    } catch (error) {
      if (error && error.maintenanceFailure) throw error;
      if (!timedOut && controller.signal.aborted && cancelWhenHidden) throw failure('paused');
      throw failure('unavailable');
    } finally {
      clearTimeout(timeout);
      reads.delete(controller);
    }
  }
  function handleError(error, suffix) {
    if (error && error.maintenanceFailure === 'auth') { expireAuth(); return; }
    if (error && error.maintenanceFailure === 'paused') return;
    if (suffix === 'notice') { readError = true; stopPolling(); }
    notice(suffix, 'سرویس در دسترس نیست. ارتباط را بررسی کنید و دوباره تلاش کنید.', 'danger');
    render();
  }
  function applySnapshot(body) {
    if (!body.app || typeof body.app !== 'object' || Array.isArray(body.app) || !body.driver || typeof body.driver !== 'object' || Array.isArray(body.driver)) throw failure('service');
    snapshot = {app: body.app, driver: body.driver, checking: body.checking === true};
    for (const kind of ['app', 'driver']) if (!sameOffer(approved[kind], offerFor(kind))) approved[kind] = null;
    if (pending && !sameOffer(pending, candidate(pending.kind))) {
      hideConfirmation(false);
      notice('notice', 'نسخهٔ پیشنهادی تغییر کرده است. پیش از نصب، پیشنهاد جدید را بررسی کنید.');
      if (updates.open) byId('updates-close').focus();
    }
    render();
  }
  function schedulePoll() {
    stopPolling();
    if (authExpired || readError || pageStopped || document.hidden || mutationBusy || readBusy || !working()) return;
    if (pollCount >= 120 || Date.now() >= pollDeadline) {
      notice('notice', 'تازه‌سازی خودکار متوقف شد. برای دیدن آخرین وضعیت، «تازه‌سازی وضعیت» را انتخاب کنید.');
      return;
    }
    const delay = Math.min(15000, 3000 + pollCount * 250);
    pollTimer = setTimeout(() => { pollTimer = null; pollCount += 1; void readSnapshot(false, false); }, delay);
  }
  function needsAutomaticCheck() {
    return snapshot && snapshot.app.checked !== true && snapshot.driver.checked !== true && !working() && !automaticCheckSent;
  }
  function resumeVisibleRead() {
    if (!visibleRefreshPending || document.hidden || pageStopped || authExpired || readError || readBusy || mutationBusy) return false;
    visibleRefreshPending = false;
    void readSnapshot(false, false);
    return true;
  }
  async function readSnapshot(allowAutomatic, manual) {
    if (authExpired || pageStopped || document.hidden || readBusy || mutationBusy || (readError && !manual)) return;
    if (manual) { readError = false; pollCount = 0; pollDeadline = Date.now() + 15 * 60 * 1000; }
    readBusy = true;
    const observedRevision = revision;
    let success = false;
    render();
    try {
      const body = await api('/api/maintenance', 'GET', null, true);
      if (observedRevision !== revision || authExpired) return;
      applySnapshot(body);
      uncertain.app = false;
      uncertain.driver = false;
      readError = false;
      if (manual) notice('notice', 'وضعیت تازه‌سازی شد. هیچ نصبی شروع نشد.');
      success = true;
    } catch (error) {
      if (observedRevision === revision) handleError(error, 'notice');
    } finally {
      readBusy = false;
      render();
      if (resumeVisibleRead()) return;
      if (success && allowAutomatic && needsAutomaticCheck()) void checkUpdates(false);
      else schedulePoll();
    }
  }
  async function checkUpdates(force) {
    if (authExpired || pageStopped || document.hidden || mutationBusy || readBusy || working()) return;
    automaticCheckSent = true;
    mutationBusy = true;
    readError = false;
    revision += 1;
    stopPolling();
    render();
    notice('notice', 'فقط اطلاعات نسخه بررسی می‌شود. نصب خودکار انجام نمی‌شود.');
    try {
      const body = await api('/api/maintenance/check', 'POST', {force}, true);
      if (!authExpired) applySnapshot(body);
    } catch (error) { handleError(error, 'notice'); }
    finally { mutationBusy = false; render(); if (!resumeVisibleRead()) schedulePoll(); }
  }
  function reviewOrRetry(kind) {
    const offer = candidate(kind);
    if (!offer || authExpired || mutationBusy || readBusy || readError || uncertain[kind] || pending) return;
    if (stateFor(kind) === 'waiting_for_idle' && sameOffer(approved[kind], offer)) {
      void install(kind, offer);
      return;
    }
    pending = {...offer, kind};
    text('confirm-product', kind === 'app' ? 'IRICA' : 'ChromeDriver');
    text('confirm-version', offer.version);
    text('confirm-detail', kind === 'app' ? 'این نسخه دریافت و اعتبارسنجی می‌شود. پس از پایان کارهای جاری، نصب و بازگشایی برنامه انجام می‌شود. با انصراف، نصب شروع نمی‌شود.' : 'فقط همین نسخهٔ پیشنهادی درایور نصب می‌شود. ابتدا باید کارهای جاری مرورگر تمام شوند. با انصراف، نصب شروع نمی‌شود.');
    byId('products').hidden = true;
    byId('update-tools').hidden = true;
    byId('confirmation').hidden = false;
    render();
    byId('confirm-cancel').focus();
  }
  async function install(kind, offer) {
    if (authExpired || mutationBusy || readError || uncertain[kind] || !sameOffer(offer, candidate(kind)) || !sameOffer(approved[kind], offer)) return;
    mutationBusy = true;
    installRequest = kind;
    revision += 1;
    stopPolling();
    abortReads();
    hideConfirmation(false);
    render();
    if (updates.open) byId('updates-close').focus();
    notice('notice', 'نصب تأییدشده در حال شروع است. بستن این پنجره درخواست را لغو نمی‌کند.');
    try {
      const payload = kind === 'app' ? {offer_id: offer.offer_id} : {offer_id: offer.offer_id, consent: true};
      const body = await api(kind === 'app' ? '/api/update-app' : '/api/update-chromedriver', 'POST', payload, false);
      if (authExpired) return;
      if (!body[kind] || typeof body[kind] !== 'object' || Array.isArray(body[kind])) throw failure('service');
      applySnapshot({ok: true, app: kind === 'app' ? body.app : snapshot.app, driver: kind === 'driver' ? body.driver : snapshot.driver, checking: false});
      notice('notice', stateFor(kind) === 'waiting_for_idle' ? 'نصب منتظر پایان کارهاست. تلاش دوباره فقط با انتخاب شما و برای همان نسخهٔ تأییدشده انجام می‌شود.' : 'درخواست نصب پذیرفته شد. برای پیگیری، وضعیت را تازه‌سازی کنید.');
    } catch (error) {
      uncertain[kind] = true;
      handleError(error, 'notice');
      if (!authExpired) notice('notice', 'وضعیت نصب نامشخص است. پیش از تلاش دوباره، وضعیت را تازه‌سازی کنید. کار پذیرفته‌شده ممکن است در پس‌زمینه ادامه داشته باشد.');
    } finally {
      installRequest = null;
      mutationBusy = false;
      render();
      if (!resumeVisibleRead()) schedulePoll();
    }
  }
  function confirmInstallation() {
    if (!pending || authExpired || mutationBusy || readError) return;
    const captured = {...pending};
    if (!sameOffer(captured, candidate(captured.kind))) {
      hideConfirmation(true);
      notice('notice', 'پیشنهاد نصب تغییر کرده است. پیش از نصب، نسخهٔ دقیق را دوباره بررسی کنید.');
      return;
    }
    approved[captured.kind] = {offer_id: captured.offer_id, version: captured.version};
    void install(captured.kind, captured);
  }

  function focusables(dialog) {
    return Array.from(dialog.querySelectorAll('button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'))
      .filter(element => !element.disabled && !element.closest('[hidden]') && element.getAttribute('aria-hidden') !== 'true' && element.tabIndex >= 0 && element.getClientRects().length > 0);
  }
  function closeDialog(dialog, restore) {
    if (dialog === updates) hideConfirmation(false);
    if (dialog === release) {
      releasePresented = false;
      releaseEpoch += 1;
      releaseReadBusy = false;
    }
    if (activeDialog === dialog) activeDialog = null;
    byId(dialogControls.get(dialog).trigger).setAttribute('aria-expanded', 'false');
    if (dialog.open) dialog.close();
    const origin = returnFocus.get(dialog);
    returnFocus.delete(dialog);
    if (restore && origin && origin.isConnected && !origin.disabled) origin.focus();
    wakeRelease();
  }
  function openDialog(dialog, trigger) {
    if (activeDialog === dialog && dialog.open) return;
    if (activeDialog) closeDialog(activeDialog, false);
    returnFocus.set(dialog, trigger);
    activeDialog = dialog;
    byId(dialogControls.get(dialog).trigger).setAttribute('aria-expanded', 'true');
    dialog.showModal();
    byId(dialogControls.get(dialog).close).focus();
  }
  function dismissDialog(dialog) {
    if (dialog === release) dismissRelease();
    else closeDialog(dialog, true);
  }
  for (const dialog of [updates, health, release]) {
    dialog.addEventListener('cancel', event => { event.preventDefault(); dismissDialog(dialog); });
    dialog.addEventListener('close', () => { if (activeDialog === dialog && !dialog.open) closeDialog(dialog, true); });
    dialog.addEventListener('click', event => {
      if (event.target !== dialog) return;
      const bounds = dialog.getBoundingClientRect();
      if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) dismissDialog(dialog);
    });
  }
  document.addEventListener('keydown', event => {
    const dialog = activeDialog;
    if (!dialog || !dialog.open) return;
    if (event.key === 'Escape') { event.preventDefault(); dismissDialog(dialog); return; }
    if (event.key !== 'Tab') return;
    const elements = focusables(dialog);
    if (!elements.length) { event.preventDefault(); dialog.focus(); return; }
    const first = elements[0];
    const last = elements[elements.length - 1];
    const outside = !dialog.contains(document.activeElement);
    if (event.shiftKey && (document.activeElement === first || outside)) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && (document.activeElement === last || outside)) { event.preventDefault(); first.focus(); }
  });

  function validRelease(body) {
    if (typeof body.seen !== 'boolean' || typeof body.storage_warning !== 'boolean') return false;
    if (body.notes === null) return body.notice_id === '';
    const notes = body.notes;
    const copy = value => typeof value === 'string' && value.trim().length > 0 && value.length <= 1200;
    return notes && /^[0-9]+\.[0-9]+\.[0-9]+$/.test(notes.version) && /^[0-9a-f]{64}$/.test(body.notice_id) &&
      copy(notes.title) && copy(notes.intro) && copy(notes.footer) && Array.isArray(notes.sections) &&
      notes.sections.length > 0 && notes.sections.length <= 4 && notes.sections.every(section =>
        section && copy(section.label) && version(section.version) !== 'نامشخص' && Array.isArray(section.items) &&
        section.items.length > 0 && section.items.length <= 4 && section.items.every(item => item && copy(item.title) && copy(item.body)));
  }
  function renderRelease() {
    const notes = releaseData && releaseData.notes;
    byId('release-content').replaceChildren();
    byId('release-ack').disabled = !notes;
    text('release-title', notes ? notes.title : 'تازه‌های ایریکا');
    text('release-version', notes ? notes.version : '—');
    text('release-intro', notes ? notes.intro : 'برای این نسخه هنوز خلاصه‌ای آماده نشده است. می‌توانید کارتان را ادامه دهید.');
    text('release-footer', notes ? notes.footer : 'این پنجره هیچ به‌روزرسانی یا نصبی را اجرا نمی‌کند.');
    notice('release-storage', releaseData && releaseData.storage_warning ? 'وضعیت مشاهدهٔ قبلی خوانده نشد. اگر پس از بستن، ذخیره هم انجام نشود، ممکن است این خلاصه در ورود بعدی دوباره نمایش داده شود.' : '');
    if (!notes) return;
    for (const section of notes.sections) {
      const group = document.createElement('section');
      group.className = 'irica-maint-release-section';
      const heading = document.createElement('h3');
      heading.className = 'irica-maint-release-section-label';
      const label = document.createElement('span');
      label.textContent = section.label;
      const releaseVersion = document.createElement('bdi');
      releaseVersion.dir = 'ltr';
      releaseVersion.textContent = section.version;
      heading.append(label, releaseVersion);
      const list = document.createElement('ul');
      list.className = 'irica-maint-release-list';
      for (const item of section.items) {
        const row = document.createElement('li');
        row.className = 'irica-maint-release-item';
        const title = document.createElement('h4');
        title.textContent = item.title;
        const description = document.createElement('p');
        description.textContent = item.body;
        row.append(title, description);
        list.append(row);
      }
      group.append(heading, list);
      byId('release-content').append(group);
    }
    releasePresented = release.open && !document.hidden;
  }
  function releaseBlocked() {
    if (document.hidden || pageStopped || authExpired || activeDialog || pending || mutationBusy || installRequest || working()) return true;
    if (Array.from(document.querySelectorAll('dialog[open], [role="dialog"]')).some(dialog =>
      dialog !== release && !dialog.hidden && dialog.getClientRects().length > 0 && getComputedStyle(dialog).visibility !== 'hidden')) return true;
    const focused = document.activeElement;
    return Boolean(focused && focused.getClientRects().length > 0 && focused.matches('a[href], button, input, textarea, select, [contenteditable="true"], [tabindex]'));
  }
  function wakeRelease() {
    if (!releaseAutoPending || releaseWakeQueued || releaseReadBusy || authExpired || pageStopped) return;
    releaseWakeQueued = true;
    queueMicrotask(() => {
      releaseWakeQueued = false;
      if (!releaseAutoPending || releaseReadBusy || authExpired || pageStopped) return;
      if (releaseBlocked()) { releaseDeferred = true; return; }
      if (!releaseData || releaseDeferred) {
        releaseDeferred = false;
        void loadRelease(false);
        return;
      }
      if (releaseData.seen || !releaseData.notes) { releaseAutoPending = false; return; }
      releaseAutoPending = false;
      releaseToUpdates = false;
      renderRelease();
      const origin = document.activeElement;
      openDialog(release, origin && origin !== document.body ? origin : byId('open-updates'));
      releasePresented = true;
      byId('release-ack').focus();
    });
  }
  async function loadRelease(manual) {
    if (authExpired || pageStopped || (releaseReadBusy && !manual)) return;
    const epoch = ++releaseEpoch;
    releaseReadBusy = true;
    try {
      const body = await api('/api/release-notes', 'GET', null, false);
      if (epoch !== releaseEpoch || authExpired || pageStopped) return;
      if (!validRelease(body)) throw failure('service');
      releaseData = body;
      if (manual && release.open) {
        renderRelease();
        if (releaseData.notes && !document.hidden) byId('release-ack').focus();
      } else if (body.seen || !body.notes) releaseAutoPending = false;
    } catch (error) {
      if (epoch !== releaseEpoch || pageStopped) return;
      releaseAutoPending = false;
      if (error && error.maintenanceFailure === 'auth') { expireAuth(); return; }
      const message = 'خلاصهٔ تغییرات دریافت نشد. کارتان را ادامه دهید و بعداً از «به‌روزرسانی‌ها» دوباره باز کنید.';
      notice('release-feedback', message);
      if (manual && release.open) {
        releaseData = null;
        renderRelease();
        text('release-intro', message);
      }
    } finally {
      if (epoch === releaseEpoch) { releaseReadBusy = false; wakeRelease(); }
    }
  }
  function openRelease() {
    if (authExpired || pageStopped || mutationBusy || pending) return;
    releaseAutoPending = false;
    releaseToUpdates = updates.open;
    releasePresented = false;
    releaseData = null;
    renderRelease();
    text('release-intro', 'در حال آماده‌سازی خلاصهٔ تغییرات…');
    text('release-footer', '');
    openDialog(release, byId('release-open'));
    void loadRelease(true);
  }
  async function acknowledgeRelease(captured) {
    try {
      await api('/api/release-notes/seen', 'POST', {version: captured.notes.version, notice_id: captured.notice_id}, false, true);
      if (!authExpired && !pageStopped) notice('release-feedback', '');
    } catch (error) {
      if (pageStopped) return;
      if (error && error.maintenanceFailure === 'auth') { expireAuth(); return; }
      notice('release-feedback', 'ثبت مشاهدهٔ خلاصه انجام نشد؛ ممکن است در ورود بعدی دوباره نمایش داده شود. کارتان را می‌توانید ادامه دهید.');
    }
  }
  function dismissRelease() {
    const captured = release.open && releasePresented && !document.hidden && !authExpired && !pageStopped ? releaseData : null;
    const back = releaseToUpdates && !authExpired && !pageStopped;
    releaseAutoPending = false;
    closeDialog(release, !back);
    releaseToUpdates = false;
    if (back) {
      openDialog(updates, byId('open-updates'));
      byId('release-open').focus();
    }
    if (captured && captured.notes && !captured.seen) void acknowledgeRelease(captured);
  }

  async function runHealth() {
    if (healthBusy || authExpired || pageStopped) return;
    healthBusy = true;
    render();
    notice('health-notice', 'فقط ارتباط سرویس‌ها بررسی می‌شود. پیامی در تلگرام ارسال نمی‌شود.');
    try {
      const body = await api('/api/health-check', 'GET', null, false);
      if (authExpired) return;
      if (!Array.isArray(body.steps)) throw failure('service');
      const statusLabels = {ok: 'ارتباط برقرار است', reachable: 'سرویس در دسترس است', unknown: 'آمادگی نامشخص', auth_denied: 'دسترسی رد شد', not_configured: 'تنظیم نشده', timeout: 'پایان مهلت', error: 'بررسی ناموفق'};
      const statusTones = {ok: 'success', reachable: 'success', unknown: 'warning', auth_denied: 'danger', not_configured: 'neutral', timeout: 'danger', error: 'danger'};
      const statusMessages = {ok: 'ارتباط این سرویس تأیید شد؛ قابلیت‌های دیگر بررسی نشده‌اند.', reachable: 'سرویس پاسخ می‌دهد، اما آمادگی ارسال پیام تأیید نشده است.', unknown: 'آمادگی سرویس مشخص نشد.', auth_denied: 'حساب یا تنظیمات این سرویس را بررسی کنید؛ ورود دوباره به پنل ممکن است کافی نباشد.', not_configured: 'این سرویس تنظیم نشده است.', timeout: 'سرویس در مهلت تعیین‌شده پاسخ نداد.', error: 'بررسی ارتباط ناموفق بود. پیش از تلاش دوباره، تنظیمات سرویس را بررسی کنید.'};
      for (const id of ['cloud', 'telegram', 'proxy']) {
        const matches = body.steps.filter(step => step && step.id === id);
        const step = matches.length === 1 ? matches[0] : null;
        const status = step && Object.hasOwn(statusLabels, step.status) ? step.status : null;
        text('health-' + id + '-status', status ? statusLabels[status] : 'نتیجه گزارش نشده');
        byId('health-' + id + '-status').dataset.tone = status ? statusTones[status] : 'neutral';
        text('health-' + id + '-message', status ? statusMessages[status] : 'نتیجهٔ معتبری برای این سرویس گزارش نشده است.');
        text('health-' + id + '-scope', serviceScopes[id]);
        text('health-' + id + '-readiness', serviceReadiness[id]);
      }
      const checked = typeof body.checked_at === 'string' && body.checked_at.length < 65 ? Date.parse(body.checked_at) : NaN;
      const timestamp = Number.isFinite(checked) ? new Date(checked).toISOString() : '';
      text('health-time', displayDate(timestamp));
      byId('health-time').dateTime = timestamp;
      const summary = {ok: 'بررسی ارتباط تمام شد. محدودهٔ هر نتیجه را بخوانید؛ در دسترس بودن سرویس، آمادگی ارسال پیام را تأیید نمی‌کند.', warning: 'بعضی سرویس‌ها نیاز به بررسی دارند. نتیجه و محدودهٔ هر بررسی را بخوانید.', error: 'بررسی ارتباط یک یا چند سرویس ناموفق بود. تنظیمات سرویس‌ها را بررسی کنید.'};
      notice('health-notice', Object.hasOwn(summary, body.overall) ? summary[body.overall] : 'بررسی تمام شد، اما وضعیت کلی نامشخص است. نتیجهٔ هر سرویس را جداگانه بخوانید.', body.overall === 'error' ? 'danger' : body.overall === 'warning' ? 'warning' : 'info');
    } catch (error) { handleError(error, 'health-notice'); }
    finally { healthBusy = false; render(); }
  }

  byId('open-updates').addEventListener('click', () => openDialog(updates, byId('open-updates')));
  byId('open-health').addEventListener('click', () => openDialog(health, byId('open-health')));
  byId('updates-close').addEventListener('click', () => closeDialog(updates, true));
  byId('health-close').addEventListener('click', () => closeDialog(health, true));
  byId('release-open').addEventListener('click', openRelease);
  byId('release-close').addEventListener('click', dismissRelease);
  byId('release-ack').addEventListener('click', dismissRelease);
  document.addEventListener('focusout', wakeRelease);
  const releaseObserver = new MutationObserver(wakeRelease);
  const releaseObservation = {subtree: true, childList: true, attributes: true, attributeFilter: ['open', 'hidden', 'class', 'style', 'aria-hidden']};
  releaseObserver.observe(document.body, releaseObservation);
  byId('confirm-cancel').addEventListener('click', () => hideConfirmation(true));
  byId('confirm-install').addEventListener('click', confirmInstallation);
  byId('app-install').addEventListener('click', () => reviewOrRetry('app'));
  byId('driver-install').addEventListener('click', () => reviewOrRetry('driver'));
  byId('check').addEventListener('click', () => { pollCount = 0; pollDeadline = Date.now() + 15 * 60 * 1000; void checkUpdates(true); });
  byId('refresh').addEventListener('click', () => { void readSnapshot(false, true); });
  byId('health-run').addEventListener('click', () => { void runHealth(); });
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && release.open && releaseData && releaseData.notes) releasePresented = true;
    wakeRelease();
    if (document.hidden) { stopPolling(); abortReads(); }
    else if (!authExpired && !readError && !pageStopped) {
      if (readBusy || mutationBusy) visibleRefreshPending = true;
      else void readSnapshot(false, false);
    }
  });
  window.addEventListener('offline', () => {
    readError = true;
    stopPolling();
    abortReads();
    notice('notice', 'ارتباط قطع است. پس از اتصال، وضعیت را تازه‌سازی کنید؛ نصب خودکار دوباره اجرا نمی‌شود.');
    render();
  });
  window.addEventListener('pagehide', () => {
    pageStopped = true;
    releaseEpoch += 1;
    releaseReadBusy = false;
    releaseObserver.disconnect();
    stopPolling();
    abortReads();
  });
  window.addEventListener('pageshow', () => {
    if (!pageStopped) return;
    pageStopped = false;
    releaseObserver.observe(document.body, releaseObservation);
    if (release.open && !authExpired) void loadRelease(true);
    else wakeRelease();
    if (!document.hidden && !authExpired && !readError) { visibleRefreshPending = true; resumeVisibleRead(); }
  });
  render();
  if (!document.hidden) void readSnapshot(true, false);
})();
</script>"""


def with_maintenance_ui(page: str) -> str:
    """Inject once into a normal document, failing closed on ambiguous shells."""
    if not isinstance(page, str) or page.count(_MARKER) != 1:
        raise RuntimeError("Maintenance UI requires exactly one actions marker")
    boundaries = {}
    for key, pattern in (
        ("head_open", r"<head(?:\s[^<>]*?)?>"),
        ("head_close", r"</head\s*>"),
        ("body_open", r"<body(?:\s[^<>]*?)?>"),
        ("body_close", r"</body\s*>"),
    ):
        matches = list(re.finditer(pattern, page, re.IGNORECASE))
        if len(matches) != 1:
            raise RuntimeError("Maintenance UI requires exactly one " + key + " boundary")
        boundaries[key] = matches[0]
    head_open, head_close = boundaries["head_open"], boundaries["head_close"]
    body_open, body_close = boundaries["body_open"], boundaries["body_close"]
    if not (head_open.end() <= head_close.start() < head_close.end() <= body_open.start() < body_open.end() <= page.index(_MARKER) < body_close.start()):
        raise RuntimeError("Maintenance UI requires ordered head/body boundaries and a body marker")
    if re.search(r'\bid\s*=\s*([\'"])irica-maint-(?:style|controller|updates|health|release)\1', page, re.IGNORECASE):
        raise RuntimeError("Maintenance UI is already present in this document")
    page = page[:body_close.start()] + _CONTROLLER + page[body_close.start():]
    page = page[:body_open.end()] + dialogs_html() + page[body_open.end():]
    page = page[:head_close.start()] + _STYLE + page[head_close.start():]
    return page.replace(_MARKER, actions_html(), 1)
