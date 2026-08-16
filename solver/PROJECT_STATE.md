# IRICA Captcha Solver — وضعیت پروژه

> OCR برای کپچای سایت گمرک (irica). جدا از پروژه‌ی hamta (که عددِ فارسیِ حروفی
> بود). اینجا رشته‌ی **الفبا-عددیِ انگلیسیِ کوچک، طول ثابت ۵** است.

## ۱. مشخصات کپچا (تأییدشده)
- الفبا: `a-z` + `0-9` (۳۶ کاراکتر، همه **حروف کوچک**).
- طول: **دقیقاً ۵ کاراکتر**.
- تصویر: RGB، ~۳۸۶×۱۵۱، پس‌زمینه‌ی خاکستریِ روشن (~۲۱۵,۲۱۸,۲۲۰)، جوهرِ تیره‌ی
  نزدیک‌مشکی، یک **خط قرمزِ** کم‌رنگ (~۲۳۲,۳۷,۳۸) روی متن، اعوجاجِ موجی ملایم.
- جوهر ~۲۰٪ پیکسل‌ها؛ ارتفاع گلیف ~۱۴۴/۱۵۱ پیکسل (تقریباً تمام‌قد).

## ۲. معماری و رویکرد
- **CRNN (CNN + BiLSTM + CTC)** — همان هندسه‌ی موفقِ persian_ocr، فقط تعداد
  کلاس‌ها عوض شده (۳۸ = ۳۶ + blank + unk). ورودی ۱×۴۸×۱۹۲ → T=۴۸ گام زمانی.
- **بدون لایه‌ی decode**: خروجی مدل مستقیماً همان جواب است (برخلاف hamta که باید
  حروف→عدد برمی‌گشت).
- **پیش‌پردازش**: grayscale نرمال‌شده (متن روشن روی زمینه‌ی تیره)، خط قرمز حذف
  **نمی‌شود** — مدل یاد می‌گیرد از رویش بخواند (درسِ hamta). تک‌خطی، فقط trim افقی.
- **decode با طول ثابت** (`utils.forced_decode`): چون طول = ۵ قطعی است، خروجی به
  ۵ کاراکترِ پرمطمئن‌ترین snap می‌شود؛ خطای «یکی کم/زیاد» را جبران می‌کند.
- **استراتژی داده** (مثل hamta): pretrain روی مصنوعیِ زیاد → fine-tune روی واقعیِ کم.

## ۳. ساختار فایل‌ها
```
solver/
├── config.py            # ALPHABET, CAPTCHA_LEN=5, هندسه، هایپرها
├── vocab.py             # ۳۶ کاراکتر + blank/unk، encode/decode
├── preprocess.py        # to_model_gray + trim_whole + prepare_line_for_model
├── dataset.py           # IricaCaptchaDataset + augment سبک + ctc_collate
├── model.py             # CRNN
├── utils.py             # CER/acc + greedy_decode + forced_decode(len=5)
├── train.py             # حلقه‌ی CTC، --select acc، --init-weights برای fine-tune
├── predict.py           # image -> رشته‌ی ۵ حرفی (IricaSolver)
├── tools/
│   ├── make_synthetic.py  # ژنراتور کپچای سبک-irica (خط قرمز + موج + فونت bold)
│   └── eval_real.py       # ارزیابی روی واقعی‌های لیبل‌دار
├── labeler/label_server.py  # لیبلرِ انگلیسی، پورت ۸۰۰۱، خروجی labels_irica.csv
├── data/{images,labels.csv}
└── checkpoints/
```

## ۴. خط لوله‌ی اجرا
```bash
# 1) داده‌ی مصنوعی (بیرونِ پروژه‌ی hamta؛ همین‌جا)
python tools/make_synthetic.py --n 20000

# 2) pretrain روی مصنوعی
python train.py --epochs 60 --select acc --out checkpoints/best_synth.pt

# 3) لیبل‌زدنِ واقعی‌ها (مرورگر: http://localhost:8001)
python labeler/label_server.py
#    -> ../captchas_irica/labels_irica.csv

# 4) fine-tune روی واقعی
python train.py --csv ../captchas_irica/labels_irica.csv \
                --images ../captchas_irica \
                --init-weights checkpoints/best_synth.pt \
                --lr 2e-4 --epochs 80 --select acc \
                --out checkpoints/best_real.pt

# 5) ارزیابی
python tools/eval_real.py --ckpt checkpoints/best_real.pt

# استنتاج تکی
python predict.py --image ../captchas_irica/captcha_100.png --ckpt checkpoints/best_real.pt
```

## ۵. وضعیت فعلی (۲۰۲۶-۰۷-۱۹)
- ✅ کل اسکلت پروژه ساخته و به‌هم‌وصل شد (vocab/model/dataset/train/predict/eval).
- ✅ ژنراتور مصنوعی کار می‌کند؛ آمارش با واقعی هم‌تراز شد (dark% ~۰٫۲، خط قرمز، موج).
  - سایز فونت ۱۵۰–۱۸۰، فونت‌های bold برای تطبیق ضخامتِ جوهر.
- ✅ لیبرِ انگلیسی آماده و ۷۲۰ کپچای واقعی لیبل خورد → `../laptop/captchas_irica/labels_irica.csv`.
- ✅ **pretrain مصنوعی کامل شد** (۴۰ epoch): `checkpoints/best_synth.pt` = val acc ۰٫۸۶۸، CER ۰٫۰۲۸.
- ✅ **fine-tune روی واقعی کامل شد**: `checkpoints/best_real.pt` = روی کل ۷۲۰ واقعی
  **exact-match ۰٫۹۹۴۴** (۷۱۶/۷۲۰)، char acc ۰٫۹۹۸۳، CER ۰٫۰۰۱۷.
- ✅ رفعِ باگ CTC برای حروفِ تکراریِ چسبیده در `forced_decode` (geee3/55mdw و…).
- ✅ `predict.py` با `best_real.pt` end-to-end تست شد (captcha_100 → 2mbwk درست).
- ⬜ **قدم بعدی: اتصال مدل به `captcha_collector.py`/سایت برای حلِ زنده** (هنوز مانده).

> نکته: مسیر واقعی داده و چک‌پوینت‌ها الان `../laptop/captchas_irica` است (نه `../captchas_irica` که در بخش ۴ مثال زده شده).

## ۶. محدودیت‌ها و نکات
- **CPU-only** (torch 2.4.1+cpu، بدون CUDA). آموزش کند است ولی مسئله کوچک است.
  اگر GPU در دسترس شد، `--device cuda`.
- Windows: DataLoader با num_workers>0 کند است؛ train.py خودش روی CPU/ویندوز
  آن را ۰ می‌کند.
- شکافِ باقی‌مانده‌ی مصنوعی↔واقعی (ارتفاع گلیف ۱۲۸ در برابر ۱۴۴) با fine-tune بسته
  می‌شود — درست مثل hamta. اگر fine-tune کم آورد، اول ارتفاع گلیفِ مصنوعی را بالاتر ببر.
- ترتیب `ALPHABET` در config.py **قفل** است؛ بعد از آموزش عوضش نکن (ایندکسِ کلاس‌ها).
