# کتابخانهٔ آیکون IRICA

آیکون‌های رسمی محصول؛ همهٔ قطعه‌ها اینلاین و آمادهٔ کپی‌اند و برای هر صفحه‌ای در `panel.py` یا `crm.py` منبع واحد به شمار می‌روند. فایل خارجی، فونت‌آیکون و ایموجی در رابط کاربری ممنوع است.

سبک، خطی مینیمال (خانوادهٔ Feather/Lucide) است. چند قطعهٔ پرمصرف (search، user، id-card، phone، send و چند مورد دیگر) عیناً همان مسیرهای جاری محصول‌اند تا هنگام مهاجرت، رگرسیون بصری رخ ندهد؛ بقیه با همان زبان طراحی و روی شبکهٔ ۲۴ واحدی ترسیم شده‌اند.

برای دیدن همهٔ آیکون‌ها در اندازهٔ واقعی، `preview.html` کنار همین فایل را در مرورگر باز کنید.

## قواعد استفاده

۱. قالب ثابت هر آیکون: `viewBox="0 0 24 24"`، `fill="none"`، `stroke="currentColor"`، `stroke-width="2"`، `stroke-linecap="round"` و در مسیرهای دارای گوشه `stroke-linejoin="round"`؛ کلاس `ic` و `aria-hidden="true"` همیشه می‌مانند.

۲. اندازه: پیش‌فرض ۱۳ پیکسل؛ ۱۲ برای ردیف‌های متراکم و متادیتا، ۱۴ برای ناوبری، هدر و دکمه‌های اصلی. اندازه را فقط با `width`/`height` عوض کنید؛ ضخامت خط همیشه ۲ می‌ماند.

۳. رنگ: رنگ ثابت ممنوع. آیکون رنگ متن را از `currentColor` می‌گیرد و در هاور و تم تاریک خودش هماهنگ می‌شود.

۴. فاصلهٔ آیکون و متن: ۶ تا ۸ پیکسل (`gap:6px` تا `8px` در flex، یا `margin-inline-end`).

۵. آیکون تزئینِ برچسب است، نه جایگزین آن. دکمهٔ فقط‌آیکونی باید `title` یا `aria-label` داشته باشد؛ خود آیکون `aria-hidden` است.

۶. ایموجی، فونت‌آیکون و تصویر بیرونی در رابط کاربری هرگز استفاده نمی‌شود.

## جهت در RTL

سه دسته:

- قرینه‌شده و آماده: `arrow-back` (رو به راست)، `skip-forward` (رو به چپ)، `log-out` (پیکان رو به چپ). این سه را همان‌طور که در این کتابخانه‌اند استفاده کنید؛ نیاز به flip ندارند.
- جهت‌خنثی: بقیهٔ آیکون‌ها در هر دو چینش درست‌اند.
- هرگز قرینه نشوند: `play`، `pause` و `stop` (کنترل رسانه جهانی‌اند)، `chart` (جهت محور داده)، `clock`، `sun`، `moon`، `sparkle`.

## نگاشت نام‌های قدیمی در panel.py

کد فعلی لازم نیست یک‌جا عوض شود؛ هر صفحه هنگام بازطراحی به نام این کتابخانه مهاجرت می‌کند.

| نام فعلی در کد | نام در این کتابخانه |
|---|---|
| `user` | `user` |
| `id` | `id-card` |
| `dev` | `phone` |
| `search` | `search` |
| `swap` | `swap` |
| `stop` | `stop` |
| `info` | `info` |
| `link` | `link` |
| `receipt` | `receipt` |
| `card` | `credit-card` |
| `eye` / `eyeOff` | `eye` / `eye-off` |
| `send` | `send` |
| `alert` | `alert-triangle` |
| `userplus` | `user-plus` |

## ۱. ناوبری و جریان

**search** (جست‌وجوی پروفایل، مشتری یا پرونده)
```html
<svg class="ic" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.8-3.8"/></svg>
```

**filter** (پالایش و فیلتر لیست‌ها و ستون‌ها)
```html
<svg class="ic" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M22 3H2l8 9.46V19l4 2v-8.54L22 3z"/></svg>
```

**chevron-down** (بازکردن منو، آکاردئون و select)
```html
<svg class="ic" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>
```

**arrow-back** (بازگشت به صفحهٔ قبل؛ در RTL رو به راست است)
```html
<svg class="ic" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12h14"/><path d="m13 6 6 6-6 6"/></svg>
```

**skip-forward** (ردکردن مرحله در جریان کاری؛ در RTL رو به چپ است)
```html
<svg class="ic" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m19 4-10 8 10 8z"/><path d="M5 5v14"/></svg>
```

**x** (بستن گفت‌وگو، چیپ و پاک‌کردن ورودی جست‌وجو)
```html
<svg class="ic" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12"/></svg>
```

**x-circle** (لغو یا رد شدن کنش و نتیجهٔ ناموفق)
```html
<svg class="ic" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="m15 9-6 6M9 9l6 6"/></svg>
```

**plus** (افزودن پروفایل، مشتری یا ردیف جدید)
```html
<svg class="ic" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>
```

**check** (تأیید، انجام‌شده، ذخیره)
```html
<svg class="ic" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg>
```

**refresh** (تلاش دوباره و همگام‌سازی وضعیت)
```html
<svg class="ic" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 12a9 9 0 0 1 15-6.7L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-15 6.7L3 16"/><path d="M3 21v-5h5"/></svg>
```

**info** (یادداشت راهنما و توضیح غیرهشداری)
```html
<svg class="ic" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 16v-4M12 8h.01"/></svg>
```

**external-link** (بازکردن پیوند بیرونی؛ سایت گمرک، درگاه و مستندات)
```html
<svg class="ic" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/></svg>
```

**log-out** (خروج از حساب؛ در RTL پیکان رو به چپ است)
```html
<svg class="ic" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M15 21h4a2 2 0 0 0 2-2V5a2 2 0 0 0-2-2h-4"/><path d="m8 17-5-5 5-5"/><path d="M3 12h13"/></svg>
```

## ۲. کاربر و مدارک

**user** (مسافر، کاربر و مشخصات فرد)
```html
<svg class="ic" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 3.6-6.5 8-6.5s8 2.5 8 6.5"/></svg>
```

**users** (مشتریان، تیم و گیرندگان)
```html
<svg class="ic" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="9" cy="8" r="3.5"/><path d="M2.5 20.5c0-3.4 2.9-5.6 6.5-5.6s6.5 2.2 6.5 5.6"/><path d="M16.2 4.9a3.5 3.5 0 0 1 0 6.2"/><path d="M17.9 15.6c2.2.7 3.6 2.4 3.6 4.9"/></svg>
```

**user-plus** (افزودن مشتری یا کاربر جدید)
```html
<svg class="ic" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="9" cy="8" r="4"/><path d="M2 21c0-4 3.6-6.5 8-6.5"/><path d="M19 8v6M16 11h6"/></svg>
```

**id-card** (کارت ملی و هویت مسافر)
```html
<svg class="ic" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><rect x="2" y="5" width="20" height="14" rx="2.5"/><circle cx="8.5" cy="11" r="2"/><path d="M5.5 16c.6-1.6 1.7-2.3 3-2.3s2.4.7 3 2.3M14.5 9.5H19M14.5 13H19"/></svg>
```

**passport** (پاسپورت و کتابچهٔ مسافر)
```html
<svg class="ic" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><rect x="4.5" y="3" width="15" height="18" rx="2.5"/><circle cx="12" cy="10.2" r="3"/><path d="M9 16h6"/></svg>
```

**phone** (دستگاه موبایل؛ IMEI و وضعیت ربات)
```html
<svg class="ic" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><rect x="7" y="2.5" width="10" height="19" rx="2.5"/><path d="M10.5 18.5h3"/></svg>
```

## ۳. پرونده، پرداخت و پیام

**receipt** (رسید پرداخت و فاکتور)
```html
<svg class="ic" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 3h14v18l-2.3-1.6-2.3 1.6-2.4-1.6-2.3 1.6L5 21V3z"/><path d="M9 8h6M9 12h6M9 16h4"/></svg>
```

**credit-card** (کارت بانکی و اطلاعات پرداخت)
```html
<svg class="ic" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><rect x="2" y="5" width="20" height="14" rx="2.5"/><path d="M2 10h20"/></svg>
```

**wallet** (کیف پول، مانده و تراکنش‌ها)
```html
<svg class="ic" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12V7H5a2 2 0 0 1 0-4h14v4"/><path d="M3 5v14a2 2 0 0 0 2 2h16v-5"/><path d="M18 12a2 2 0 0 0 0 4h4v-4Z"/></svg>
```

**link** (کپی و اشتراک‌گذاری پیوند پرونده)
```html
<svg class="ic" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M10 14a5 5 0 0 0 7.1.4l2.1-2.1a5 5 0 0 0-7.1-7.1l-1.2 1.2"/><path d="M14 10a5 5 0 0 0-7.1-.4l-2.1 2.1a5 5 0 0 0 7.1 7.1l1.2-1.2"/></svg>
```

**upload** (بارگذاری مدرک و تصویر)
```html
<svg class="ic" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15v3a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-3"/><path d="m7 9 5-5 5 5"/><path d="M12 4v12"/></svg>
```

**download** (دانلود خروجی و مدرک)
```html
<svg class="ic" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15v3a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-3"/><path d="m7 10 5 5 5-5"/><path d="M12 15V3"/></svg>
```

**send** (ارسال فرم، پیام و کد)
```html
<svg class="ic" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M22 2 11 13"/><path d="M22 2 15 22l-4-9-9-4z"/></svg>
```

**telegram** (ارسال و اعلان تلگرام)
```html
<svg class="ic" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M16.4 7.6 11.6 12.4"/><path d="m16.4 7.6-3.1 8.9-1.7-4.1-4-1.5z"/></svg>
```

**calendar** (تاریخ، مهلت و سررسید)
```html
<svg class="ic" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><rect x="3" y="4" width="18" height="18" rx="2"/><path d="M8 2v4M16 2v4M3 10h18"/></svg>
```

**clock** (زمان‌بندی، مدت‌کاری و سن پرونده)
```html
<svg class="ic" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.5 2"/></svg>
```

## ۴. وضعیت و کنترل اجرا

**alert-triangle** (هشدار و وضعیت نیازمند توجه)
```html
<svg class="ic" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3 2 20h20L12 3z"/><path d="M12 10v4"/><path d="M12 17.2h.01"/></svg>
```

**stop** (توقف ربات یا پروفایل)
```html
<svg class="ic" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="1.5"/></svg>
```

**play** (اجرا و شروع ربات؛ هرگز قرینه نمی‌شود)
```html
<svg class="ic" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7 5v14l12-7L7 5z"/></svg>
```

**pause** (توقف موقت)
```html
<svg class="ic" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><rect x="6" y="4" width="4" height="16" rx="1.2"/><rect x="14" y="4" width="4" height="16" rx="1.2"/></svg>
```

**power** (روشن و خاموش‌کردن سرویس یا پروفایل)
```html
<svg class="ic" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M18.36 6.64a9 9 0 1 1-12.73 0"/><path d="M12 2v10"/></svg>
```

**eye** (نمایش رمز و جزئیات)
```html
<svg class="ic" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>
```

**eye-off** (پنهان‌کردن رمز و جزئیات)
```html
<svg class="ic" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2 12s3.6-7 10-7c1.8 0 3.4.5 4.7 1.3M22 12s-3.6 7-10 7c-1.8 0-3.4-.5-4.7-1.3"/><path d="m3 3 18 18"/></svg>
```

**shield** (سپر؛ امنیت، اعتبارسنجی و وضعیت سالم پرونده)
```html
<svg class="ic" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
```

## ۵. ویرایش و داده

**pencil** (ویرایش ردیف و مشخصات)
```html
<svg class="ic" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z"/></svg>
```

**trash** (حذف؛ همیشه همراه تأیید)
```html
<svg class="ic" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="M10 11v6M14 11v6"/></svg>
```

**copy** (کپی شناسه، کد و متن)
```html
<svg class="ic" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><rect x="9" y="9" width="12" height="12" rx="2.5"/><path d="M5 15H4.5A2.5 2.5 0 0 1 2 12.5v-8A2.5 2.5 0 0 1 4.5 2h8A2.5 2.5 0 0 1 15 4.5V5"/></svg>
```

**swap** (جابه‌جایی و تعویض؛ IMEI یا پروفایل)
```html
<svg class="ic" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M7 16V4m0 0L3.5 7.5M7 4l3.5 3.5M17 8v12m0 0 3.5-3.5M17 20l-3.5-3.5"/></svg>
```

**list** (لاگ و رخدادها)
```html
<svg class="ic" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M8 6h13M8 12h13M8 18h13"/><path d="M3.5 6h.01M3.5 12h.01M3.5 18h.01"/></svg>
```

**chart** (آمار و نمودار عملکرد؛ جهت محور داده ثابت می‌ماند)
```html
<svg class="ic" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M3 3v18h18"/><path d="M18 17V9"/><path d="M13 17V5"/><path d="M8 17v-3"/></svg>
```

## ۶. سامانه‌ها و گمرک

**gear** (تنظیمات)
```html
<svg class="ic" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
```

**bot** (ربات و کارهای خودکار)
```html
<svg class="ic" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><rect x="4" y="8" width="16" height="12" rx="3"/><path d="M12 8V5.2"/><path d="M12 3.4h.01"/><path d="M2 14h2M20 14h2"/><path d="M9 13v2M15 13v2"/></svg>
```

**customs** (ساختمان گمرک و سامانه‌های مرجع)
```html
<svg class="ic" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 8h16l-8-5-8 5z"/><path d="M6 16V8m4 8V8m4 8V8m4 8V8"/><path d="M4 19h16"/></svg>
```

**globe** (وب و سامانه‌های آنلاین)
```html
<svg class="ic" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M3 12h18"/><path d="M12 3c2.7 2.6 4 5.7 4 9s-1.3 6.4-4 9c-2.7-2.6-4-5.7-4-9s1.3-6.4 4-9z"/></svg>
```

## ۷. پوسته و امضای برند

**sun** (تم روشن)
```html
<svg class="ic" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>
```

**moon** (تم تاریک)
```html
<svg class="ic" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>
```

**sparkle** (امضای برند؛ فقط برای کارهای خودکار و هوشمند)
```html
<svg class="ic" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3.5c1 5 2.5 7.5 8.5 8.5-5 1-7.5 3.5-8.5 8.5-1-5-2.5-7.5-8.5-8.5 5-1 7.5-3.5 8.5-8.5z"/></svg>
```

## افزودن آیکون جدید

پیش از ترسیم آیکون تازه، همین فایل را ورق بزنید؛ احتمالاً موجود است. اگر واقعاً جدید است: روی شبکهٔ ۲۴ واحدی و با ضخامت خط ۲ بکشید، شروع و پایان مسیرها را روی نقاط گرد (مضارب ۰٫۵) بگذارید، در یکی از گروه‌های بالا با همان قالب (نام، یک خط کاربرد، قطعهٔ کامل) ثبت کنید و `preview.html` را دوباره بسازید. آیکونی که در این کتابخانه نیست، در UI محصول جایی ندارد.
