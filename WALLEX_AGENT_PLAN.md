# پروژه «سیاوش» — Agent معامله‌گر هوشمند والکس
## تحلیل کامل چالش + معماری + قراردادهای کد + تسک‌بندی قابل اجرا توسط Coding Agent

> نسخه: v2.0 — تاریخ: ۱۴۰۵/۰۷/۳۰ (2026-10-22)
> این فایل «منبع حقیقت» پروژه است. Coding Agent فقط با خواندن همین فایل باید بتواند پروژه را از صفر تا انتها جلو ببرد. اگر چیزی در این فایل نیامده، Agent **حدس نمی‌زند**؛ آن را در `PROGRESS.md` به‌عنوان BLOCKED ثبت می‌کند و سراغ تسک بعدی می‌رود.

---

## ⚡ دستورالعمل اجرایی برای Coding Agent (اول این بخش را بخوان)

### الف) قبل از هر کاری
1. این بخش‌ها را کامل بخوان: «دستورالعمل اجرایی» (همین بخش)، ۱ (قوانین سخت)، ۳ (معماری)، ۴ و ۵ (منطق و ریسک)، ۷ (لاگ)، ۸ (استک، ساختار ریپو و **۸.۳ قراردادهای کد**)، ۹.۴ (ترتیب اجرا).
2. اگر فایل `PROGRESS.md` در ریشه ریپو وجود ندارد، آن را از روی ضمیمه G بساز (اولین کار تسک T-010 است).
3. اگر فایل `AGENTS.md` وجود ندارد، تسک T-005 هنوز انجام نشده است.

### ب) حالت‌های کار
- **حالت اتوپایلوت (پیش‌فرض):** وقتی پرامپت فقط می‌گوید «طبق WALLEX_AGENT_PLAN.md پروژه را جلو ببر».
- **حالت تک‌تسک:** وقتی پرامپت یک شناسه مشخص می‌دهد (مثلاً «فقط T-102»). فقط همان تسک را انجام بده و تمام.
- **حالت دو نفره:** وقتی پرامپت می‌گوید `OWNER=A` یا `OWNER=B`. فقط تسک‌های همان مالک را بردار؛ تسک‌های مالک دیگر را لمس نکن (اگر به آن‌ها وابسته‌ای و هنوز DONE نیستند، از Fakeهای T-008 استفاده کن یا تسک بعدی را بردار).

### ج) حلقه اتوپایلوت
```
تا وقتی تسک قابل انجام هست:
  1. PROGRESS.md را باز کن.
  2. در جدول ۹.۴ (به ترتیب ردیف)، اولین تسکی را پیدا کن که:
       - وضعیتش TODO است،
       - نوعش AGENT یا AGENT+HUMAN است،
       - همه وابستگی‌هایش DONE هستند (یا وابستگی فقط از نوع «نرم» است و Fake آن موجود است)،
       - در حالت دو نفره، مالکش با OWNER یکی است.
  3. وضعیتش را IN_PROGRESS کن.
  4. شرح تسک در بخش ۱۰ را دقیق اجرا کن. فقط فایل‌های «خروجی» همان تسک (و تست‌هایش) را بساز/تغییر بده.
  5. همه بندهای «پذیرش» را اجرا کن + `make lint test`.
  6. اگر همه سبز بود: وضعیت DONE، commit با پیام «T-xxx: <عنوان>»، یادداشت کوتاه در ستون notes.
     اگر سبز نشد و بعد از ۳ تلاش جدی درست نشد: وضعیت BLOCKED + توضیح دقیق خطا، و برو سراغ تسک بعدی.
  7. اگر به تسک HUMAN رسیدی: وضعیت WAITING_HUMAN و در notes دقیقاً بنویس انسان باید چه کار کند/چه فایلی بدهد. سراغ تسک‌های بعدی که به آن وابسته نیستند برو.
  8. اگر context رو به اتمام است: تسک جاری را تمام یا به حالت سالم برگردان، PROGRESS.md را به‌روز کن، و متوقف شو.
در پایان: خلاصه‌ای از DONE / BLOCKED / WAITING_HUMAN در PROGRESS.md بنویس.
```

### د) ممنوعیت‌های مطلق (هیچ تسکی این‌ها را مجاز نمی‌کند)
1. هرگز `MODE=live` را فعال نکن، هرگز سفارش واقعی ثبت نکن، هرگز `scripts/smoke_test.py` را اجرا نکن. اجرای لایو فقط کار انسان است.
2. هرگز endpoint یا فیلد API والکس را حدس نزن. فقط از `docs/wallex_api_notes.md` و `src/siavash/exchange/endpoints.py` استفاده کن. اگر وجود ندارد یا UNVERIFIED است، روی mock کار کن و در PROGRESS بنویس.
3. هیچ مسیر کدی برای futures/margin/withdraw/transfer/deposit ننویس.
4. هر سفارش باید از `SymbolGuard` داخل لایه کلاینت/Broker عبور کند و `decision_id` داشته باشد.
5. LLM هرگز سفارش ثبت نمی‌کند و هرگز محدودیت ریسک را بالا نمی‌برد.
6. هیچ عدد پارامتری را hardcode نکن؛ همه از `config/*.yaml`.
7. مقادیر سقف در `config/risk.yaml` را بالاتر از مقادیر بخش ۵ نبر (فقط انسان می‌تواند).
8. فایل‌های `src/siavash/core/` (قراردادها) را بدون ضرورت عوض نکن. اگر لازم شد: همه استفاده‌کننده‌ها را هم اصلاح کن، تست‌ها را سبز کن و در `CHANGELOG.md` بنویس «CONTRACT CHANGE: ...».
9. کلید API، توکن یا `.env` را commit یا لاگ نکن.
10. Lookahead ممنوع: هیچ ویژگی/سیگنالی در زمان t از داده بعد از t استفاده نکند.

### هـ) نوع تسک‌ها
- `AGENT` — کاملاً توسط Agent انجام می‌شود.
- `AGENT+HUMAN` — Agent آماده می‌کند؛ انسان یک ورودی می‌دهد یا تأیید/اجرا می‌کند (در شرح مشخص شده کدام بخش با کیست).
- `HUMAN` — Agent انجامش نمی‌دهد؛ فقط آن را در PROGRESS به WAITING_HUMAN می‌برد.

### و) قرارداد تست
- تست‌های واحد: `tests/unit/...` — بدون شبکه، بدون کلید. با `make test` اجرا می‌شوند.
- تست‌های یکپارچه با API واقعی: `tests/integration/...` با `@pytest.mark.live` — به‌طور پیش‌فرض skip؛ فقط انسان با `make test-live` اجرا می‌کند.
- هر بند پذیرش که با `` ` `` شروع می‌شود یک دستور قابل اجراست و باید با exit code صفر تمام شود.

---

## فهرست

0. خلاصه اجرایی
1. تحلیل چالش: قوانین سخت، معیارهای داوری، نتیجه‌گیری‌ها
2. تز برد
3. معماری سیستم
4. منطق استراتژی و تصمیم‌گیری
5. مدیریت سرمایه و ریسک
6. نقش واقعی هوش مصنوعی
7. لاگ تصمیم، تکرارپذیری و نسخه‌گذاری
8. استک فنی، ساختار ریپو و **قراردادهای کد**
9. تقسیم کار، اولویت‌ها، Milestoneها و **ترتیب اجرای خطی**
10. تسک‌ها (شرح کامل + پذیرش قابل اجرا)
11. نقشه پوشش معیارهای داوری
12. ریسک‌های پروژه
13. ضمیمه‌ها: A denylist، B نمونه risk.yaml، C اسکیمای لاگ، D پرامپت‌ها، E متن AGENTS.md، F قالب پرامپت‌های آماده، G قالب PROGRESS.md، H endpointهای شناخته‌شده والکس (تأییدنشده)، I نمونه strategy.yaml و base.yaml


---

## 0. خلاصه اجرایی

- **چه می‌سازیم:** یک موتور معاملاتی خودکار «فقط اسپات» روی والکس که هر چند دقیقه داده بازار را می‌گیرد، سیگنال‌های کمّی تولید می‌کند، یک مدل ML احتمال موفقیت هر سیگنال را (بعد از کسر کارمزد) تخمین می‌زند، یک Agent مبتنی بر LLM رژیم بازار را تحلیل و هر معامله کاندید را تأیید/رد/تعدیل می‌کند، یک لایه ریسک سخت‌گیر (که AI نمی‌تواند دورش بزند) حجم و حد ضرر را تعیین می‌کند و در نهایت سفارش اجرا می‌شود. **برای هر تصمیم، دلیل کامل در لاگ JSONL ذخیره می‌شود.**
- **فلسفه:** «اول زنده ماندن، بعد سود». ۶۰ امتیاز از ۱۵۰ امتیاز عملکرد (کنترل ضرر + فعال بودن + پایداری) به سود ربطی ندارد، و ۱۵۰ امتیاز کیفیت فنی کاملاً به کیفیت مهندسی و مستندات بستگی دارد. **یک سیستم با سود متوسط ولی افت کم، لاگ کامل و بک‌تست/شدو تست مستند، از سیستم پرریسکِ پرسود امتیاز بیشتری می‌گیرد.** فقط به یک شرط: بازدهی نهایی باید **مثبت** باشد (تیم زیان‌ده ۹۰ امتیاز سود را کامل از دست می‌دهد).
- **یک کد برای سه حالت:** Backtest، Shadow (Paper) و Live دقیقاً از همان موتور تصمیم استفاده می‌کنند؛ فقط «Broker» عوض می‌شود. این مهم‌ترین تصمیم معماری برای امتیاز تکرارپذیری است.
- **پایان چالش:** چند ساعت قبل از پایان رسمی، ورود جدید متوقف و همه دارایی‌ها به **تومان** تبدیل می‌شوند (سود باید realized در تومان باشد).

---

## 1. تحلیل چالش

### 1.1 قوانین سخت (Hard Constraints) — نقض هرکدام = حذف یا از دست رفتن امتیاز

| # | قانون | پیاده‌سازی در کد |
|---|---|---|
| R1 | فقط معاملات **اسپات**؛ فیوچرز/مارجین ممنوع | کلاینت اصلاً endpoint فیوچرز ندارد + تست واحد |
| R2 | فقط بازارهای مجاز (همه بازارها **به جز** لیست ممنوعه، ضمیمه A) | `SymbolGuard` در لایه کلاینت؛ هر سفارش روی بازار ممنوع با Exception رد می‌شود |
| R3 | **هیچ معامله دستی** — همه سفارش‌ها توسط الگوریتم | API Key فقط داخل سرور ربات؛ هر سفارش `client_order_id` با پیشوند ربات و `decision_id` دارد؛ هیچ‌کس با این حساب از اپ/سایت معامله نکند |
| R4 | ممنوعیت Self-trading و معاملات هماهنگ درون‌تیمی | اعضای تیم با حساب شخصی روی همان بازارها معامله نکنند؛ ربات هرگز سفارش خرید و فروش هم‌قیمت هم‌زمان روی یک بازار نمی‌گذارد |
| R5 | ممنوعیت Wash trading / حجم مصنوعی | حداقل فاصله زمانی بین ورود و خروج، حداقل هدف سود ≥ ۲× کارمزد رفت‌وبرگشت، سقف تعداد معامله در ساعت |
| R6 | سرمایه مبنا ۵ میلیون تومان؛ بازدهی = (ارزش نهایی − ۵M) / ۵M | گزارش‌ساز همین فرمول را محاسبه می‌کند |
| R7 | سود باید **realized در تومان** باشد، نه دارایی کوینی در پایان | ماژول `EndgameLiquidator`: توقف ورود از T−6h، تبدیل کامل به TMN تا T−1h |
| R8 | سورس در GitHub؛ **همان نسخه‌ای** که معامله کرده | git tag برای هر نسخه دیپلوی‌شده + ثبت `git_sha` در هر رکورد لاگ |
| R9 | داکیومنت md با بخش‌های مشخص (معماری، AI و ابزار والکس، شرایط خرید/فروش، ریسک، اجرای مجدد) | `docs/STRATEGY.md` (تسک T-1302) |
| R10 | لاگ تصمیم برای **هر معامله**: چه سیگنالی، چرا خرید/فروش — جزو تحویلی‌ها | `logs/decisions.jsonl` (اسکیمای ضمیمه C) |
| R11 | ویدیو دمو قبل از ددلاین — بدون آن وارد داوری نمی‌شوید | تسک T-1304 با یادآور ددلاین |

### 1.2 معیارهای داوری (۳۰۰ امتیاز) و برداشت ما

**بخش اول — عملکرد معاملاتی (۱۵۰)**

| معیار | امتیاز | برداشت عملیاتی |
|---|---|---|
| درصد سود نهایی (رتبه‌ای) | ۹۰ | فقط بازدهی مثبت امتیاز دارد. رتبه‌ای است، پس باید «به‌اندازه کافی» سود کرد، اما ریسک ضرر کردن کل این ۹۰ را می‌سوزاند. |
| کنترل ضرر (افت حساب وسط چالش) | ۳۰ | Max Drawdown باید خیلی پایین بماند (هدف: زیر ۳٪، سقف سخت ۶٪). یعنی حجم کم برای هر معامله، حد ضرر سفت. |
| فعال بودن الگوریتم | ۱۵ | معامله «منظم و معقول» در هر سه روز. هدف: حدود ۴ تا ۱۲ معامله در روز؛ نه صفر، نه صدها. |
| پایداری اجرا | ۱۵ | خطای کم API، سفارش درست (دقت قیمت/مقدار)، بدون کرش. نیاز به retry، reconciliation، auto-restart. |

**بخش دوم — کیفیت فنی (۱۵۰)**

| معیار | امتیاز | برداشت عملیاتی |
|---|---|---|
| منطق استراتژی | ۴۰ | تز معاملاتی روشن + قواعد ورود/خروج مکتوب + لاگ که نشان دهد همین قواعد اجرا شده. |
| استفاده واقعی از AI | ۳۵ | AI باید در «تصمیم» نقش داشته باشد: مدل ML که احتمال می‌دهد + Agent LLM که رژیم را تعیین و معاملات را تأیید/رد می‌کند و این نقش در لاگ قابل مشاهده باشد (مثلاً X٪ کاندیداها توسط AI رد شدند و نتیجه آن‌ها چه بود). |
| مدیریت ریسک و سرمایه | ۳۰ | سقف حجم سفارش، حد ضرر، عدم ورود با کل پول — صریحاً در بارم آمده. |
| قابل اجرای مجدد بودن | ۲۵ | Docker، `make` دستورات، راهنما، دیتای snapshot، کش LLM برای replay. |
| بک‌تست و تست سایه | ۲۰ | گزارش بک‌تست walk-forward + گزارش شدو تست + مقایسه این دو با لایو. |

**شاخص‌هایی که داوران روی حساب می‌بینند:** تعداد معاملات، حجم معاملات، میانگین حجم هر معامله، درصد بازدهی، سرعت اجرا. پس گزارش‌ساز ما باید دقیقاً همین‌ها را تولید کند (T-1201).

### 1.3 نتیجه‌گیری‌های کلیدی

1. **کارمزد دشمن اصلی است.** طبق گزارش‌های عمومی، کارمزد اسپات والکس بسته به سطح کاربری حدود ۰٫۱۰ تا ۰٫۳۵ درصد در بازار تومانی و ۰٫۱۳ تا ۰٫۲۰ درصد در بازار تتری است. با فرض بدترین حالت (۰٫۳۵٪)، رفت‌وبرگشت ≈ ۰٫۷٪. پس اسکالپینگ با هدف ۰٫۳٪ قطعاً ضررده است؛ هدف سود هر معامله باید حداقل حدود ۱٫۵٪ تا ۳٪ باشد. (کارمزد واقعی حساب چالش را در T-101 از API/پنل بخوانید.)
2. **بازار تومانی ترجیح دارد.** چون نتیجه نهایی به تومان سنجیده می‌شود، معامله روی جفت‌های `/TMN` حسابداری را ساده و ریسک تبدیل تتر را حذف می‌کند. جفت‌های `/USDT` فقط به‌عنوان «منبع داده» استفاده می‌شوند.
3. **بازارهای مجاز پرنقدشونده:** طبق لیست شما، بازارهای اصلی مثل BTC، ETH، USDT، SOL، XRP، DOGE، TRX، ADA، BNB، LTC و SHIB در لیست ممنوعه **نیستند** (اما TON، NEAR، INJ، FET، ICP و... ممنوع‌اند) (قبل از استفاده، هر نماد را با SymbolGuard و API بررسی کنید). استراتژی فقط روی ۵ تا ۱۰ بازارِ با بیشترین نقدشوندگی و کمترین اسپرد اجرا شود.
4. **زمان کوتاه است (۳ روز).** استراتژی‌های بلندمدت جواب نمی‌دهند؛ تایم‌فریم اصلی ۱۵ دقیقه با فیلتر ۱ ساعته/۴ ساعته.
5. **حساب از لحظه اول ارزیابی می‌شود.** پس ربات باید از روز اول (با پیکربندی محافظه‌کار) زنده باشد و در طول هکاتون بهبود پیدا کند؛ هر تغییر نسخه باید تگ و مستند شود.

---

## 2. تز برد

سه منبع «لبه» (Edge) که با هم ترکیب می‌شوند:

1. **انحراف قیمت تومانی از قیمت ضمنی (Implied Price Deviation)** — مخصوص بازار ایران و یک ایده تمایزدهنده:
   `implied_TMN(BTC) = BTC/USDT × USDT/TMN` (هر دو از خود والکس).
   وقتی `BTC/TMN` به‌طور معنادار پایین‌تر از قیمت ضمنی باشد (مثلاً z-score < −2)، بازار تومانی معمولاً به سمت آن برمی‌گردد. این سیگنال «تأخیر بازار تومانی نسبت به بازار تتری» را شکار می‌کند. فقط یک لِگ معامله می‌شود (نه آربیتراژ سه‌گانه)، پس کارمزد زیاد نمی‌شود و wash trading هم نیست.
2. **روند/مومنتوم (Trend Breakout)** — در رژیم روندی: شکست سقف کانال ۲۰ کندلی ۱۵ دقیقه با تأیید حجم و همسویی روند ۱ ساعته.
3. **بازگشت به میانگین (Mean Reversion)** — در رژیم رنج: قیمت زیر باند پایین بولینگر + RSI پایین + نبود روند نزولی قوی در ۴ ساعته.

لایه AI تعیین می‌کند **کدام** از این سه در هر لحظه فعال باشد (رژیم) و **کدام** کاندید ارزش ورود دارد (احتمال سود بعد از کارمزد). لایه ریسک تعیین می‌کند **چقدر**.

---

## 3. معماری سیستم

```
                ┌──────────────────────── Wallex ────────────────────────┐
                │      REST API (market/account/orders)    Wallex MCP     │
                └───────▲──────────────────────▲───────────────▲──────────┘
                        │                      │               │ (ابزارهای Agent)
             ┌──────────┴─────────┐   ┌────────┴────────┐      │
             │  WallexClient      │   │  Broker         │      │
             │  (auth, rate-limit,│   │  Live|Paper|BT  │      │
             │   retry, guard)    │   └────────▲────────┘      │
             └──────────┬─────────┘            │               │
                        ▼                      │               │
   ┌────────────────────────────────┐          │               │
   │ DataService                    │          │               │
   │ candles, depth, trades, cache  │          │               │
   └──────────────┬─────────────────┘          │               │
                  ▼                            │               │
   ┌────────────────────────────────┐          │               │
   │ FeatureEngine (indicators,     │          │               │
   │ orderbook, implied deviation)  │          │               │
   └──────────────┬─────────────────┘          │               │
                  ▼                            │               │
   ┌────────────────────────────────┐   ┌──────┴─────────┐     │
   │ SignalGenerators (3 strategy)  │──▶│ DecisionEngine │◀────┤
   └────────────────────────────────┘   │ (Orchestrator) │     │
   ┌────────────────────────────────┐   │                │     │
   │ ML Scorer (LightGBM, P(win))   │──▶│  candidates →  │     │
   └────────────────────────────────┘   │  ML → LLM →    │     │
   ┌────────────────────────────────┐   │  Risk → Exec   │     │
   │ LLM Agents: MarketAnalyst,     │──▶│                │─────┘
   │ TradeReviewer, DailyReflector  │   └──────┬─────────┘
   └────────────────────────────────┘          │
   ┌────────────────────────────────┐          │
   │ RiskManager (hard rules, veto) │◀─────────┤
   └────────────────────────────────┘          ▼
   ┌──────────────────────────────────────────────────────────┐
   │ Persistence: SQLite state + logs/*.jsonl + reports/       │
   └──────────────────────────────────────────────────────────┘
```

### 3.1 حلقه اصلی (هر ۶۰ ثانیه — قابل تنظیم)

1. به‌روزرسانی داده‌ها (کندل‌های بسته‌شده، دفتر سفارش، موجودی).
2. مدیریت پوزیشن‌های باز: بررسی SL/TP/Trailing/Time-stop → در صورت نیاز خروج.
3. هر ۱ ساعت (یا با رویداد غیرعادی مثل نوسان شدید): **MarketAnalyst Agent** رژیم و ضریب ریسک را به‌روز می‌کند.
4. روی کندل ۱۵ دقیقه‌ای بسته‌شده: SignalGeneratorها → کاندیدها.
5. هر کاندید: ML Score → اگر `P(win) ≥ threshold` → **TradeReviewer Agent** → اگر APPROVE → RiskManager (حجم، حد ضرر، چک‌های پرتفوی) → Broker.
6. ثبت همه چیز (حتی کاندیدهای ردشده) در `decisions.jsonl`.
7. Heartbeat + بررسی Circuit Breakerها.

### 3.2 اصل «AI پیشنهاد می‌دهد، ریسک وتو می‌کند»

LLM هرگز مستقیماً سفارش نمی‌دهد و نمی‌تواند حدود ریسک را تغییر دهد. فقط می‌تواند: رژیم را تعیین کند، کاندید را رد کند، یا ضریب اندازه را **کوچک‌تر یا مساوی ۱** کند. این هم ایمنی می‌دهد و هم تکرارپذیری.

---

## 4. منطق استراتژی و تصمیم‌گیری

> همه اعداد زیر **مقادیر اولیه** هستند و باید با بک‌تست walk-forward (T-904) تأیید/تنظیم شوند. هر تغییر در `config/strategy.yaml` و CHANGELOG ثبت شود.

### 4.1 Universe (بازارهای قابل معامله)
- ورودی: همه بازارهای `/TMN` که از API می‌آیند − denylist (ضمیمه A).
- فیلتر نقدشوندگی (هر ۶ ساعت): حجم ۲۴ ساعته تومانی در ۱۰ بازار اول، اسپرد ≤ ۰٫۳٪، عمق سفارش در ۰٫۵٪ اطراف قیمت ≥ ۵ برابر اندازه سفارش ما.
- حداکثر ۸ بازار فعال.

### 4.2 ویژگی‌ها (Features)
- قیمتی/روند: EMA(20/50/200)، شیب EMA، ADX(14)، کانال Donchian(20)، بازده ۱/۴/۱۶ کندل.
- نوسان: ATR(14)، ATR٪، عرض باند بولینگر، realized volatility.
- مومنتوم: RSI(14)، MACD hist، Stochastic.
- حجم: volume z-score، OBV slope.
- دفتر سفارش: اسپرد، imbalance سطح ۱ تا ۱۰، عمق.
- **Implied deviation:** `dev = (P_tmn − P_usdt × USDT_TMN) / implied`، z-score آن روی پنجره ۲۴ ساعته.
- چندتایم‌فریمی: همه موارد بالا روی 15m و 1h و 4h.
- بازار کل: بازده BTC و میانگین بازده universe (breadth).

### 4.3 قواعد سیگنال (کاندید ورود — فقط Long، چون اسپات است)

| استراتژی | رژیم مجاز | شرط ورود (15m) | خروج |
|---|---|---|---|
| S1 Trend Breakout | TREND_UP | Close > Donchian High(20) قبلی، EMA20>EMA50 در 1h، ADX(1h)>20، volume z>1 | SL = entry − 1.5×ATR؛ TP1 = +2×ATR (نیمی)، بقیه با trailing 1.5×ATR؛ time stop 12 ساعت |
| S2 Mean Reversion | RANGE | Close < BB lower(20,2)، RSI<30، ADX(1h)<20، قیمت 4h بالای EMA200 یا نزدیکش | TP = بازگشت به BB mid؛ SL = entry − 1.2×ATR؛ time stop 6 ساعت |
| S3 Implied Deviation | همه (به جز PANIC) | z(dev) < −2 و اسپرد عادی و قیمت USDT-pair روند نزولی تند ندارد | TP = z(dev) ≥ 0 یا +1.5٪؛ SL = −1.2٪؛ time stop 3 ساعت |

**قاعده سراسری:** کاندید فقط وقتی معتبر است که `expected_move ≥ 2.5 × round_trip_fee`.

### 4.4 مراحل امتیازدهی هر کاندید
1. **ML Scorer:** خروجی `p_win` = احتمال اینکه قبل از برخورد به SL، به TP برسد (با برچسب‌گذاری triple-barrier شامل کارمزد). اگر `p_win < θ` (θ از بک‌تست، مثلاً ۰٫۵۵) → REJECT_ML.
2. **Expected Value:** `EV = p_win × TP% − (1−p_win) × SL% − fees`. اگر EV ≤ 0 → REJECT_EV.
3. **TradeReviewer (LLM):** با ورودی خلاصه ساختاریافته (ویژگی‌ها، رژیم، SHAP مهم‌ترین عوامل ML، پوزیشن‌های باز، عملکرد اخیر همان استراتژی) → خروجی JSON: `{"verdict": "APPROVE|REJECT", "size_multiplier": 0.25..1.0, "reasons": [...], "risk_flags": [...]}`.
4. **RiskManager:** محاسبه حجم و اعمال همه محدودیت‌ها (بخش ۵). ممکن است وتو کند.
5. **Execution:** سفارش Limit روی بهترین bid (maker) با timeout ۹۰ ثانیه؛ اگر پر نشد و سیگنال هنوز معتبر بود → Limit تهاجمی با حداکثر لغزش ۰٫۱۵٪؛ وگرنه لغو.

### 4.5 خروج
اولویت‌ها: (۱) Circuit Breaker/Endgame، (۲) Stop-loss، (۳) Take-profit/Trailing، (۴) Time-stop، (۵) سیگنال خروج (مثلاً رژیم به PANIC تغییر کند یا LLM هشدار «خروج» بدهد — فقط برای خروج زودتر، نه نگه داشتن بیشتر).
حد ضرر **سمت کلاینت** پیاده می‌شود (هر ۱۰ ثانیه چک قیمت) مگر اینکه API والکس سفارش stop برای اسپات داشته باشد (در T-101 بررسی شود؛ اگر داشت، stop سمت صرافی هم به‌عنوان پشتیبان گذاشته شود).

---

## 5. مدیریت سرمایه و ریسک

| پارامتر | مقدار اولیه | توضیح |
|---|---|---|
| ریسک هر معامله | ۰٫۵٪ ارزش حساب | فاصله تا SL × حجم ≤ ۰٫۵٪ × equity |
| سقف اندازه هر پوزیشن | ۲۰٪ equity (۱M تومان) | «عدم ورود با کل پول» |
| حداکثر پوزیشن هم‌زمان | ۳ | |
| سقف کل exposure | ۵۰٪ equity | همیشه ≥ ۵۰٪ نقد تومانی |
| حداکثر یک پوزیشن در هر بازار | ۱ | بدون pyramiding |
| حد ضرر روزانه | ۲٪ | رسید → توقف ورود جدید تا روز بعد |
| Kill-switch افت کل | ۵٪ از اوج | فروش همه + توقف کامل + هشدار؛ فقط با دستور دستیِ «resume» (که دستور سیستمی است، نه معامله دستی) |
| Cooldown بعد از ۲ ضرر پیاپی | ۶۰ دقیقه | |
| Cooldown بعد از خروج از یک بازار | ۳۰ دقیقه | ضد wash trading/churn |
| سقف تعداد معامله | ۴ در ساعت، ۲۵ در روز | ضد overtrading |
| ضریب ریسک رژیم | TREND 1.0، RANGE 0.7، HIGH_VOL 0.5، PANIC 0 | از MarketAnalyst |
| حداقل اندازه سفارش | از API (min notional) | زیر آن، سفارش ساخته نمی‌شود |
| Endgame | T−6h بدون ورود جدید؛ T−3h خروج تدریجی؛ T−1h فروش اجباری هر چه مانده | سود realized در تومان |
| Circuit breaker فنی | ≥ ۵ خطای API در ۵ دقیقه یا دیتای کهنه > ۳ دقیقه | توقف ورود، فقط مدیریت خروج |

اندازه پوزیشن نهایی:
`size_tmn = min( (0.005 × equity) / SL% , 0.20 × equity , free_exposure ) × regime_mult × llm_size_multiplier`

---

## 6. نقش واقعی هوش مصنوعی (برای ۳۵ امتیاز)

| جزء | نوع | نقش در تصمیم | قابل اندازه‌گیری با |
|---|---|---|---|
| ML Scorer | LightGBM طبقه‌بند | فیلتر کاندیدها بر اساس احتمال موفقیت پس از کارمزد؛ آستانه از walk-forward | مقایسه بک‌تست «با ML» و «بدون ML» |
| MarketAnalyst Agent | LLM + ابزارها (Wallex MCP/داده‌های ما) | تعیین رژیم (TREND_UP / RANGE / HIGH_VOL / PANIC) و ضریب ریسک؛ فعال/غیرفعال کردن استراتژی‌ها | لاگ رژیم‌ها و عملکرد هر رژیم |
| TradeReviewer Agent | LLM با خروجی ساختاریافته | APPROVE/REJECT و size multiplier با دلیل متنی | آمار «کاندیدهای رد شده توسط AI» + نتیجه فرضی آن‌ها (counterfactual) |
| DailyReflector Agent | LLM | گزارش روزانه: تحلیل معاملات، پیشنهاد تغییر پارامتر **در بازه‌های مجاز** — اعمال فقط با بازبینی تیم و نسخه جدید | `reports/daily_*.md` |
| SHAP Explainer | توضیح‌پذیری ML | دلیل عددی هر امتیاز ML در لاگ | فیلد `ml.top_features` در لاگ |

**گزارش Counterfactual** (بسیار مهم برای قانع کردن داور): برای هر کاندیدی که AI رد کرده، سیستم در پس‌زمینه بررسی می‌کند اگر وارد می‌شد چه نتیجه‌ای می‌گرفت. خروجی: «AI از N معامله زیان‌ده جلوگیری کرد و M معامله سودده را از دست داد» ← اثبات اینکه AI واقعاً تصمیم‌ساز است.

**تکرارپذیری LLM:** temperature=0، مدل و نسخه پرامپت ثابت، و **کش پاسخ‌ها** با کلید `hash(model + prompt_version + input)`. در حالت replay، پاسخ‌ها از کش خوانده می‌شوند ← داور می‌تواند تصمیمات را دقیقاً بازتولید کند. اگر LLM در دسترس نبود: سیاست جایگزین قطعی (deterministic fallback) و ثبت `llm.status = "fallback"`.

---

## 7. لاگ تصمیم، تکرارپذیری و نسخه‌گذاری

- `logs/decisions.jsonl` — یک رکورد برای هر کاندید (پذیرفته یا رد) و هر خروج. اسکیمای کامل: ضمیمه C.
- `logs/orders.jsonl` — هر درخواست/پاسخ سفارش (بدون کلید API).
- `logs/fills.csv` — معاملات انجام‌شده (منبع گزارش داوری).
- `logs/equity.csv` — ارزش حساب هر ۵ دقیقه (برای محاسبه drawdown).
- `logs/llm/*.jsonl` — ورودی/خروجی کامل LLM + کش.
- `logs/system.log` — لاگ فنی ساختاریافته.
- هر رکورد شامل `git_sha`، `config_hash`، `model_version`، `prompt_version`، `mode` (backtest/paper/live).
- هر نسخه‌ای که روی لایو می‌رود: `git tag live-vX.Y` + یادداشت در `CHANGELOG.md` (چه چیزی و چرا عوض شد).
- `make replay DATE=...` — بازپخش یک بازه با داده ذخیره‌شده و کش LLM و مقایسه تصمیم‌ها با لاگ لایو.

---

## 8. استک فنی و ساختار ریپو

### 8.1 استک پیشنهادی
- **زبان: Python 3.11** — اکوسیستم کمّی (pandas، numpy، LightGBM، SHAP، پیاده‌سازی MCP در پایتون) خیلی جلوتر از Node است. (اگر تیم ترجیح قطعی به TypeScript دارد، فقط لایه کلاینت/اجرا را می‌شود TS کرد، ولی برای سرعت توصیه نمی‌شود.)
- `httpx` (async) + `tenacity` (retry) + `pydantic v2` (مدل‌ها و config) + `pyyaml`
- `pandas`, `numpy`, `pandas-ta` یا پیاده‌سازی دستی اندیکاتورها، `lightgbm`, `scikit-learn`, `shap`
- `sqlite3`/`SQLAlchemy` برای state، `pyarrow` برای parquet
- `structlog` برای لاگ ساختاریافته، `typer` برای CLI
- `pytest`, `pytest-asyncio`, `respx` (mock HTTP)
- LLM: کلاینت OpenAI-compatible (برای OpenRouter/هر gateway) با پشتیبانی proxy؛ ابزارهای Agent از طریق Wallex MCP و ابزارهای داخلی خودمان
- `matplotlib` برای نمودارهای گزارش
- Docker + docker compose + Makefile

### 8.2 ساختار ریپو

```
siavash-wallex-agent/
├── AGENTS.md                 # راهنمای Coding Agentها (ضمیمه E)
├── README.md                 # نصب و اجرا
├── CHANGELOG.md
├── Makefile
├── Dockerfile / docker-compose.yml
├── pyproject.toml
├── .env.example
├── config/
│   ├── base.yaml             # تنظیمات عمومی
│   ├── strategy.yaml         # پارامترهای استراتژی
│   ├── risk.yaml             # پارامترهای ریسک
│   └── denylist.txt          # بازارهای ممنوعه (ضمیمه A)
├── docs/
│   ├── STRATEGY.md           # داکیومنت داوری (R9)
│   ├── wallex_api_notes.md   # خروجی T-101
│   ├── RUNBOOK.md            # دیپلوی و عملیات
│   └── DEMO_SCRIPT.md
├── src/siavash/
│   ├── config.py
│   ├── logging.py
│   ├── exchange/   (client.py, models.py, guard.py, rate_limit.py, mcp_tools.py)
│   ├── data/       (history.py, live.py, store.py, universe.py, implied.py)
│   ├── features/   (indicators.py, orderbook.py, regime_features.py, builder.py)
│   ├── signals/    (base.py, trend.py, meanrev.py, implied_dev.py)
│   ├── ml/         (labeling.py, dataset.py, train.py, model.py, explain.py)
│   ├── agents/     (llm.py, cache.py, analyst.py, reviewer.py, reflector.py, prompts/)
│   ├── risk/       (sizing.py, limits.py, breakers.py, endgame.py)
│   ├── execution/  (broker_base.py, live_broker.py, paper_broker.py, router.py, reconcile.py)
│   ├── engine/     (orchestrator.py, positions.py, exits.py, decision_log.py)
│   ├── backtest/   (engine.py, fills.py, metrics.py, walkforward.py, report.py)
│   ├── reporting/  (judge_metrics.py, counterfactual.py, equity.py)
│   └── cli.py
├── scripts/                  # download_data, train_model, run_backtest, export_submission
├── data/                     # snapshot (parquet) – با git-lfs یا لینک دانلود
├── models/                   # مدل‌های نسخه‌دار + metadata.json
├── reports/                  # خروجی بک‌تست/شدو/لایو
├── logs/
└── tests/
```

---

### 8.3 قراردادهای کد مشترک (Contracts) — الزامی برای همه تسک‌ها

> این کدها در تسک T-007 **عیناً** در `src/siavash/core/` ساخته می‌شوند. همه ماژول‌ها فقط از طریق این تایپ‌ها و interfaceها با هم حرف می‌زنند. این بخش جلوی ناسازگاری کدی را می‌گیرد که دو نفر/دو Agent موازی می‌نویسند.

**افزوده‌ها به ساختار ریپو (۸.۲):**
```
src/siavash/core/        types.py, models.py, interfaces.py, symbols.py, ids.py
tests/unit/              تست‌های واحد (آینه ساختار src)
tests/integration/       تست با API واقعی (@pytest.mark.live)
tests/fakes/             FakeClock, InMemoryMarketData, FakeBroker, synthetic.py
tests/fixtures/wallex/   نمونه پاسخ‌های JSON والکس
docs/vendor/wallex/      مستندات خام والکس که انسان می‌گذارد
PROGRESS.md              وضعیت تسک‌ها (ضمیمه G)
```

**قواعد عمومی:**
- نماد استاندارد داخلی همیشه `BASE/QUOTE` با حروف بزرگ است (`BTC/TMN`). تبدیل به فرمت والکس فقط در `exchange/` با `core/symbols.py`.
- همه زمان‌ها `datetime` آگاه از منطقه زمانی در **UTC**. زمان کندل = زمان **باز شدن** کندل.
- پول، قیمت و مقدار در لایه سفارش/حسابداری: `Decimal`. در لایه ویژگی/ML: `float`.
- واحد پول حسابداری: تومان (TMN).
- نام ویژگی‌ها: `"{tf}_{name}"` مثل `15m_rsi14`, `1h_adx14`, `4h_ema200_dist`; ویژگی‌های بدون تایم‌فریم با پیشوند `x_` مثل `x_implied_z`, `x_btc_ret_1h`, `x_breadth_ema50`.

#### `src/siavash/core/types.py`
```python
from enum import Enum

class Mode(str, Enum):
    BACKTEST = "backtest"; PAPER = "paper"; LIVE = "live"; REPLAY = "replay"

class Side(str, Enum):
    BUY = "buy"; SELL = "sell"

class OrderType(str, Enum):
    LIMIT = "limit"; MARKET = "market"

class OrderStatus(str, Enum):
    NEW = "new"; PARTIALLY_FILLED = "partially_filled"; FILLED = "filled"
    CANCELED = "canceled"; REJECTED = "rejected"; EXPIRED = "expired"
    @property
    def is_terminal(self) -> bool:
        return self in {OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.REJECTED, OrderStatus.EXPIRED}

class Timeframe(str, Enum):
    M1 = "1m"; M15 = "15m"; H1 = "1h"; H4 = "4h"
    @property
    def seconds(self) -> int:
        return {"1m": 60, "15m": 900, "1h": 3600, "4h": 14400}[self.value]

class Regime(str, Enum):
    TREND_UP = "TREND_UP"; RANGE = "RANGE"; HIGH_VOL = "HIGH_VOL"; PANIC = "PANIC"

class StrategyId(str, Enum):
    S1_TREND_BREAKOUT = "S1_TREND_BREAKOUT"
    S2_MEAN_REVERSION = "S2_MEAN_REVERSION"
    S3_IMPLIED_DEV = "S3_IMPLIED_DEV"

class PositionStatus(str, Enum):
    PENDING_ENTRY = "PENDING_ENTRY"; OPEN = "OPEN"; PENDING_EXIT = "PENDING_EXIT"
    CLOSED = "CLOSED"; CANCELLED = "CANCELLED"

class ExitReason(str, Enum):
    STOP_LOSS = "STOP_LOSS"; TAKE_PROFIT = "TAKE_PROFIT"; TP1_PARTIAL = "TP1_PARTIAL"
    TRAILING_STOP = "TRAILING_STOP"; TIME_STOP = "TIME_STOP"; SIGNAL = "SIGNAL"
    BREAKER = "BREAKER"; ENDGAME = "ENDGAME"; OPERATOR = "OPERATOR"

class DecisionType(str, Enum):
    ENTRY_CANDIDATE = "ENTRY_CANDIDATE"; EXIT = "EXIT"; REGIME_UPDATE = "REGIME_UPDATE"
    UNIVERSE_UPDATE = "UNIVERSE_UPDATE"; BREAKER = "BREAKER"
    OPERATOR_COMMAND = "OPERATOR_COMMAND"; ENDGAME = "ENDGAME"

class RejectStage(str, Enum):
    FEE_FILTER = "FEE_FILTER"; ML = "ML"; EV = "EV"; LLM = "LLM"; RISK = "RISK"; EXECUTION = "EXECUTION"

class BreakerState(str, Enum):
    NORMAL = "NORMAL"; NO_NEW_ENTRIES = "NO_NEW_ENTRIES"; FLATTEN_AND_HALT = "FLATTEN_AND_HALT"

class ComponentStatus(str, Enum):
    OK = "ok"; SKIPPED = "skipped"; FALLBACK = "fallback"; ERROR = "error"
```

#### `src/siavash/core/models.py`
```python
from __future__ import annotations
from datetime import datetime
from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict
from .types import *

class _M(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

class MarketInfo(_M):
    symbol: str                 # "BTC/TMN"
    exchange_symbol: str        # فرمت والکس، مثلاً "BTCTMN"
    base: str
    quote: str
    price_tick: Decimal         # کوچک‌ترین گام قیمت
    qty_step: Decimal           # کوچک‌ترین گام مقدار
    min_qty: Decimal
    min_notional: Decimal       # بر حسب quote
    is_active: bool
    is_spot: bool = True

class Candle(_M):
    symbol: str
    timeframe: Timeframe
    ts_open: datetime
    open: float; high: float; low: float; close: float
    volume: float               # بر حسب base
    is_filled: bool = False     # True اگر با forward-fill ساخته شده

class OrderBookLevel(_M):
    price: Decimal
    qty: Decimal

class OrderBook(_M):
    symbol: str
    ts: datetime
    bids: list[OrderBookLevel]  # نزولی بر اساس قیمت
    asks: list[OrderBookLevel]  # صعودی بر اساس قیمت
    @property
    def best_bid(self) -> Decimal | None: return self.bids[0].price if self.bids else None
    @property
    def best_ask(self) -> Decimal | None: return self.asks[0].price if self.asks else None
    @property
    def mid(self) -> Decimal | None:
        return (self.best_bid + self.best_ask) / 2 if self.bids and self.asks else None
    @property
    def spread_pct(self) -> float | None:
        if not (self.bids and self.asks): return None
        return float((self.best_ask - self.best_bid) / self.mid * 100)

class Ticker(_M):
    symbol: str
    ts: datetime
    last: Decimal
    bid: Decimal | None
    ask: Decimal | None
    volume_24h_quote: Decimal   # حجم ۲۴ساعته بر حسب quote

class PublicTrade(_M):
    symbol: str
    ts: datetime
    price: Decimal
    qty: Decimal
    side: Side | None           # سمت taker اگر API بدهد

class Balance(_M):
    asset: str
    free: Decimal
    locked: Decimal
    @property
    def total(self) -> Decimal: return self.free + self.locked

class OrderRequest(_M):
    symbol: str
    side: Side
    type: OrderType
    qty: Decimal
    price: Decimal | None       # برای LIMIT الزامی
    client_order_id: str
    decision_id: str
    purpose: Literal["entry", "exit", "tp1", "endgame", "operator", "smoke_test"]

class Order(_M):
    order_id: str | None        # شناسه صرافی
    client_order_id: str
    symbol: str
    side: Side
    type: OrderType
    price: Decimal | None
    qty: Decimal
    filled_qty: Decimal = Decimal("0")
    avg_fill_price: Decimal | None = None
    status: OrderStatus
    created_at: datetime
    updated_at: datetime

class Fill(_M):
    fill_id: str
    order_id: str | None
    client_order_id: str
    symbol: str
    side: Side
    price: Decimal
    qty: Decimal
    fee: Decimal
    fee_asset: str
    ts: datetime
    is_maker: bool | None = None

class Candidate(_M):
    candidate_id: str           # ULID
    ts: datetime                # زمان بسته شدن کندل سیگنال
    symbol: str
    strategy: StrategyId
    entry_ref_price: float
    stop_loss: float
    take_profit: float
    tp1_price: float | None = None
    time_stop_minutes: int
    expected_move_pct: float    # (take_profit/entry - 1) * 100
    conditions: dict[str, float | str | bool]  # شرط‌ها و مقادیرشان (برای لاگ)

class MLScore(_M):
    status: ComponentStatus
    model_version: str | None = None
    p_win: float | None = None
    threshold: float | None = None
    ev_pct: float | None = None
    top_features: list[tuple[str, float]] = Field(default_factory=list)

class RegimeAssessment(_M):
    ts: datetime
    regime: Regime
    risk_multiplier: float = Field(ge=0.0, le=1.0)
    enabled_strategies: list[StrategyId]
    reasons: list[str]
    source: Literal["llm", "rule", "fallback"]
    rule_regime: Regime
    status: ComponentStatus = ComponentStatus.OK

class TradeReview(_M):
    status: ComponentStatus
    verdict: Literal["APPROVE", "REJECT"]
    size_multiplier: float = Field(ge=0.25, le=1.0)
    reasons: list[str]
    risk_flags: list[str] = Field(default_factory=list)
    confidence: float | None = None
    model: str | None = None
    prompt_version: str | None = None
    cache_key: str | None = None

class PortfolioSnapshot(_M):
    ts: datetime
    equity_tmn: Decimal
    cash_tmn: Decimal
    peak_equity_tmn: Decimal
    exposure_tmn: Decimal
    open_positions: list["Position"]
    trades_last_hour: int
    trades_today: int
    daily_pnl_pct: float
    drawdown_pct: float         # از اوج، مثبت = افت
    breaker_state: BreakerState

class RiskDecision(_M):
    approved: bool
    size_tmn: Decimal = Decimal("0")
    qty: Decimal = Decimal("0")
    checks_failed: list[str] = Field(default_factory=list)   # کدهای RISK_*
    equity_tmn: Decimal

class Position(BaseModel):     # قابل تغییر (state)
    model_config = ConfigDict(extra="forbid")
    position_id: str
    symbol: str
    strategy: StrategyId
    status: PositionStatus
    entry_decision_id: str
    qty: Decimal = Decimal("0")
    avg_entry: Decimal | None = None
    stop_loss: Decimal
    take_profit: Decimal
    tp1_price: Decimal | None = None
    tp1_done: bool = False
    trailing_stop: Decimal | None = None
    highest_price: Decimal | None = None
    time_stop_at: datetime
    opened_at: datetime | None = None
    closed_at: datetime | None = None
    exit_reason: ExitReason | None = None
    realized_pnl_tmn: Decimal = Decimal("0")
    fees_tmn: Decimal = Decimal("0")
    order_client_ids: list[str] = Field(default_factory=list)

class DecisionRecord(BaseModel):
    """اسکیمای هر خط logs/decisions.jsonl — جزئیات در ضمیمه C"""
    model_config = ConfigDict(extra="forbid")
    decision_id: str
    ts: datetime
    mode: Mode
    git_sha: str
    version_tag: str | None
    config_hash: str
    type: DecisionType
    symbol: str | None = None
    strategy: StrategyId | None = None
    regime: RegimeAssessment | None = None
    signal: Candidate | None = None
    ml: MLScore | None = None
    llm_review: TradeReview | None = None
    risk: RiskDecision | None = None
    position_id: str | None = None
    exit_reason: ExitReason | None = None
    outcome_action: Literal["ORDER_PLACED", "REJECTED", "EXITED", "INFO"]
    rejected_at: RejectStage | None = None
    reject_code: str | None = None
    client_order_ids: list[str] = Field(default_factory=list)
    final_reason: str           # یک جمله خوانا برای داور
    extra: dict = Field(default_factory=dict)

PortfolioSnapshot.model_rebuild()
```

#### `src/siavash/core/interfaces.py`
```python
from __future__ import annotations
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Protocol, Mapping
from .models import *
from .types import *

FeatureRow = Mapping[str, float]        # ویژگی‌های یک (symbol, ts)

class Clock(ABC):
    @abstractmethod
    def now(self) -> datetime: ...                  # UTC
    @abstractmethod
    async def sleep(self, seconds: float) -> None: ...

class MarketData(ABC):
    @abstractmethod
    async def list_markets(self) -> list[MarketInfo]: ...
    @abstractmethod
    async def get_candles(self, symbol: str, tf: Timeframe, start: datetime, end: datetime) -> list[Candle]: ...
    @abstractmethod
    async def get_orderbook(self, symbol: str, depth: int = 20) -> OrderBook: ...
    @abstractmethod
    async def get_ticker(self, symbol: str) -> Ticker: ...
    @abstractmethod
    async def get_recent_trades(self, symbol: str, limit: int = 100) -> list[PublicTrade]: ...

class Broker(ABC):
    """Live، Paper و Backtest همه این را پیاده می‌کنند. موتور فقط این را می‌شناسد."""
    @abstractmethod
    async def place_order(self, req: OrderRequest) -> Order: ...
    @abstractmethod
    async def cancel_order(self, symbol: str, client_order_id: str) -> Order: ...
    @abstractmethod
    async def get_order(self, symbol: str, client_order_id: str) -> Order | None: ...
    @abstractmethod
    async def get_open_orders(self, symbol: str | None = None) -> list[Order]: ...
    @abstractmethod
    async def get_balances(self) -> dict[str, Balance]: ...
    @abstractmethod
    async def get_fills(self, since: datetime) -> list[Fill]: ...

class SignalGenerator(ABC):
    strategy_id: StrategyId
    @abstractmethod
    def generate(self, symbol: str, ts: datetime, features: FeatureRow,
                 regime: RegimeAssessment) -> list[Candidate]: ...

class Scorer(Protocol):
    def score(self, c: Candidate, features: FeatureRow) -> MLScore: ...

class RegimeProvider(Protocol):
    async def assess(self, market_features: FeatureRow, ts: datetime) -> RegimeAssessment: ...

class Reviewer(Protocol):
    async def review(self, c: Candidate, features: FeatureRow, ml: MLScore,
                     regime: RegimeAssessment, portfolio: PortfolioSnapshot) -> TradeReview: ...

class RiskManager(Protocol):
    def evaluate(self, c: Candidate, ml: MLScore, review: TradeReview | None,
                 regime: RegimeAssessment, portfolio: PortfolioSnapshot,
                 market: MarketInfo, book: OrderBook) -> RiskDecision: ...

class DecisionLogger(Protocol):
    def write(self, record: DecisionRecord) -> None: ...
```

#### `src/siavash/core/symbols.py` و `ids.py`
```python
# symbols.py
def to_canonical(exchange_symbol: str, known_quotes=("TMN", "USDT")) -> str: ...  # "BTCTMN" -> "BTC/TMN"
def to_exchange(symbol: str) -> str: ...                                            # "BTC/TMN" -> "BTCTMN"
def split(symbol: str) -> tuple[str, str]: ...                                      # -> ("BTC","TMN")

# ids.py
def new_ulid() -> str: ...                     # با کتابخانه python-ulid
def client_order_id(decision_id: str, leg: str, attempt: int) -> str:
    """فرمت: SIAV-{decision_id}-{leg}{attempt}  ؛ leg ∈ {E,X,T,G,O,S}
    (Entry, eXit, Tp1, endGame, Operator, Smoke). اگر والکس محدودیت طول دارد
    (T-101)، decision_id را به ۱۶ کاراکتر آخر کوتاه کن و در یادداشت API ثبت کن."""
def is_ours(client_order_id: str) -> bool: return client_order_id.startswith("SIAV-")
```

#### کدهای رد ریسک (ثابت؛ در `risk/codes.py`)
`RISK_SYMBOL_NOT_ALLOWED, RISK_BREAKER_ACTIVE, RISK_MAX_POSITIONS, RISK_MARKET_ALREADY_OPEN, RISK_MAX_EXPOSURE, RISK_INSUFFICIENT_CASH, RISK_BELOW_MIN_NOTIONAL, RISK_COOLDOWN_LOSSES, RISK_COOLDOWN_MARKET, RISK_MAX_TRADES_HOUR, RISK_MAX_TRADES_DAY, RISK_SPREAD_TOO_WIDE, RISK_STALE_DATA, RISK_OPPOSITE_ORDER_OPEN, RISK_ENDGAME, RISK_REGIME_PANIC, RISK_SIZE_ZERO`


## 9. تقسیم کار، اولویت‌ها و زمان‌بندی

### 9.1 نقش‌ها
- **نفر A — زیرساخت و اجرا (Backend/Infra):** EPICهای E0, E1, E6 (بخش اجرایی), E7, E8, E11, گزارش‌ها.
- **نفر B — استراتژی و هوش مصنوعی (Quant/AI):** EPICهای E2, E3, E4, E5, E9, E10, STRATEGY.md.
- نفر سوم تیم (اگر کمی وقت داشت): ویدیو دمو، بازبینی مستندات، تست دستیِ checklistها.

### 9.2 اولویت‌ها
- **P0** = بدون آن لایو نمی‌رویم (MVP).
- **P1** = برای امتیاز فنی بالا لازم است.
- **P2** = اگر وقت شد.

### 9.3 مسیر بحرانی (Critical Path) و Milestoneها

| Milestone | محتوا | شرط عبور |
|---|---|---|
| **M1 — اتصال** | E0 + E1 (P0) | گرفتن کندل/دفتر سفارش/موجودی؛ ثبت و لغو یک سفارش تست روی حساب (با حداقل مبلغ، در صورت مجاز بودن) |
| **M2 — موتور قطعی** | E2, E3, E6, E7, E8 (P0) با سیگنال‌ها بدون AI + PaperBroker | اجرای ۲ ساعت paper بدون خطا، لاگ تصمیم کامل |
| **M3 — بک‌تست** | E9 (P0) | گزارش بک‌تست سه استراتژی روی حداقل ۶۰ روز داده با کارمزد |
| **M4 — AI** | E4, E5 (P0/P1) | بک‌تست با ML بهتر از بدون ML (یا حداقل drawdown کمتر)؛ LLM با کش و fallback |
| **M5 — Shadow** | E10 | حداقل ۱۲ تا ۲۴ ساعت شدو تست؛ مقایسه با بک‌تست |
| **M6 — Go-Live** | T-1107 checklist | لایو با پیکربندی محافظه‌کار (ضریب ریسک ۰٫۵) |
| **M7 — تحویل** | E13 | ریپو، STRATEGY.md، لاگ‌ها، ویدیو |

> **نکته حیاتی زمان‌بندی:** اگر طبق قوانین هکاتون مجازید قبل از شروع رسمی کد بزنید، M1 تا M5 را **قبل از هکاتون** تمام کنید تا از ساعت اول روز اول ربات با حساب چالش لایو باشد. اگر مجاز نیستید: روز ۱ تا ظهر M1+M2 با استراتژی S3+S1 بدون ML و ریسک ۰٫۵ لایو شود؛ بعدازظهر روز ۱ و روز ۲ بک‌تست/ML/LLM و انتشار نسخه v2؛ روز ۳ تثبیت، Endgame و تحویلی‌ها.

---

### 9.4 ترتیب اجرای خطی (Execution Order) — مرجع اتوپایلوت و PROGRESS.md

> Agent تسک‌ها را **به ترتیب ردیف** این جدول برمی‌دارد (نه به ترتیب شماره شناسه). وابستگی «(نرم)» یعنی می‌توان با Fake یا حالت fallback شروع کرد و بعداً تکمیل شد. این جدول به‌صورت خودکار از متادیتای بخش ۱۰ ساخته و اعتبارسنجی شده است (هیچ وابستگی سختی بعد از تسک وابسته‌اش نیامده).

| # | ID | عنوان | مالک | نوع | اولویت | وابستگی‌ها | Milestone |
|---|----|-------|------|-----|--------|------------|-----------|
| 1 | T-001 | اسکلت ریپو | A | AGENT | P0 | — | M1 |
| 2 | T-002 | سیستم پیکربندی | A | AGENT | P0 | T-001 | M1 |
| 3 | T-003 | لاگ ساختاریافته | A | AGENT | P0 | T-002 | M1 |
| 4 | T-007 | قراردادهای کد مشترک | A | AGENT | P0 | T-001 | M1 |
| 5 | T-008 | Fakeها و داده مصنوعی | A | AGENT | P0 | T-007 | M1 |
| 6 | T-605 | نویسنده لاگ تصمیم | A | AGENT | P0 | T-003, T-007 | M2 |
| 7 | T-004 | Docker و Makefile کامل | A | AGENT | P0 | T-001 | M1 |
| 8 | T-005 | فایل راهنمای Agent | A | AGENT | P0 | T-001 | M1 |
| 9 | T-010 | فایل PROGRESS.md | A | AGENT | P0 | T-001 | M1 |
| 10 | T-301 | کتابخانه اندیکاتورها | B | AGENT | P0 | T-001 | M2 |
| 11 | T-303 | Feature Builder چندتایم‌فریمی | B | AGENT | P0 | T-301, T-007 | M2 |
| 12 | T-205 | قیمت ضمنی و انحراف | B | AGENT | P0 | T-303 | M2 |
| 13 | T-304 | ویژگی‌های رژیم و رژیم‌یاب قاعده‌ای | B | AGENT | P0 | T-303 | M2 |
| 14 | T-305 | Signal Generatorها | B | AGENT | P0 | T-304, T-205 | M2 |
| 15 | T-306 | فیلتر کارمزد | B | AGENT | P0 | T-305 | M2 |
| 16 | T-101 | کشف و مستندسازی API والکس | A | AGENT+HUMAN | P0 | T-001 | M1 |
| 17 | T-102 | کلاینت HTTP پایه | A | AGENT | P0 | T-101, T-003 | M1 |
| 18 | T-103 | متادیتای بازار و گرد کردن | A | AGENT | P0 | T-102 | M1 |
| 19 | T-104 | endpointهای داده بازار | A | AGENT | P0 | T-103 | M1 |
| 20 | T-105 | endpointهای حساب و سفارش | A | AGENT | P0 | T-103 | M1 |
| 21 | T-106 | SymbolGuard | A | AGENT | P0 | T-105 | M1 |
| 22 | T-109 | بررسی شبکه و محل اجرا | A | HUMAN | P0 | — | M1 |
| 23 | T-108 | Smoke test حساب واقعی | A | AGENT+HUMAN | P0 | T-106, T-605, T-109 | M1 |
| 24 | T-201 | دانلودر داده تاریخی | B | AGENT+HUMAN | P0 | T-104 | M3 |
| 25 | T-202 | اعتبارسنجی و پاک‌سازی داده | B | AGENT | P0 | T-201 | M3 |
| 26 | T-203 | سرویس داده لایو | A | AGENT | P0 | T-104, T-008 | M2 |
| 27 | T-204 | انتخاب Universe | B | AGENT | P0 | T-203, T-106 | M2 |
| 28 | T-302 | ویژگی‌های دفتر سفارش | B | AGENT | P1 | T-303 | M2 |
| 29 | T-806 | LiveBroker و تست قراردادی Broker | A | AGENT | P0 | T-106, T-008 | M2 |
| 30 | T-1001 | PaperBroker (شبیه‌سازی با دفتر سفارش واقعی) | A | AGENT | P0 | T-806, T-203 | M2 |
| 31 | T-801 | Order Router | A | AGENT | P0 | T-806, T-103 | M2 |
| 32 | T-802 | Idempotency و بازیابی سفارش | A | AGENT | P0 | T-801 | M2 |
| 33 | T-803 | رهگیری سفارش و fillها | A | AGENT | P0 | T-802, T-605 | M2 |
| 34 | T-603 | Position Manager | A | AGENT | P0 | T-803 | M2 |
| 35 | T-604 | منطق خروج | A | AGENT | P0 | T-603 | M2 |
| 36 | T-1101 | State ماندگار | A | AGENT | P0 | T-603 | M2 |
| 37 | T-701 | اندازه پوزیشن | A | AGENT | P0 | T-103, T-007 | M2 |
| 38 | T-702 | چک‌های پیش از معامله و RiskManager | A | AGENT | P0 | T-701, T-106 | M2 |
| 39 | T-1202 | ردیاب Equity و Drawdown | A | AGENT | P0 | T-1101, T-203 | M2 |
| 40 | T-703 | Circuit Breakerها | A | AGENT | P0 | T-1202, T-102 | M2 |
| 41 | T-704 | Endgame Liquidator | A | AGENT | P0 | T-801, T-1202 | M6 |
| 42 | T-705 | تست‌های جامع ریسک | A | AGENT | P0 | T-702, T-703, T-704 | M2 |
| 43 | T-804 | Reconciliation | A | AGENT | P0 | T-1101, T-803 | M2 |
| 44 | T-601 | Orchestrator | A | AGENT | P0 | T-203, T-604, T-703, T-704, T-804, T-1001, T-1101 | M2 |
| 45 | T-501 | لایه انتزاعی LLM | B | AGENT | P0 | T-002, T-003 | M4 |
| 46 | T-502 | کش و Replay پاسخ‌های LLM | B | AGENT | P0 | T-501 | M4 |
| 47 | T-503 | اسکیماها، اعتبارسنجی و fallback قطعی | B | AGENT | P0 | T-502 | M4 |
| 48 | T-507 | پرامپت‌های نسخه‌دار | B | AGENT | P0 | T-503 | M4 |
| 49 | T-107 | یکپارچه‌سازی Wallex MCP | B | AGENT | P1 | T-104, T-501 | M4 |
| 50 | T-505 | TradeReviewer Agent | B | AGENT | P0 | T-507, T-107 (نرم) | M4 |
| 51 | T-504 | MarketAnalyst Agent | B | AGENT | P1 | T-507, T-304, T-107 (نرم) | M4 |
| 52 | T-602 | Decision Pipeline | B | AGENT | P0 | T-601, T-505, T-702, T-306, T-406 (نرم) | M2 |
| 53 | T-1104 | CLI | A | AGENT | P0 | T-601 | M2 |
| 54 | T-1102 | Supervisor و Heartbeat | A | AGENT | P0 | T-601 | M2 |
| 55 | T-805 | متریک‌های سرعت | A | AGENT | P1 | T-602 | M5 |
| 56 | T-1103 | هشدارها | A | AGENT | P1 | T-703 | M5 |
| 57 | T-902 | BacktestBroker، مدل fill، کارمزد و لغزش | B | AGENT | P0 | T-806, T-202 | M3 |
| 58 | T-903 | متریک‌های عملکرد | B | AGENT | P0 | T-902 | M3 |
| 59 | T-901 | موتور بک‌تست | B | AGENT | P0 | T-601, T-602, T-902, T-903 | M3 |
| 60 | T-905 | گزارش‌ساز بک‌تست | B | AGENT | P0 | T-901 | M3 |
| 61 | T-401 | برچسب‌گذاری Triple-Barrier | B | AGENT | P1 | T-305, T-306 | M4 |
| 62 | T-402 | ساخت دیتاست | B | AGENT | P1 | T-401, T-202 | M4 |
| 63 | T-403 | آموزش Walk-forward | B | AGENT | P1 | T-402 | M4 |
| 64 | T-404 | کالیبراسیون و انتخاب آستانه | B | AGENT | P1 | T-403 | M4 |
| 65 | T-405 | رجیستری مدل و SHAP | B | AGENT | P1 | T-404 | M4 |
| 66 | T-406 | ML Scorer | B | AGENT | P1 | T-405 | M4 |
| 67 | T-904 | Walk-forward، حساسیت پارامتر و مقایسه نسخه‌ها | B | AGENT+HUMAN | P1 | T-905, T-406 | M4 |
| 68 | T-1106 | نسخه‌گذاری دیپلوی | A | AGENT | P0 | T-004, T-003 | M6 |
| 69 | T-1105 | RUNBOOK | A | AGENT | P0 | T-1104, T-1106 | M6 |
| 70 | T-1003 | مقایسه Backtest ↔ Shadow ↔ Live | B | AGENT | P1 | T-901, T-903 | M5 |
| 71 | T-1002 | اجرای Shadow | B | HUMAN | P0 | T-1106, T-1105, T-602 | M5 |
| 72 | T-1201 | گزارش متریک‌های داوری | B | AGENT | P0 | T-903, T-805 | M5 |
| 73 | T-1203 | گزارش Counterfactual نقش AI | B | AGENT | P1 | T-1201, T-401 | M5 |
| 74 | T-1107 | Go-Live Checklist | A | AGENT+HUMAN | P0 | T-1002, T-704, T-1105 | M6 |
| 75 | T-506 | DailyReflector Agent | B | AGENT | P2 | T-1201, T-507 | M7 |
| 76 | T-1305 | دستور Replay برای داور | B | AGENT | P1 | T-1003, T-502 | M7 |
| 77 | T-206 | Snapshot داده برای داوران | B | AGENT | P1 | T-202 | M7 |
| 78 | T-006 | CI | A | AGENT | P1 | T-001 | M7 |
| 79 | T-1301 | README | A | AGENT | P0 | T-1104, T-905 | M7 |
| 80 | T-1302 | STRATEGY.md (داکیومنت داوری) | B | AGENT+HUMAN | P0 | T-904, T-1203, T-1201 | M7 |
| 81 | T-1303 | بسته لاگ تحویلی | A | AGENT | P0 | T-605, T-1201 | M7 |
| 82 | T-1204 | داشبورد ساده (اختیاری) | A | AGENT | P2 | T-1201 | M7 |
| 83 | T-1304 | ویدیو دمو | B | AGENT+HUMAN | P0 | T-1201, T-1302 | M7 |
| 84 | T-1306 | چک‌لیست نهایی تحویل | A | HUMAN | P0 | T-1303, T-1304, T-1302 | M7 |

**نقاط هم‌گام‌سازی دو نفره:**
- **SYNC-1 (بعد از ردیف T-010):** قراردادهای `core/`، Fakeها و config در main merge شده‌اند. از این لحظه نفر B می‌تواند E3/E5/E4 را موازی جلو ببرد.
- **SYNC-2 (قبل از T-602):** Orchestrator (A) و Reviewer/RiskManager (A/B) آماده‌اند؛ B pipeline را وصل می‌کند.
- **SYNC-3 (قبل از T-1002):** نسخه کامل روی سرور برای shadow؛ هر دو نفر گزارش را بررسی می‌کنند.

**برش MVP (اگر وقت کم است):** همه ردیف‌های P0 را انجام دهید و با `ml.enabled=false` (ML در pipeline با status=SKIPPED رد می‌شود) و در صورت نیاز `llm.analyst.enabled=false` لایو شوید. تسک‌های P1 (ML، Analyst، counterfactual، replay) را در طول چالش با shadow تست و با نسخه جدید (T-1106) اضافه کنید.

**تسک‌هایی که Agent انجام نمی‌دهد (HUMAN):** T-109, T-1002, T-1306. تسک‌های AGENT+HUMAN را Agent آماده می‌کند و بخش انسانی آن‌ها در شرح هر تسک آمده است.

---

## 10. تسک‌ها

**قالب هر تسک:**
- خط متادیتا: مالک (A = زیرساخت/اجرا، B = استراتژی/AI) | نوع | اولویت | وابستگی‌ها | Milestone
- **خروجی:** دقیقاً همین فایل‌ها ساخته/تغییر داده می‌شوند (به‌علاوه به‌روزرسانی PROGRESS.md و در صورت لزوم CHANGELOG.md).
- **شرح:** کاری که باید انجام شود.
- **پذیرش:** چک‌لیست. بندهای داخل `` ` `` دستور قابل اجرا هستند و باید exit code صفر بدهند.

قواعد مشترک همه تسک‌ها (در پذیرش تکرار نمی‌شوند ولی الزامی‌اند): `make lint` و `make test` سبز؛ type hint کامل؛ docstring برای توابع عمومی؛ هیچ پارامتر hardcode نشده؛ رعایت «ممنوعیت‌های مطلق».

---

### E0 — راه‌اندازی پروژه

### T-001 — اسکلت ریپو
`مالک: A | نوع: AGENT | اولویت: P0 | وابستگی: — | Milestone: M1`
**خروجی:** ساختار پوشه‌های بخش ۸.۲ و ۸.۳ (با `__init__.py`)، `pyproject.toml`، `.gitignore`، `.env.example`، `README.md` (اولیه)، `CHANGELOG.md`، `tests/unit/test_smoke.py`، `Makefile` (اولیه).
**شرح:**
- Python 3.11؛ بسته `siavash` در `src/`؛ نصب با `pip install -e ".[dev]"`.
- وابستگی‌های اصلی (نسخه pin شده با `~=`): httpx, tenacity, pydantic>=2, pydantic-settings, pyyaml, pandas, numpy, pyarrow, lightgbm, scikit-learn, shap, structlog, typer, python-ulid, matplotlib, openai (کلاینت سازگار)، mcp (SDK پایتون MCP)، aiosqlite.
- dev: pytest, pytest-asyncio, respx, hypothesis, ruff, mypy, freezegun.
- `.gitignore`: `.env`, `logs/`, `state/`, `data/raw/`, `data/live/`, `__pycache__/`, `.venv/`, `*.parquet` به جز `data/snapshot/`.
- `.env.example`: `MODE=paper`, `WALLEX_API_KEY=`, `WALLEX_BASE_URL=`, `LLM_API_KEY=`, `LLM_BASE_URL=`, `LLM_MODEL=`, `LLM_FALLBACK_MODEL=`, `HTTPS_PROXY=`, `ALERT_WEBHOOK_URL=`, `VERSION_TAG=`.
- Makefile اولیه: `install`, `lint` (ruff check + mypy src)، `test` (pytest tests/unit -q)، `test-live` (pytest tests/integration -m live).
- `pytest.ini`/`pyproject`: `asyncio_mode=auto`، marker `live` تعریف و به‌طور پیش‌فرض با `-m "not live"` اجرا.
**پذیرش:**
- [ ] `pip install -e ".[dev]"`
- [ ] `make lint`
- [ ] `make test`
- [ ] `python -c "import siavash"`

### T-002 — سیستم پیکربندی
`مالک: A | نوع: AGENT | اولویت: P0 | وابستگی: T-001 | Milestone: M1`
**خروجی:** `src/siavash/config.py`، `config/base.yaml`، `config/strategy.yaml`، `config/risk.yaml` (از ضمیمه B و I)، `tests/unit/test_config.py`.
**شرح:**
- مدل‌های pydantic: `BaseConfig`, `StrategyConfig`, `RiskConfig`, `LLMConfig`, `AppConfig` (ترکیب همه + env). بارگذاری ترتیبی: yamlها → env (env برنده).
- فلگ‌های روشن/خاموش (برای MVP): `ml.enabled`, `llm.analyst.enabled`, `llm.reviewer.enabled`, `strategies.enabled: [S1_TREND_BREAKOUT, S3_IMPLIED_DEV, S2_MEAN_REVERSION]`.
- اعتبارسنجی بازه‌ها (Field با ge/le): `risk_per_trade_pct ≤ 1.0`, `max_position_pct ≤ 25`, `max_total_exposure_pct ≤ 60`, `kill_switch_drawdown_pct ≤ 8`, `daily_loss_limit_pct ≤ 3`, `llm_size_multiplier_range` زیرمجموعه [0.25,1].
- `config_hash(cfg) -> str`: `"sha256:" + sha256(json.dumps(cfg.model_dump(mode="json", exclude={secrets}), sort_keys=True))`. اسرار (کلیدها) در hash نیستند.
- تابع `load_config(path="config", env_file=".env") -> AppConfig`.
**پذیرش:**
- [ ] `pytest tests/unit/test_config.py -q` شامل: بارگذاری موفق، رد مقدار خارج از بازه با پیام واضح، برابری hash برای config یکسان، تفاوت hash با تغییر یک پارامتر، عدم حضور کلید API در hash/dump.

### T-003 — لاگ ساختاریافته
`مالک: A | نوع: AGENT | اولویت: P0 | وابستگی: T-002 | Milestone: M1`
**خروجی:** `src/siavash/logging.py`، `src/siavash/runtime.py`، `tests/unit/test_logging.py`.
**شرح:**
- `runtime.py`: `RuntimeInfo` با `run_id` (ULID)، `git_sha` (از `git rev-parse --short HEAD` یا env `GIT_SHA`، در غیر این صورت `"unknown"`)، `version_tag` (env)، `mode`، `config_hash`. تابع `get_runtime()` (singleton).
- `logging.py`: `setup_logging(level, log_dir)`؛ structlog با خروجی JSON در `logs/system.log` (rotating) و خروجی رنگی در کنسول؛ processor که فیلدهای runtime را اضافه کند؛ processor ماسک: هر کلیدی شامل `key|token|secret|password|authorization` → `"***"`، و هر رشته‌ای که با مقدار `WALLEX_API_KEY`/`LLM_API_KEY` برابر باشد.
**پذیرش:**
- [ ] `pytest tests/unit/test_logging.py -q` شامل تست ماسک کلید و حضور `run_id/git_sha/mode` در هر رکورد.

### T-007 — قراردادهای کد مشترک
`مالک: A | نوع: AGENT | اولویت: P0 | وابستگی: T-001 | Milestone: M1`
**خروجی:** `src/siavash/core/{types,models,interfaces,symbols,ids}.py`، `src/siavash/risk/codes.py`، `tests/unit/core/test_models.py`، `tests/unit/core/test_symbols.py`.
**شرح:** کد بخش ۸.۳ را **عیناً** پیاده کن (تکمیل بدنه توابع `symbols.py` و `ids.py`). پراپرتی‌ها و validatorها کار کنند. JSON serialization همه مدل‌ها با `model_dump(mode="json")` (Decimal → str، datetime → ISO با Z).
**پذیرش:**
- [ ] `pytest tests/unit/core -q`: round-trip JSON همه مدل‌ها؛ `to_canonical("BTCTMN")=="BTC/TMN"`، `to_canonical("USDTTMN")=="USDT/TMN"`، `to_canonical("BTCUSDT")=="BTC/USDT"`؛ `TradeReview(size_multiplier=1.5)` خطا بدهد؛ `OrderBook.spread_pct` درست؛ `client_order_id` با پیشوند `SIAV-` و `is_ours`.

### T-008 — Fakeها و داده مصنوعی
`مالک: A | نوع: AGENT | اولویت: P0 | وابستگی: T-007 | Milestone: M1`
**خروجی:** `tests/fakes/{clock.py,market_data.py,broker.py,synthetic.py}`، `tests/unit/test_fakes.py`.
**شرح:**
- `FakeClock(start)`: `now()`، `sleep()` فقط زمان را جلو می‌برد، `advance(seconds)`.
- `InMemoryMarketData(markets, candles, books, tickers)`: پیاده‌سازی `MarketData` روی داده درون حافظه.
- `FakeBroker(balances, fee_pct)`: پیاده‌سازی `Broker`؛ MARKET فوراً با قیمت ticker پر می‌شود؛ LIMIT فقط با متد `simulate_fill(client_order_id, price)`؛ سفارش تکراری با همان `client_order_id` همان سفارش را برمی‌گرداند (idempotent)؛ کسر کارمزد؛ بررسی موجودی.
- `synthetic.py`: `make_synthetic_market(seed, days, tf="15m")` → DataFrameهایی برای `BTC/USDT`، `USDT/TMN`، `BTC/TMN` (+ `ETH/*`) با: GBM و تغییر رژیم (روند/رنج/پرنوسان)، و رابطه `BTC/TMN ≈ BTC/USDT × USDT/TMN` با **تأخیر ۲ کندلی و نویز** تا سیگنال S3 قابل تست باشد. کاملاً قطعی با seed.
**پذیرش:**
- [ ] `pytest tests/unit/test_fakes.py -q`: قطعی بودن داده با seed یکسان؛ همبستگی تأخیری BTC/TMN با implied در lag=2 بیشتر از lag=0؛ idempotency FakeBroker.

### T-605 — نویسنده لاگ تصمیم
`مالک: A | نوع: AGENT | اولویت: P0 | وابستگی: T-003, T-007 | Milestone: M2`
**خروجی:** `src/siavash/engine/decision_log.py`، `tests/unit/engine/test_decision_log.py`.
**شرح:** `JsonlDecisionLogger(path)` پیاده‌سازی `DecisionLogger`: append-only، یک خط JSON برای هر `DecisionRecord`، flush + `os.fsync` بعد از هر نوشتن، قفل async/thread. تابع کمکی `build_record(type, **fields)` که `decision_id`، `ts` (از Clock)، و فیلدهای runtime را پر کند. همچنین `OrdersLogger` (`logs/orders.jsonl`: هر request/response سفارش، بدون هدرها) و `FillsCsvLogger` (`logs/fills.csv` با ستون‌های: ts, symbol, side, price, qty, notional_tmn, fee, fee_asset, is_maker, client_order_id, decision_id, position_id, strategy).
**پذیرش:**
- [ ] `pytest tests/unit/engine/test_decision_log.py -q`: هر خط با `DecisionRecord.model_validate_json` قابل خواندن؛ نوشتن هم‌زمان ۱۰۰ رکورد از چند task بدون خط خراب.

### T-004 — Docker و Makefile کامل
`مالک: A | نوع: AGENT | اولویت: P0 | وابستگی: T-001 | Milestone: M1`
**خروجی:** `Dockerfile`، `docker-compose.yml`، `Makefile` (کامل)، `.dockerignore`.
**شرح:**
- `Dockerfile`: python:3.11-slim، نصب وابستگی‌ها، کاربر غیر root، `ENTRYPOINT ["siavash"]`، `ARG GIT_SHA` → env.
- `docker-compose.yml`: سرویس `bot` با `command: run --mode ${MODE:-paper}`، `restart: unless-stopped`، `env_file: .env`، volumeهای `./logs ./state ./data ./models ./reports ./config`، healthcheck روی فایل heartbeat (T-1102).
- تارگت‌های Makefile (بعضی تا تسک مربوطه پیاده نشده‌اند و فعلاً پیام «not implemented: T-xxx» چاپ و exit 1 می‌کنند): `install lint test test-live download-data validate-data train backtest walkforward paper live report replay flatten pause resume status shadow-report export-submission dashboard docker-build docker-up docker-logs`.
- `make live` قبل از اجرا یک تأیید تعاملی بگیرد (`read -p "Type LIVE to confirm"`) — Agent هرگز آن را اجرا نمی‌کند.
**پذیرش:**
- [ ] `docker build -t siavash:test .` (اگر docker در محیط Agent نیست، بند را با یادداشت «needs human verification» علامت بزن و WAITING_HUMAN نکن)
- [ ] `make -n backtest paper report replay` (همه تارگت‌ها تعریف شده باشند)

### T-005 — فایل راهنمای Agent
`مالک: A | نوع: AGENT | اولویت: P0 | وابستگی: T-001 | Milestone: M1`
**خروجی:** `AGENTS.md`، `CLAUDE.md` (هم‌محتوا).
**شرح:** محتوای ضمیمه E را عیناً بنویس.
**پذیرش:**
- [ ] `test -f AGENTS.md && test -f CLAUDE.md && diff AGENTS.md CLAUDE.md`

### T-010 — فایل PROGRESS.md
`مالک: A | نوع: AGENT | اولویت: P0 | وابستگی: T-001 | Milestone: M1`
**خروجی:** `PROGRESS.md`، `scripts/check_progress.py`.
**شرح:** از روی ضمیمه G و جدول ۹.۴ بساز (همه تسک‌ها TODO به جز تسک‌های انجام‌شده). اسکریپت `check_progress.py`: پارس جدول، بررسی اینکه هیچ تسک DONE وابستگی غیر DONE ندارد، و چاپ «تسک‌های قابل انجام بعدی».
**پذیرش:**
- [ ] `python scripts/check_progress.py`

### T-006 — CI
`مالک: A | نوع: AGENT | اولویت: P1 | وابستگی: T-001 | Milestone: M7`
**خروجی:** `.github/workflows/ci.yml`، badge در README.
**شرح:** روی push و PR: setup python 3.11، `pip install -e ".[dev]"`، `make lint`، `make test`. کش pip.
**پذیرش:**
- [ ] فایل workflow با `python -c "import yaml;yaml.safe_load(open('.github/workflows/ci.yml'))"` معتبر باشد.

---

### E3 — ویژگی‌ها و سیگنال‌ها (روی داده مصنوعی قابل شروع)

### T-301 — کتابخانه اندیکاتورها
`مالک: B | نوع: AGENT | اولویت: P0 | وابستگی: T-001 | Milestone: M2`
**خروجی:** `src/siavash/features/indicators.py`، `tests/unit/features/test_indicators.py`.
**شرح:** توابع برداری روی `pd.Series`/`pd.DataFrame` (ستون‌های open/high/low/close/volume): `ema(s,n)`, `sma(s,n)`, `rsi(s,n=14)` (Wilder)، `atr(df,n=14)` (Wilder)، `bollinger(s,n=20,k=2)->(mid,upper,lower,width_pct)`، `adx(df,n=14)`، `macd(s,12,26,9)->(macd,signal,hist)`، `donchian(df,n=20)->(high,low)` (**بدون** کندل جاری: `shift(1)`)، `stochastic(df,14,3)`، `obv(df)`، `zscore(s,n)`، `realized_vol(close,n)`. همه فقط از گذشته استفاده کنند.
**پذیرش:**
- [ ] `pytest tests/unit/features/test_indicators.py -q`: مقایسه با مقادیر مرجع از پیش‌محاسبه‌شده (fixture CSV کوچک که Agent با یک پیاده‌سازی مستقل ساده/حلقه‌ای می‌سازد) با تلورانس 1e-6؛ تست ضد lookahead: تغییر داده بعد از t، مقدار در t را عوض نکند.

### T-303 — Feature Builder چندتایم‌فریمی
`مالک: B | نوع: AGENT | اولویت: P0 | وابستگی: T-301, T-007 | Milestone: M2`
**خروجی:** `src/siavash/features/builder.py`، `src/siavash/features/feature_set.py`، `tests/unit/features/test_builder.py`.
**شرح:**
- `feature_set.py`: ثابت `FEATURE_SET_VERSION = "fs_v1"` و لیست مرتب `FEATURES` (نام‌ها طبق قاعده ۸.۳). برای هر tf در {15m,1h,4h}: `ema20, ema50, ema200, ema20_slope, ema50_dist, ema200_dist, adx14, rsi14, atr14_pct, bb_width_pct, bb_pos (موقعیت در باند ۰..۱), macd_hist_norm, donchian_high_dist, donchian_low_dist, stoch_k, vol_z20, ret_1, ret_4, ret_16, rv20`.
- `build_features(candles: dict[Timeframe, pd.DataFrame]) -> pd.DataFrame`: ایندکس = زمان بسته شدن کندل 15m. ویژگی‌های 1h و 4h با `merge_asof` روی **زمان بسته شدن** کندل‌های بالاتر (فقط کندل‌های بسته‌شده) هم‌تراز می‌شوند.
- `latest_row(...) -> FeatureRow` برای حالت لایو (همان کد، فقط آخرین سطر).
- ویژگی‌های `x_*` (implied، بازار کل) از T-205 و T-304 با `add_cross_features(df, extra)` اضافه می‌شوند.
**پذیرش:**
- [ ] `pytest tests/unit/features/test_builder.py -q` شامل **تست ضد lookahead**: برای ۲۰ زمان تصادفی t، `build_features(data[:t]).iloc[-1] == build_features(data).loc[t]`؛ و اینکه مقدار 1h در زمانی که کندل 1h هنوز بسته نشده، از کندل قبلی است.

### T-205 — قیمت ضمنی و انحراف
`مالک: B | نوع: AGENT | اولویت: P0 | وابستگی: T-303 | Milestone: M2`
**خروجی:** `src/siavash/data/implied.py`، `tests/unit/data/test_implied.py`.
**شرح:** `implied_deviation(tmn: pd.Series, usdt: pd.Series, usdt_tmn: pd.Series, window_bars=96) -> pd.DataFrame` با ستون‌های `implied`, `dev_pct = (tmn/implied - 1)*100`, `x_implied_z` (z-score غلتان dev روی window)، `x_implied_dev_pct`. هم‌ترازی دقیق روی timestamp (inner join؛ گپ = NaN). تابع `implied_pairs(markets) -> list[tuple[tmn_symbol, usdt_symbol]]` برای baseهایی که هر دو جفت مجاز را دارند. نسخه لایو: `implied_from_books(book_tmn, book_usdt, book_usdt_tmn)` با mid.
**پذیرش:**
- [ ] `pytest tests/unit/data/test_implied.py -q`: روی داده مصنوعی T-008، وقتی BTC/TMN عمداً ۲٪ پایین‌تر شده z < −2 شود؛ ضد lookahead.

### T-304 — ویژگی‌های رژیم و رژیم‌یاب قاعده‌ای
`مالک: B | نوع: AGENT | اولویت: P0 | وابستگی: T-303 | Milestone: M2`
**خروجی:** `src/siavash/features/regime.py`، `tests/unit/features/test_regime.py`.
**شرح:**
- `market_features(universe_features: dict[symbol, DataFrame], btc_symbol="BTC/TMN") -> DataFrame` با: `x_btc_ret_1h, x_btc_ret_4h, x_btc_adx_1h, x_btc_rv_1h, x_breadth_ema50` (درصد بازارهای بالای EMA50 در 1h)، `x_avg_ret_4h`, `x_rv_percentile_7d`.
- `RuleRegimeProvider` (پیاده‌سازی `RegimeProvider`) با قواعد قطعی، به ترتیب:
  1. PANIC اگر `x_btc_ret_1h ≤ -3%` یا `x_btc_ret_4h ≤ -5%`
  2. HIGH_VOL اگر `x_rv_percentile_7d ≥ 90`
  3. TREND_UP اگر `x_btc_adx_1h ≥ 20` و `1h_ema20 > 1h_ema50` (BTC) و `x_breadth_ema50 ≥ 55`
  4. در غیر این صورت RANGE
  آستانه‌ها از `strategy.yaml > regime.rules`. `risk_multiplier` از `risk.yaml > regime_multipliers`. `enabled_strategies`: TREND_UP→[S1,S3]، RANGE→[S2,S3]، HIGH_VOL→[S3]، PANIC→[]. `source="rule"`.
**پذیرش:**
- [ ] `pytest tests/unit/features/test_regime.py -q`: یک سناریو برای هر رژیم.

### T-305 — Signal Generatorها
`مالک: B | نوع: AGENT | اولویت: P0 | وابستگی: T-304, T-205 | Milestone: M2`
**خروجی:** `src/siavash/signals/{base.py,trend.py,meanrev.py,implied_dev.py,registry.py}`، `tests/unit/signals/test_signals.py`.
**شرح:** پیاده‌سازی `SignalGenerator` برای هر استراتژی طبق جدول ۴.۳ (پارامترها از `strategy.yaml`):
- **S1 TrendBreakout:** شرط: `15m close > donchian_high(20)` (یعنی `15m_donchian_high_dist > 0`)، `1h_ema20 > 1h_ema50`، `1h_adx14 > s1.adx_min (20)`، `15m_vol_z20 > s1.vol_z_min (1.0)`. SL = entry − `s1.sl_atr (1.5)`×ATR(15m)؛ TP1 = entry + `s1.tp1_atr (2.0)`×ATR؛ take_profit = entry + `s1.tp_atr (4.0)`×ATR (سقف؛ بقیه با trailing)؛ time_stop = `s1.time_stop_min (720)`.
- **S2 MeanReversion:** `15m_bb_pos < 0` (زیر باند پایین)، `15m_rsi14 < s2.rsi_max (30)`، `1h_adx14 < s2.adx_max (20)`، `4h_ema200_dist > s2.min_4h_ema200_dist (-2%)`. TP = BB mid (فاصله از entry ثبت شود)؛ SL = entry − `s2.sl_atr (1.2)`×ATR؛ time_stop `360`.
- **S3 ImpliedDev:** `x_implied_z < -s3.z_entry (2.0)`، `15m spread_pct ≤ max_spread_pct` (اگر ویژگی موجود است)، `x_btc_ret_1h > s3.min_usdt_ret_1h (-1.5%)`. TP = min(قیمت متناظر با z=0 , entry×(1+`s3.tp_pct (1.5)`%))؛ SL = entry×(1−`s3.sl_pct (1.2)`%)؛ time_stop `180`.
- اگر استراتژی در `regime.enabled_strategies` نیست یا در `strategies.enabled` نیست → لیست خالی.
- `conditions` شامل همه مقادیر شرط‌ها (برای لاگ). `expected_move_pct` = فاصله TP (برای S1 از TP1) بر حسب درصد.
- `registry.py`: `build_generators(cfg) -> list[SignalGenerator]`.
**پذیرش:**
- [ ] `pytest tests/unit/signals/test_signals.py -q`: برای هر استراتژی یک سناریوی «باید سیگنال بدهد» و سه سناریوی «نباید»؛ رعایت `enabled_strategies`؛ `stop_loss < entry < take_profit`.

### T-306 — فیلتر کارمزد
`مالک: B | نوع: AGENT | اولویت: P0 | وابستگی: T-305 | Milestone: M2`
**خروجی:** `src/siavash/signals/fee_filter.py`، `tests/unit/signals/test_fee_filter.py`.
**شرح:** `round_trip_fee_pct(cfg) = maker_or_taker_entry + taker_exit` (بدبینانه: `2 × taker_pct` اگر maker ناشناخته). `passes_fee_filter(c, cfg) -> tuple[bool, str]`: رد اگر `expected_move_pct < fee_multiple (2.5) × round_trip_fee_pct`. کد رد: `FEE_TOO_HIGH`.
**پذیرش:**
- [ ] `pytest tests/unit/signals/test_fee_filter.py -q`

---

### E1 — اتصال به والکس

### T-101 — کشف و مستندسازی API والکس
`مالک: A | نوع: AGENT+HUMAN | اولویت: P0 | وابستگی: T-001 | Milestone: M1`
**خروجی:** `docs/wallex_api_notes.md`، `src/siavash/exchange/endpoints.py`، `tests/fixtures/wallex/*.json`.
**بخش انسان:** مستندات رسمی API والکس (لینک/PDF/HTML/Postman) و مستندات/ریپوی Wallex MCP را که برگزارکننده داده در `docs/vendor/wallex/` و `docs/vendor/wallex_mcp/` بگذارد؛ کارمزد maker/taker حساب چالش را از پنل در `docs/vendor/wallex/fees.txt` بنویسد؛ در صورت امکان چند پاسخ واقعی (بدون اطلاعات حساس) از endpointها را در `docs/vendor/wallex/samples/` بگذارد.
**شرح (Agent):**
- اگر `docs/vendor/wallex/` خالی است: `endpoints.py` را از روی ضمیمه H بساز، بالای فایل `VERIFIED = False` بگذار، fixtureها را بر اساس ساختار ضمیمه H بساز، و تسک را با وضعیت `DONE (UNVERIFIED)` + WAITING_HUMAN برای تأیید ثبت کن. بقیه تسک‌های E1 روی mock ادامه پیدا می‌کنند.
- اگر مستندات هست: از آن استخراج کن و `VERIFIED = True`.
- `docs/wallex_api_notes.md` شامل جدولی برای هر endpoint: متد، مسیر، پارامترها، هدرها، نمونه پاسخ، کدهای خطا، rate limit؛ و بخش‌های: احراز هویت، فرمت نماد، دقت قیمت/مقدار و حداقل سفارش، انواع سفارش (آیا stop اسپات وجود دارد؟)، محدودیت طول `client_order_id` (آیا اصلاً پشتیبانی می‌شود؟ اگر نه، راه‌حل: نگهداری نگاشت `order_id ↔ client_order_id` در state)، WebSocket (اگر هست)، کارمزدها، ابزارهای MCP و نحوه اجرای سرور MCP.
- `endpoints.py`: یک دیکشنری/کلاس ثابت از همه مسیرها و نام فیلدها + توابع parse (`parse_market`, `parse_candles`, `parse_orderbook`, `parse_order`, `parse_balance`, `parse_fill`, `parse_error`) که پاسخ خام را به مدل‌های `core.models` تبدیل می‌کنند. **هیچ مسیر دیگری در کد پروژه نباید مسیر API را بداند.**
**پذیرش:**
- [ ] `pytest tests/unit/exchange/test_endpoints_parse.py -q` (همه parseها روی fixtureها)
- [ ] `grep -rn "api.wallex" src/ | grep -v endpoints.py` خروجی خالی (فقط endpoints.py آدرس دارد)

### T-102 — کلاینت HTTP پایه
`مالک: A | نوع: AGENT | اولویت: P0 | وابستگی: T-101, T-003 | Milestone: M1`
**خروجی:** `src/siavash/exchange/{client.py,rate_limit.py,errors.py}`، `tests/unit/exchange/test_client.py`.
**شرح:**
- `WallexHttp(base_url, api_key, limits, timeout=10)`: httpx.AsyncClient؛ هدر احراز هویت طبق endpoints.py؛ proxy از env فقط اگر `exchange.use_proxy=true`.
- `rate_limit.py`: `TokenBucket(rate_per_sec, burst)` جدا برای public و private (مقادیر از config، پیش‌فرض محافظه‌کار: public 5/s، private 2/s اگر T-101 مقداری نداد).
- retry با tenacity: فقط برای خطای شبکه، timeout، 5xx و 429 (با احترام به `Retry-After`)؛ حداکثر ۴ تلاش؛ backoff نمایی 0.5s..8s با jitter. **برای POST ثبت سفارش retry خودکار ممنوع** (idempotency در T-802 انجام می‌شود).
- `errors.py`: `WallexError` و زیرکلاس‌ها: `AuthError, RateLimited, InsufficientBalance, InvalidPrecision, BelowMinimum, MarketClosed, OrderNotFound, ServerError, NetworkError` — نگاشت با `parse_error`.
- اندازه‌گیری latency هر درخواست و انتشار در لاگ (`event="http_request", path, status, ms`) + شمارنده خطا برای breaker (اینترفیس `ErrorCounter` با `record(ts)` و `count(window_s)`).
**پذیرش:**
- [ ] `pytest tests/unit/exchange/test_client.py -q` (با respx): retry روی 503 و موفقیت در تلاش سوم؛ عدم retry روی 400؛ عدم retry POST سفارش؛ رعایت rate limit (زمان‌سنجی با FakeClock یا monkeypatch)؛ نگاشت خطاها؛ عدم حضور کلید در لاگ.

### T-103 — متادیتای بازار و گرد کردن
`مالک: A | نوع: AGENT | اولویت: P0 | وابستگی: T-102 | Milestone: M1`
**خروجی:** `src/siavash/exchange/markets.py`، `tests/unit/exchange/test_markets.py`.
**شرح:** `MarketRegistry`: بارگذاری از API و کش (refresh هر ۶ ساعت)؛ `get(symbol) -> MarketInfo`؛ توابع: `round_price(symbol, price, side)` (خرید رو به پایین، فروش رو به بالا روی tick)، `round_qty(symbol, qty)` (همیشه رو به پایین)، `check_min(symbol, qty, price) -> bool`، `max_qty_for_notional(symbol, tmn, price)`.
**پذیرش:**
- [ ] `pytest tests/unit/exchange/test_markets.py -q` (hypothesis: خروجی گرد شده همیشه مضرب tick/step و هرگز بیشتر از ورودی برای qty).

### T-104 — endpointهای داده بازار
`مالک: A | نوع: AGENT | اولویت: P0 | وابستگی: T-103 | Milestone: M1`
**خروجی:** `src/siavash/exchange/market_data.py`، `tests/unit/exchange/test_market_data.py`.
**شرح:** `WallexMarketData` پیاده‌سازی `MarketData`. `get_candles` با صفحه‌بندی خودکار (اگر API بازه را محدود می‌کند، بازه را تکه‌تکه درخواست و ادغام کن، تکراری‌ها حذف، مرتب بر اساس زمان)؛ تبدیل resolutionها طبق endpoints.py.
**پذیرش:**
- [ ] `pytest tests/unit/exchange/test_market_data.py -q` با fixtureها و respx؛ صفحه‌بندی ۳ تکه‌ای درست ادغام شود.


### T-105 — endpointهای حساب و سفارش
`مالک: A | نوع: AGENT | اولویت: P0 | وابستگی: T-103 | Milestone: M1`
**خروجی:** `src/siavash/exchange/account.py`، `tests/unit/exchange/test_account.py`، `tests/unit/test_no_forbidden_paths.py`.
**شرح:** `WallexAccount` با متدهای: `get_balances`, `place_order(req)`, `cancel_order(symbol, client_order_id|order_id)`, `get_order`, `get_open_orders`, `get_my_trades(since)`. قبل از ارسال: گرد کردن با MarketRegistry و بررسی min. اگر API `client_order_id` پشتیبانی نمی‌کند، نگاشت را در `OrderIdMap` (SQLite در `state/`) نگه دار.
`get_my_trades(since)` endpoint مستقل ندارد: آن را از `GET /v1/account/orders` (صفحه‌بندی‌شده با `per_page=100`، فیلتر `from`) و `fills[]` هر سفارش بساز؛ برای سفارش‌های باز و اخیر از `GET /v1/account/orders/{client_id}` استفاده کن. چون fill شناسه یکتا ندارد، dedupe با (clientOrderId, timestamp, price, quantity) انجام شود و `fill_id` داخلی = sha1 همین چهار فیلد. موجودی: `free = value − locked`.
**پذیرش:**
- [ ] `pytest tests/unit/exchange/test_account.py -q` (شامل تست ساخت fill از `fills[]` و dedupe در دو بار فراخوانی پیاپی)
- [ ] `pytest tests/unit/test_no_forbidden_paths.py -q` — تستی که در `src/` به دنبال الگوهای `futures|margin|leverage|withdraw|transfer|deposit|iban|card-numbers|wallets` (case-insensitive) بگردد و در صورت یافتن fail شود. **استثناها:** کامنت‌های مستندسازی در `guard.py`، و نام فیلدهای پاسخ بازارها در `endpoints.py` (`is_margin`, `leverage_step`, `max_leverage`) که فقط برای **رد کردن** بازارهای غیر اسپات خوانده می‌شوند.

### T-106 — SymbolGuard
`مالک: A | نوع: AGENT | اولویت: P0 | وابستگی: T-105 | Milestone: M1`
**خروجی:** `src/siavash/exchange/guard.py`، `config/denylist.txt` (ضمیمه A)، `tests/unit/exchange/test_guard.py`.
**شرح:** `SymbolGuard(denylist_path, registry, allowed_quotes=["TMN"])`؛ `is_tradable(symbol) -> tuple[bool, str]` و `assert_tradable(symbol)` (raise `SymbolNotAllowed`). شرایط: در denylist نباشد (مقایسه بعد از نرمال‌سازی به canonical)، در registry باشد، `is_active` و `is_spot`، quote در `allowed_quotes` (برای تبدیل USDT در endgame، `allow_usdt_liquidation=True` فقط سفارش SELL روی `USDT/TMN` را مجاز کند). فراخوانی اجباری داخل `WallexAccount.place_order` **و** داخل `PaperBroker`/`BacktestBroker` (T-1001/T-902). لاگ هشدار برای هر رد.
**پذیرش:**
- [ ] `pytest tests/unit/exchange/test_guard.py -q`: `TON/TMN`, `NEAR/TMN`, `YFI/USDT`, `1BBABYDOGE/TMN` رد؛ `BTC/TMN`, `ETH/TMN` (اگر در registry fixture فعال‌اند) مجاز؛ `BTCTMN` نرمال شود؛ تعداد نمادهای denylist = ۳۴۵ (برابر ضمیمه A).
- [ ] `pytest tests/unit/exchange/test_account.py -q -k guard` — place_order روی بازار ممنوع قبل از هر درخواست HTTP خطا بدهد.

### T-109 — بررسی شبکه و محل اجرا
`مالک: A | نوع: HUMAN | اولویت: P0 | وابستگی: — | Milestone: M1`
**شرح (انسان):** VPS نهایی را انتخاب کنید. از روی همان سرور بررسی کنید: (۱) دسترسی به API والکس (`curl` به endpoint بازارها)، (۲) دسترسی به API مدل LLM (مستقیم یا با proxy)، (۳) latency هر دو. اگر هیچ سروری هر دو را ندارد: ربات روی سرور داخلی + `HTTPS_PROXY` فقط برای LLM (`llm.use_proxy=true`, `exchange.use_proxy=false`). نتیجه را در `docs/RUNBOOK.md` بخش «شبکه» بنویسید (Agent در T-1105 آن را کامل می‌کند).
**پذیرش:** هر دو مسیر از سرور نهایی کار کنند.

### T-108 — Smoke test حساب واقعی
`مالک: A | نوع: AGENT+HUMAN | اولویت: P0 | وابستگی: T-106, T-605, T-109 | Milestone: M1`
**خروجی:** `scripts/smoke_test.py`، `tests/integration/test_wallex_live.py`.
**شرح (Agent):** اسکریپتی که: موجودی را بخواند؛ markets/ticker/orderbook/candles را برای `BTC/TMN` بگیرد؛ (فقط با فلگ `--place-test-order`) یک LIMIT BUY با حداقل مقدار و قیمت ۲۰٪ زیر best bid ثبت، وضعیتش را بخواند، و **فوراً** لغو کند؛ همه از طریق `WallexAccount` (یعنی SymbolGuard)، با `purpose="smoke_test"` و یک DecisionRecord از نوع `OPERATOR_COMMAND`؛ گزارش latency. تست‌های integration با `@pytest.mark.live`.
**بخش انسان:** اجرا با `python scripts/smoke_test.py` و بعد (اگر قوانین چالش اجازه می‌دهد) با `--place-test-order`. **Agent اجرا نمی‌کند.**
**پذیرش (Agent):**
- [ ] `python scripts/smoke_test.py --help`
- [ ] `pytest tests/integration -m live --collect-only -q`
**پذیرش (انسان):** اجرا موفق؛ سپس `VERIFIED=True` در endpoints.py (اگر قبلاً نبود) با یادداشت.

### T-107 — یکپارچه‌سازی Wallex MCP
`مالک: B | نوع: AGENT | اولویت: P1 | وابستگی: T-104, T-501 | Milestone: M4`
**خروجی:** `src/siavash/agents/tools.py`، `src/siavash/exchange/mcp_tools.py`، `tests/unit/agents/test_tools.py`، بخش «MCP» در `docs/wallex_api_notes.md`.
**شرح:**
- `agents/tools.py`: مجموعه ابزارهای **فقط‌خواندنی** برای Agentها با اسکیمای function-calling (JSON schema): `get_ticker(symbol)`, `get_orderbook_summary(symbol)`, `get_candles_summary(symbol, tf, n)`, `get_recent_trades_summary(symbol)`, `get_portfolio()`, `get_strategy_stats(strategy, hours)`. هر ابزار خروجی خلاصه و کوتاه (≤ ۱۵۰۰ کاراکتر) می‌دهد.
- `exchange/mcp_tools.py`: اگر سرور Wallex MCP در `docs/vendor/wallex_mcp/` تعریف شده، با SDK پایتون MCP به آن (stdio یا HTTP طبق مستند) وصل شو و ابزارهای خواندنی آن را به همان اسکیمای بالا نگاشت کن (`backend="mcp"`). در غیر این صورت `backend="native"` (روی `WallexMarketData`). انتخاب با `llm.tools_backend: auto|mcp|native`.
- **ابزارهای ثبت/لغو سفارش MCP هرگز به LLM داده نمی‌شوند** — فهرست سفید صریح.
- هر فراخوانی ابزار در `logs/llm/tool_calls.jsonl` ثبت شود.
**پذیرش:**
- [ ] `pytest tests/unit/agents/test_tools.py -q`: فهرست سفید؛ تلاش برای ابزار ناشناخته/سفارش → خطا؛ کوتاه بودن خروجی.

---

### E2 — داده

### T-201 — دانلودر داده تاریخی
`مالک: B | نوع: AGENT+HUMAN | اولویت: P0 | وابستگی: T-104 | Milestone: M3`
**خروجی:** `src/siavash/data/history.py`، `scripts/download_data.py`، `tests/unit/data/test_history.py`.
**شرح (Agent):** `download(symbols, tfs=[15m,1h], days=120, out="data/raw")` → `data/raw/{BASE}_{QUOTE}_{tf}.parquet` (ستون‌ها: ts_open, open, high, low, close, volume). Resume: اگر فایل هست از آخرین ts ادامه بده. لیست symbols پیش‌فرض: `strategy.yaml > universe.candidates` (همه /TMN مجاز + /USDT متناظر + `USDT/TMN`). گزارش پوشش: `reports/data_coverage.md` (بازه، تعداد کندل، درصد گپ هر فایل). منبع کمکی (اختیاری، فلگ `--aux-source`): اگر والکس کمتر از ۶۰ روز داد، loader برای فایل‌های عمومی klines بایننس (zip/CSV) که انسان در `data/aux/` می‌گذارد؛ ذخیره در `data/raw/aux/` و علامت `source` در metadata.
**بخش انسان:** اجرای `make download-data` روی سرور با کلید/دسترسی.
**پذیرش (Agent):**
- [ ] `pytest tests/unit/data/test_history.py -q` (با mock: صفحه‌بندی، resume، نوشتن parquet)
- [ ] `python scripts/download_data.py --help`

### T-202 — اعتبارسنجی و پاک‌سازی داده
`مالک: B | نوع: AGENT | اولویت: P0 | وابستگی: T-201 | Milestone: M3`
**خروجی:** `src/siavash/data/clean.py`، `scripts/validate_data.py`، `tests/unit/data/test_clean.py`.
**شرح:** تشخیص: کندل تکراری (حذف)، ts خارج از شبکه زمانی (اصلاح/حذف)، OHLC نامعتبر (`high<max(open,close)` و...)، قیمت صفر، پرش > ۲۰٪ در یک کندل 15m (علامت outlier)، گپ. گپ ≤ ۳ کندل: forward-fill با `is_filled=True` و volume=0؛ گپ بزرگ‌تر: NaN باقی می‌ماند و بازه در `reports/data_quality.md` ثبت. خروجی `data/processed/`.
**پذیرش:**
- [ ] `pytest tests/unit/data/test_clean.py -q` با داده خراب ساختگی.

### T-203 — سرویس داده لایو
`مالک: A | نوع: AGENT | اولویت: P0 | وابستگی: T-104, T-008 | Milestone: M2`
**خروجی:** `src/siavash/data/live.py`، `tests/unit/data/test_live.py`.
**شرح:** `LiveDataService(market_data: MarketData, clock: Clock, symbols, tfs)`:
- بافر حلقوی آخرین `buffer_bars (500)` کندل برای هر (symbol, tf)؛ bootstrap اولیه از `get_candles`.
- polling هر `poll_seconds (20)`؛ تشخیص بسته شدن کندل (وقتی `now ≥ ts_open + tf`) و انتشار رویداد `on_candle_close(symbol, tf, candle)` (callback list یا `asyncio.Queue`).
- snapshot دفتر سفارش هر `book_poll_seconds (15)` برای بازارهای فعال و بازارهای دارای پوزیشن.
- `last_update_ts(symbol)`؛ `is_stale(symbol, max_age_s=180)`.
- ذخیره هر کندل بسته‌شده و snapshot دفتر در `data/live/{date}/...` (parquet/jsonl) برای replay.
- قابل اجرا با `InMemoryMarketData` + `FakeClock` (برای تست و backtest/replay).
**پذیرش:**
- [ ] `pytest tests/unit/data/test_live.py -q`: با FakeClock، ۴ کندل 15m پیاپی رویداد بدهند؛ هیچ رویداد تکراری؛ تشخیص stale.

### T-204 — انتخاب Universe
`مالک: B | نوع: AGENT | اولویت: P0 | وابستگی: T-203, T-106 | Milestone: M2`
**خروجی:** `src/siavash/data/universe.py`، `tests/unit/data/test_universe.py`.
**شرح:** `select_universe(markets, tickers, books, guard, open_position_symbols, cfg) -> (selected: list[str], report: dict[symbol, reason])` طبق ۴.۱: فقط tradable (guard)، رتبه‌بندی بر اساس `volume_24h_quote`، `spread_pct ≤ max_spread_pct`، عمق ±0.5٪ ≥ `depth_multiple (5)` × `max_position_tmn`، حداکثر `max_markets (8)`. همیشه `BTC/TMN` و `USDT/TMN` را برای داده (نه لزوماً معامله) نگه دار. بازارهای دارای پوزیشن باز حذف نشوند. خروجی یک DecisionRecord نوع `UNIVERSE_UPDATE`.
**پذیرش:**
- [ ] `pytest tests/unit/data/test_universe.py -q`

### T-302 — ویژگی‌های دفتر سفارش
`مالک: B | نوع: AGENT | اولویت: P1 | وابستگی: T-303 | Milestone: M2`
**خروجی:** `src/siavash/features/orderbook.py`، `tests/unit/features/test_orderbook.py`.
**شرح:** از `OrderBook`: `ob_spread_pct`, `ob_imbalance_1`, `ob_imbalance_5`, `ob_imbalance_10` ((bid_qty−ask_qty)/(bid_qty+ask_qty))، `ob_depth_tmn_05` (عمق تومانی ±0.5٪)، `ob_microprice_dist_pct`. در بک‌تست (نبود دفتر سفارش تاریخی) این ویژگی‌ها NaN و مدل ML نسخه بدون آن‌ها آموزش ببیند (ویژگی‌های `ob_*` از `FEATURES` مدل حذف، ولی در لاگ لایو ثبت شوند).
**پذیرش:**
- [ ] `pytest tests/unit/features/test_orderbook.py -q`

---

### E8 — اجرا (Broker و سفارش‌ها)

### T-806 — LiveBroker و تست قراردادی Broker
`مالک: A | نوع: AGENT | اولویت: P0 | وابستگی: T-106, T-008 | Milestone: M2`
**خروجی:** `src/siavash/execution/live_broker.py`، `tests/unit/execution/broker_contract.py`، `tests/unit/execution/test_live_broker.py`.
**شرح:** `LiveBroker(account: WallexAccount, orders_logger)` پیاده‌سازی `Broker` (لایه نازک روی T-105 + لاگ orders.jsonl + assert اینکه `req.decision_id` خالی نیست). `broker_contract.py`: مجموعه تست مشترک (تابع `run_broker_contract(broker_factory)`) که هر Broker باید پاس کند: ثبت/لغو/استعلام، idempotency با client_order_id تکراری، رد بازار ممنوع، رد بدون decision_id، موجودی بعد از fill، کارمزد.
**پذیرش:**
- [ ] `pytest tests/unit/execution/test_live_broker.py -q` (LiveBroker روی respx + FakeBroker هر دو از contract عبور کنند)

### T-1001 — PaperBroker (شبیه‌سازی با دفتر سفارش واقعی)
`مالک: A | نوع: AGENT | اولویت: P0 | وابستگی: T-806, T-203 | Milestone: M2`
**خروجی:** `src/siavash/execution/paper_broker.py`، `tests/unit/execution/test_paper_broker.py`.
**شرح:** `PaperBroker(live_data: LiveDataService, market_data, guard, fee_cfg, initial_balances={"TMN": 5_000_000})`:
- سفارش‌ها هرگز به صرافی نمی‌روند.
- MARKET: پر شدن با پیمایش دفتر سفارش واقعی لحظه (VWAP سطوح) + کارمزد taker.
- LIMIT خرید: پر شدن وقتی در `get_recent_trades` معامله‌ای با قیمت **کمتر از** قیمت سفارش رخ دهد (یا best ask ≤ قیمت → taker فوری)؛ مقدار پرشده حداکثر تا حجم آن معاملات (partial). فروش متقارن. کارمزد maker.
- task پس‌زمینه برای بررسی سفارش‌های باز هر `paper.fill_check_seconds (5)`.
- state در `state/paper.sqlite` تا با ری‌استارت حفظ شود.
- `SymbolGuard` اجباری.
**پذیرش:**
- [ ] `pytest tests/unit/execution/test_paper_broker.py -q` (+ contract)

### T-801 — Order Router
`مالک: A | نوع: AGENT | اولویت: P0 | وابستگی: T-806, T-103 | Milestone: M2`
**خروجی:** `src/siavash/execution/router.py`، `tests/unit/execution/test_router.py`.
**شرح:** `OrderRouter(broker, registry, clock, cfg)`:
- `async enter(position, qty, book, still_valid: Callable[[], Awaitable[bool]]) -> list[Order]`: LIMIT BUY روی best bid (اگر `best_ask - best_bid > tick` آن را یک tick بهتر کن ولی کمتر از ask بماند) → انتظار `entry_timeout_s (90)` → اگر پر نشد/ناقص و `still_valid()`: لغو باقی‌مانده و LIMIT تهاجمی با قیمت `best_ask × (1 + max_slippage_pct (0.15)%)`، انتظار `aggressive_timeout_s (30)` → لغو باقی‌مانده.
- `async exit(position, qty, reason, book)`: برای STOP_LOSS/BREAKER/ENDGAME/OPERATOR: LIMIT تهاجمی `best_bid × (1 − exit_max_slippage_pct (0.3)%)` و تکرار تا ۳ بار با به‌روزرسانی قیمت؛ برای TAKE_PROFIT/TP1: LIMIT روی best ask یا قیمت TP (هر کدام بالاتر) با timeout و fallback تهاجمی.
- همه client_order_idها از `ids.client_order_id`؛ ثبت لغزش (`fill_price` vs `ref_price`) در لاگ.
**پذیرش:**
- [ ] `pytest tests/unit/execution/test_router.py -q` با FakeBroker/FakeClock: مسیر پر شدن maker، مسیر fallback تهاجمی، مسیر لغو وقتی سیگنال نامعتبر شد، exit سه‌مرحله‌ای.

### T-802 — Idempotency و بازیابی سفارش
`مالک: A | نوع: AGENT | اولویت: P0 | وابستگی: T-801 | Milestone: M2`
**خروجی:** `src/siavash/execution/safe_submit.py`، `tests/unit/execution/test_safe_submit.py`.
**شرح:** `safe_place(broker, req)`: ارسال؛ اگر `NetworkError`/timeout: تا ۳ بار با فاصله ۲ ثانیه `get_order(symbol, client_order_id)` (یا جست‌وجو در open orders/my trades اگر API استعلام با client id ندارد)؛ اگر یافت شد همان را برگردان؛ اگر یافت نشد بعد از ۳ استعلام، با `attempt+1` (client id جدید) دوباره ارسال کن. هرگز دو سفارش فعال برای یک leg.
**پذیرش:**
- [ ] `pytest tests/unit/execution/test_safe_submit.py -q`: سناریوی «timeout ولی سفارش در صرافی ثبت شده» → سفارش تکراری ساخته نشود.

### T-803 — رهگیری سفارش و fillها
`مالک: A | نوع: AGENT | اولویت: P0 | وابستگی: T-802, T-605 | Milestone: M2`
**خروجی:** `src/siavash/execution/tracker.py`، `tests/unit/execution/test_tracker.py`.
**شرح:** `OrderTracker`: هر `order_poll_seconds (5)` وضعیت سفارش‌های باز ربات را به‌روز کند؛ fillهای جدید (`get_fills(since)`) را dedupe (با fill_id) و در `fills.csv` بنویسد؛ رویداد `on_fill(fill)` و `on_order_terminal(order)` منتشر کند؛ کارمزد را به تومان تبدیل کند (اگر fee_asset کوین است، با قیمت fill).
**پذیرش:**
- [ ] `pytest tests/unit/execution/test_tracker.py -q`

### T-603 — Position Manager
`مالک: A | نوع: AGENT | اولویت: P0 | وابستگی: T-803 | Milestone: M2`
**خروجی:** `src/siavash/engine/positions.py`، `tests/unit/engine/test_positions.py`.
**شرح:** `PositionManager(store)`: ساخت Position از Candidate+RiskDecision (`PENDING_ENTRY`)، اعمال fill ورود (به‌روزرسانی qty و avg_entry → `OPEN` با اولین fill؛ `opened_at`)، لغو کامل بدون fill → `CANCELLED`، شروع خروج → `PENDING_EXIT`، fill خروج (کاهش qty، محاسبه `realized_pnl_tmn` و `fees_tmn`)؛ qty≤min_qty → `CLOSED`. انتقال‌های غیرمجاز → Exception. `open_positions()`, `exposure_tmn(prices)`. ذخیره هر تغییر در store (T-1101؛ تا آن موقع یک `InMemoryStore` با همان اینترفیس).
**پذیرش:**
- [ ] `pytest tests/unit/engine/test_positions.py -q`: همه انتقال‌ها، partial fill، PnL با کارمزد، رد انتقال غیرمجاز.

### T-604 — منطق خروج
`مالک: A | نوع: AGENT | اولویت: P0 | وابستگی: T-603 | Milestone: M2`
**خروجی:** `src/siavash/engine/exits.py`، `tests/unit/engine/test_exits.py`.
**شرح:** تابع خالص `evaluate_exit(position, price_bid, now, breaker_state, endgame_phase, regime, cfg) -> ExitDecision | None` (ExitDecision: reason, qty_fraction, ref_price) با اولویت: ENDGAME/BREAKER(FLATTEN) → STOP_LOSS (`bid ≤ stop_loss` یا `bid ≤ trailing_stop`) → TP1 (فقط S1: `bid ≥ tp1_price` و `not tp1_done` → fraction `s1.tp1_fraction (0.5)` و انتقال SL به breakeven + کارمزد) → TAKE_PROFIT (`bid ≥ take_profit`) → TIME_STOP (`now ≥ time_stop_at`) → SIGNAL (رژیم PANIC و پوزیشن در سود کمتر از `panic_exit_min_profit_pct`). به‌روزرسانی trailing (فقط بعد از TP1 برای S1؛ `highest_price − s1.trail_atr × ATR`، فقط رو به بالا) در تابع جدا `update_trailing`.
**پذیرش:**
- [ ] `pytest tests/unit/engine/test_exits.py -q`: یک سناریو برای هر دلیل + تست اولویت (SL و TP هم‌زمان → SL).

### T-1101 — State ماندگار
`مالک: A | نوع: AGENT | اولویت: P0 | وابستگی: T-603 | Milestone: M2`
**خروجی:** `src/siavash/engine/store.py`، `tests/unit/engine/test_store.py`.
**شرح:** `SqliteStore(path="state/state.sqlite")` با WAL؛ جداول: positions (JSON)، orders، id_map، breaker (state, reason, since)، kv (peak_equity، شمارنده‌ها، last_regime، cooldownها)، equity (ts, equity). متدهای async. `InMemoryStore` با همان اینترفیس برای تست/بک‌تست.
**پذیرش:**
- [ ] `pytest tests/unit/engine/test_store.py -q`: نوشتن، بستن، باز کردن مجدد و بازیابی کامل.

---

### E7 — مدیریت ریسک

### T-701 — اندازه پوزیشن
`مالک: A | نوع: AGENT | اولویت: P0 | وابستگی: T-103, T-007 | Milestone: M2`
**خروجی:** `src/siavash/risk/sizing.py`، `tests/unit/risk/test_sizing.py`.
**شرح:** `position_size_tmn(equity, entry, stop, free_exposure_tmn, cash_tmn, regime_mult, llm_mult, cfg) -> Decimal` طبق فرمول بخش ۵: `min(risk_per_trade_pct% × equity / sl_pct, max_position_pct% × equity, free_exposure, cash × 0.98) × regime_mult × llm_mult × initial_live_risk_scale` (اگر mode=live)؛ سپس تبدیل به qty و گرد کردن؛ زیر min_notional → 0.
**پذیرش:**
- [ ] `pytest tests/unit/risk/test_sizing.py -q` + property-based (hypothesis): هرگز > ۲۰٪ equity، هرگز > cash، هرگز منفی.

### T-702 — چک‌های پیش از معامله و RiskManager
`مالک: A | نوع: AGENT | اولویت: P0 | وابستگی: T-701, T-106 | Milestone: M2`
**خروجی:** `src/siavash/risk/manager.py`، `src/siavash/risk/limits.py`، `tests/unit/risk/test_manager.py`.
**شرح:** `DefaultRiskManager` پیاده‌سازی `RiskManager`. ترتیب چک‌ها و کد رد (از `risk/codes.py`): SymbolGuard → breaker ≠ NORMAL → endgame phase → regime PANIC → max concurrent → بازار تکراری → cooldown ضرر پیاپی → cooldown بازار → سقف معامله ساعت/روز → spread → stale data → سفارش مخالف باز روی همان بازار → محاسبه اندازه (T-701) → exposure/cash/min_notional. همه چک‌های شکست‌خورده جمع‌آوری شوند (نه فقط اولی) ولی approved=false با اولین.
**پذیرش:**
- [ ] `pytest tests/unit/risk/test_manager.py -q`: یک تست برای هر کد رد + یک مسیر موفق.

### T-1202 — ردیاب Equity و Drawdown
`مالک: A | نوع: AGENT | اولویت: P0 | وابستگی: T-1101, T-203 | Milestone: M2`
**خروجی:** `src/siavash/reporting/equity.py`، `tests/unit/reporting/test_equity.py`.
**شرح:** `EquityTracker(broker, live_data, store, clock)`: هر `equity_interval_s (300)` (و بعد از هر fill): `equity = TMN.total + Σ(asset.total × best_bid(asset/TMN))` (برای USDT از `USDT/TMN`؛ کوینی بدون بازار TMN با مسیر USDT)؛ peak، drawdown_pct، daily_pnl_pct (روز = 00:00 تهران؛ از config `timezone: Asia/Tehran`)؛ نوشتن در `logs/equity.csv` و store؛ ساخت `PortfolioSnapshot`.
**پذیرش:**
- [ ] `pytest tests/unit/reporting/test_equity.py -q`

### T-703 — Circuit Breakerها
`مالک: A | نوع: AGENT | اولویت: P0 | وابستگی: T-1202, T-102 | Milestone: M2`
**خروجی:** `src/siavash/risk/breakers.py`، `tests/unit/risk/test_breakers.py`.
**شرح:** `BreakerController(store, alerts, cfg)`: ورودی‌ها هر حلقه: snapshot، ErrorCounter، staleness. قواعد: daily_pnl ≤ −daily_loss_limit → `NO_NEW_ENTRIES` تا روز بعد؛ drawdown ≥ kill_switch → `FLATTEN_AND_HALT` (ماندگار؛ فقط `siavash resume --confirm` آن را برمی‌گرداند)؛ خطای API ≥ threshold در پنجره → `NO_NEW_ENTRIES` تا ۱۵ دقیقه بدون خطا؛ داده stale → `NO_NEW_ENTRIES` موقت. هر تغییر state → DecisionRecord نوع `BREAKER` + هشدار. حالت شدیدتر همیشه برنده.
**پذیرش:**
- [ ] `pytest tests/unit/risk/test_breakers.py -q`: هر سناریو + ماندگاری پس از ری‌استارت.

### T-704 — Endgame Liquidator
`مالک: A | نوع: AGENT | اولویت: P0 | وابستگی: T-801, T-1202 | Milestone: M6`
**خروجی:** `src/siavash/risk/endgame.py`، `tests/unit/risk/test_endgame.py`.
**شرح:** `endgame_phase(now, cfg) -> Literal["NONE","NO_NEW","UNWIND","FORCE","DONE"]` بر اساس `endgame.competition_end_utc` و ساعت‌های ضمیمه B. `EndgameLiquidator.run_step()`: در UNWIND: خروج پوزیشن‌ها با reason=ENDGAME (مسیر TP-مانند با timeout کوتاه)؛ در FORCE: خروج تهاجمی همه + فروش هر موجودی کوینی که پوزیشن ندارد (اگر ≥ min_qty) + فروش USDT به TMN؛ گزارش نهایی `reports/endgame.md`: موجودی‌ها، ارزش نهایی تومانی، دارایی‌های ریز غیرقابل فروش، بازدهی با فرمول رسمی. اگر `competition_end_utc` خالی است، ربات در حالت live **شروع نشود** (خطای config).
**پذیرش:**
- [ ] `pytest tests/unit/risk/test_endgame.py -q` با FakeClock و FakeBroker: در پایان، موجودی همه دارایی‌ها جز TMN < min_qty.

### T-705 — تست‌های جامع ریسک
`مالک: A | نوع: AGENT | اولویت: P0 | وابستگی: T-702, T-703, T-704 | Milestone: M2`
**خروجی:** `tests/unit/risk/test_risk_properties.py`.
**شرح:** hypothesis روی دنباله‌های تصادفی از کاندیدها، قیمت‌ها و fillها با FakeBroker: ثابت‌ها (invariants): exposure کل ≤ ۵۰٪ equity، هیچ پوزیشن > ۲۰٪، حداکثر ۳ پوزیشن، هیچ ورود در breaker≠NORMAL، هیچ سفارش بدون decision_id، هیچ سفارش روی بازار ممنوع.
**پذیرش:**
- [ ] `pytest tests/unit/risk/test_risk_properties.py -q`

---

### E5 — Agentهای LLM

### T-501 — لایه انتزاعی LLM
`مالک: B | نوع: AGENT | اولویت: P0 | وابستگی: T-002, T-003 | Milestone: M4`
**خروجی:** `src/siavash/agents/llm.py`، `src/siavash/agents/budget.py`، `tests/unit/agents/test_llm.py`، `tests/unit/agents/test_budget.py`، دستور `siavash llm-check` در `src/siavash/cli.py`.
**شرح:** `LLMClient(cfg)` روی کتابخانه `openai` با `base_url` قابل تنظیم (OpenAI-compatible)؛ proxy اختیاری؛ `temperature=0`؛ `timeout_s (20)`؛ ۲ retry روی خطای شبکه/5xx؛ مدل fallback. متد `async complete_json(system, user, schema: type[BaseModel], tools=None, max_tool_calls=3) -> (parsed, raw_meta)`: حلقه function-calling (اجرای ابزار از T-107 و برگرداندن نتیجه)، سپس پارس JSON و اعتبارسنجی با pydantic. ثبت هر فراخوانی در `logs/llm/calls.jsonl` (model, prompt_version, input_hash, prompt_tokens, completion_tokens, latency, status) — بدون کلید.
**ارائه‌دهنده LLM (قطعی):** BoxAPI — سازگار با OpenAI، `base_url = https://ai.boxapi.ir/ai/v1`، فقط `/chat/completions`، بدون proxy (`llm.use_proxy=false`). مدل‌ها از env (`LLM_MODEL`, `LLM_FALLBACK_MODEL`؛ پیش‌فرض `gpt-5.4-mini` و `gpt-5-mini`). **کل بودجه فقط ۳ دلار است.**
- **Budget guard (`budget.py`):** config جدید `llm.budget: {max_calls_per_hour: 20, max_calls_per_day: 200, max_tokens_per_day: 400000, max_input_tokens_per_call: 3000}`. قبل از هر فراخوانی بررسی شود؛ ورودی بزرگ‌تر از `max_input_tokens_per_call` (تخمین با `len(text)/3`) کوتاه شود یا رد شود. با رسیدن به هر سقف: `BudgetExceeded` → reviewer و analyst به fallback قطعی بروند (`status=FALLBACK`) + هشدار. شمارنده‌ها (ساعتی/روزانه، روز = Asia/Tehran) در store ماندگار باشند. پاسخ‌هایی که از کش (T-502) می‌آیند در بودجه حساب نشوند.
- **Capability probe:** دستور `siavash llm-check` با دو درخواست کوچک، پشتیبانی BoxAPI از function calling (`tools`) و `response_format={"type":"json_object"}` را تست کند و نتیجه را در `state/llm_capabilities.json` ذخیره کند. `LLMClient` در شروع این فایل را بخواند: اگر tools پشتیبانی نمی‌شود، `complete_json` بدون tools کار کند (ابزارها نادیده و در لاگ ثبت شود)؛ اگر JSON mode پشتیبانی نمی‌شود، از دستور «فقط JSON معتبر برگردان» در پرامپت + استخراج اولین بلوک JSON از متن + اعتبارسنجی pydantic استفاده کند.
- پرامپت‌ها فشرده باشند (فقط ویژگی‌های خلاصه، بدون داده خام کندل).
**پذیرش:**
- [ ] `pytest tests/unit/agents/test_llm.py -q` با mock HTTP (respx) برای: پاسخ معتبر، JSON خراب، timeout، یک دور tool call، حالت بدون tools، حالت بدون JSON mode (استخراج JSON از متن).
- [ ] `pytest tests/unit/agents/test_budget.py -q`: رسیدن به هر سقف → fallback؛ ماندگاری شمارنده‌ها پس از ری‌استارت؛ عدم شمارش پاسخ‌های کش‌شده.
- [ ] `siavash llm-check --help`

### T-502 — کش و Replay پاسخ‌های LLM
`مالک: B | نوع: AGENT | اولویت: P0 | وابستگی: T-501 | Milestone: M4`
**خروجی:** `src/siavash/agents/cache.py`، `tests/unit/agents/test_cache.py`.
**شرح:** `LLMCache(path="logs/llm/cache.sqlite", mode: live|replay|record_only)`؛ کلید `"sha256:" + sha256(model + "|" + prompt_version + "|" + canonical_json(input))` (canonical: کلیدهای مرتب، floatها گرد به ۶ رقم). در `live`: اگر در کش هست همان را برگردان، وگرنه فراخوانی و ذخیره. در `replay`: فقط کش؛ نبودن → `CacheMiss`. یکپارچه با `LLMClient` (wrapper `CachedLLMClient`).
**پذیرش:**
- [ ] `pytest tests/unit/agents/test_cache.py -q`: دو فراخوانی با ورودی یکسان → یک درخواست HTTP؛ replay بدون شبکه.

### T-503 — اسکیماها، اعتبارسنجی و fallback قطعی
`مالک: B | نوع: AGENT | اولویت: P0 | وابستگی: T-502 | Milestone: M4`
**خروجی:** `src/siavash/agents/schemas.py`، `src/siavash/agents/fallback.py`، `tests/unit/agents/test_schemas.py`.
**شرح:** مدل‌های خروجی LLM خام (`LLMTradeReviewOut`, `LLMRegimeOut` طبق ضمیمه D) و تبدیل آن‌ها به `TradeReview`/`RegimeAssessment` با clamp (`size_multiplier` به [0.25,1]، `risk_multiplier` ≤ ضریب قاعده‌ای رژیم انتخاب‌شده، `enabled_strategies` ⊆ استراتژی‌های مجاز آن رژیم). خروجی نامعتبر → یک بار درخواست اصلاح با پیام خطای اعتبارسنجی → در غیر این صورت fallback. `fallback.py`: `fallback_review(ml, cfg)`: APPROVE با size 0.5 اگر `p_win ≥ θ + 0.05` (یا اگر ML خاموش است: APPROVE با size 0.5)، وگرنه REJECT؛ `status=FALLBACK`. `fallback_regime = RuleRegimeProvider`.
**پذیرش:**
- [ ] `pytest tests/unit/agents/test_schemas.py -q`

### T-507 — پرامپت‌های نسخه‌دار
`مالک: B | نوع: AGENT | اولویت: P0 | وابستگی: T-503 | Milestone: M4`
**خروجی:** `src/siavash/agents/prompts/{reviewer_v1.md,analyst_v1.md,reflector_v1.md}`، `src/siavash/agents/prompts/__init__.py` (loader)، `src/siavash/agents/prompts/manifest.json`، `tests/unit/agents/test_prompts.py`.
**شرح:** پرامپت‌ها از ضمیمه D (کامل‌شده به انگلیسی برای عملکرد بهتر مدل؛ توضیح فارسی در کامنت). frontmatter: `prompt_version`, `purpose`. `manifest.json`: hash محتوای هر پرامپت. loader: `load_prompt(name) -> (text, version)`.
**پذیرش:**
- [ ] `pytest tests/unit/agents/test_prompts.py -q`: اگر محتوای پرامپت عوض شده ولی hash در manifest نه → fail (یعنی تغییر پرامپت بدون نسخه جدید ممنوع).

### T-505 — TradeReviewer Agent
`مالک: B | نوع: AGENT | اولویت: P0 | وابستگی: T-507, T-107 (نرم) | Milestone: M4`
**خروجی:** `src/siavash/agents/reviewer.py`، `tests/unit/agents/test_reviewer.py`.
**شرح:** `LLMReviewer` پیاده‌سازی `Reviewer`. ساخت ورودی ساختاریافته (ضمیمه D) از Candidate، ویژگی‌های کلیدی (حداکثر ۳۰ ویژگی مهم: لیست ثابت در config)، MLScore (شامل top_features)، رژیم، پرتفوی، و آمار ۱۰ معامله اخیر همان استراتژی (از store). ابزارها اختیاری (`llm.reviewer.allow_tools`). محدودیت زمانی کل `reviewer_timeout_s (20)` → fallback. اگر `llm.reviewer.enabled=false` → fallback با `status=SKIPPED`.
**پذیرش:**
- [ ] `pytest tests/unit/agents/test_reviewer.py -q` با LLM mock: APPROVE، REJECT، timeout→fallback، خروجی خراب→fallback، clamp.

### T-504 — MarketAnalyst Agent
`مالک: B | نوع: AGENT | اولویت: P1 | وابستگی: T-507, T-304, T-107 (نرم) | Milestone: M4`
**خروجی:** `src/siavash/agents/analyst.py`، `tests/unit/agents/test_analyst.py`.
**شرح:** `LLMRegimeProvider` پیاده‌سازی `RegimeProvider`: ابتدا `RuleRegimeProvider` را اجرا کن؛ سپس LLM با ورودی (ویژگی‌های بازار، رژیم قاعده‌ای و دلیلش، عملکرد ۲۴ ساعت استراتژی‌ها، ابزارهای فقط‌خواندنی حداکثر ۳ فراخوانی). ادغام محافظه‌کارانه: رژیم نهایی = رژیمی که `risk_multiplier` کمتری دارد بین LLM و قاعده؛ `enabled_strategies` = اشتراک؛ `source="llm"` اگر انتخاب LLM غالب شد، وگرنه `"rule"`؛ `rule_regime` همیشه پر. زمان‌بندی: هر `analyst_interval_min (60)` یا وقتی `|x_btc_ret_15m| ≥ 2 × ATR%`. نتیجه در `logs/regime.jsonl` + DecisionRecord `REGIME_UPDATE`. خطا → قاعده‌ای با `status=FALLBACK`.
**پذیرش:**
- [ ] `pytest tests/unit/agents/test_analyst.py -q`: ادغام محافظه‌کارانه (LLM بگوید TREND_UP، قاعده RANGE → RANGE)، fallback.

### T-506 — DailyReflector Agent
`مالک: B | نوع: AGENT | اولویت: P2 | وابستگی: T-1201, T-507 | Milestone: M7`
**خروجی:** `src/siavash/agents/reflector.py`، `tests/unit/agents/test_reflector.py`.
**شرح:** `siavash reflect --date YYYY-MM-DD`: خواندن تصمیمات و fillهای روز، خلاصه آماری (از T-1201)، LLM → `reports/daily_YYYYMMDD.md` (چه کار کرد، چه نکرد، پیشنهاد پارامتر **فقط در بازه‌های مجاز config**، با برچسب «پیشنهاد — نیاز به بازبینی انسانی»). هیچ تغییری را اعمال نمی‌کند.
**پذیرش:**
- [ ] `pytest tests/unit/agents/test_reflector.py -q` با LLM mock.

---

### E4 — یادگیری ماشین

### T-401 — برچسب‌گذاری Triple-Barrier
`مالک: B | نوع: AGENT | اولویت: P1 | وابستگی: T-305, T-306 | Milestone: M4`
**خروجی:** `src/siavash/ml/labeling.py`، `tests/unit/ml/test_labeling.py`.
**شرح:** `label_candidate(c, future_bars: DataFrame, fee_pct, slippage_pct) -> (label: int, realized_ret_pct: float, exit_reason, bars_held)`: ورود در open کندل بعدی (+slippage)؛ پیمایش کندل‌ها: اگر در یک کندل هم low ≤ SL و هم high ≥ TP → **بدبینانه SL**؛ TP (برای S1 از TP1) → label=1؛ SL → 0؛ time-stop → label = 1 اگر بازده خالص > 0 وگرنه 0. بازده خالص بعد از کارمزد رفت‌وبرگشت.
**پذیرش:**
- [ ] `pytest tests/unit/ml/test_labeling.py -q` با مسیرهای قیمت ساختگی (TP، SL، هر دو در یک کندل، time-stop مثبت/منفی).

### T-402 — ساخت دیتاست
`مالک: B | نوع: AGENT | اولویت: P1 | وابستگی: T-401, T-202 | Milestone: M4`
**خروجی:** `src/siavash/ml/dataset.py`، `scripts/build_dataset.py`، `tests/unit/ml/test_dataset.py`.
**شرح:** اجرای ویژگی‌ها و همه SignalGeneratorها روی داده پردازش‌شده (رژیم از RuleRegimeProvider و برای این کار همه استراتژی‌ها را بدون فیلتر رژیم تولید کن و رژیم را به‌عنوان ویژگی one-hot اضافه کن)، برچسب‌گذاری، خروجی `data/ml/dataset_{FEATURE_SET_VERSION}.parquet` با ستون‌ها: ts, symbol, strategy, regime, features..., label, ret_pct, exit_reason. گزارش `reports/ml_dataset.md`: تعداد نمونه هر استراتژی/بازار، نسبت کلاس‌ها، بازده میانگین بدون فیلتر.
**پذیرش:**
- [ ] `pytest tests/unit/ml/test_dataset.py -q` روی داده مصنوعی.
- [ ] `python scripts/build_dataset.py --synthetic --out /tmp/ds.parquet` (حالت مصنوعی برای اجرای بدون داده واقعی)

### T-403 — آموزش Walk-forward
`مالک: B | نوع: AGENT | اولویت: P1 | وابستگی: T-402 | Milestone: M4`
**خروجی:** `src/siavash/ml/train.py`، `scripts/train_model.py`، `tests/unit/ml/test_train.py`.
**شرح:** LightGBM (پارامترهای ثابت در `strategy.yaml > ml.lgbm`، `seed=42`، `deterministic=true`، `num_threads=1`)؛ walk-forward: train `train_days (45)` → embargo `embargo_hours (12)` → test `test_days (7)` لغزان؛ متریک‌های هر fold: AUC، Brier، precision@θ، تعداد معاملات فیلترشده، بازده میانگین و تجمعی با/بدون فیلتر. مدل نهایی روی کل داده تا آخرین روز آموزش. گزارش `reports/ml_walkforward.md`.
**پذیرش:**
- [ ] `pytest tests/unit/ml/test_train.py -q`: دو اجرای پیاپی روی داده یکسان → پیش‌بینی‌های دقیقاً یکسان؛ هیچ نمونه test در بازه train+embargo.

### T-404 — کالیبراسیون و انتخاب آستانه
`مالک: B | نوع: AGENT | اولویت: P1 | وابستگی: T-403 | Milestone: M4`
**خروجی:** `src/siavash/ml/threshold.py`، بخش در `reports/ml_walkforward.md`، به‌روزرسانی `ml.threshold` در `config/strategy.yaml` (با کامنت منبع).
**شرح:** کالیبراسیون isotonic روی out-of-fold؛ برای θ ∈ {0.45..0.70 گام 0.025}: بازده خالص تجمعی، تعداد معامله، max drawdown معاملات فیلترشده روی همه foldها؛ انتخاب θ = بیشینه «بازده/max_dd» با شرط حداقل `min_trades_per_day (3)` معادل؛ گزارش پایداری θ بین foldها. **اگر هیچ θ بهتر از «بدون فیلتر» نبود، `ml.enabled=false` پیشنهاد و در گزارش صریحاً بنویس** (صداقت برای داور).
**پذیرش:**
- [ ] `pytest tests/unit/ml/test_threshold.py -q`

### T-405 — رجیستری مدل و SHAP
`مالک: B | نوع: AGENT | اولویت: P1 | وابستگی: T-404 | Milestone: M4`
**خروجی:** `src/siavash/ml/registry.py`، `src/siavash/ml/explain.py`، `models/README.md`، `tests/unit/ml/test_registry.py`.
**شرح:** ذخیره `models/lgbm_v{N}/model.txt`, `calibrator.pkl`, `metadata.json` (created_at, feature_set_version, features، data range، data hash، metrics، threshold، seed، git_sha). `load_model(version)` با بررسی `feature_set_version == FEATURE_SET_VERSION` (عدم تطابق → خطا). `explain(model, row, k=5) -> list[(feature, shap_value)]` با TreeExplainer.
**پذیرش:**
- [ ] `pytest tests/unit/ml/test_registry.py -q`

### T-406 — ML Scorer
`مالک: B | نوع: AGENT | اولویت: P1 | وابستگی: T-405 | Milestone: M4`
**خروجی:** `src/siavash/ml/scorer.py`، `tests/unit/ml/test_scorer.py`.
**شرح:** `LgbmScorer` پیاده‌سازی `Scorer`: `p_win` کالیبره، `threshold`، `ev_pct = p×tp% − (1−p)×sl% − round_trip_fee%`، `top_features`. اگر `ml.enabled=false` یا مدل نیست → `MLScore(status=SKIPPED)`؛ استثنا → `status=ERROR`.
**پذیرش:**
- [ ] `pytest tests/unit/ml/test_scorer.py -q` (زمان هر score < 50ms روی CPU با مدل کوچک تست).

---

### E6 — موتور تصمیم

### T-601 — Orchestrator
`مالک: A | نوع: AGENT | اولویت: P0 | وابستگی: T-203, T-604, T-703, T-704, T-804, T-1001, T-1101 | Milestone: M2`
**خروجی:** `src/siavash/engine/orchestrator.py`، `src/siavash/engine/clock.py`، `src/siavash/engine/factory.py`، `tests/unit/engine/test_orchestrator.py`.
**شرح:**
- `clock.py`: `SystemClock` (پیاده‌سازی `Clock`).
- `factory.py`: `build_app(cfg, mode) -> App` که بر اساس mode اجزا را می‌سازد: LIVE → LiveBroker+WallexMarketData؛ PAPER → PaperBroker+WallexMarketData؛ BACKTEST/REPLAY → BacktestBroker+HistoricalMarketData (T-901). اجزای تصمیم (signals, scorer, regime, reviewer, risk) برای همه حالت‌ها یکسان.
- `Orchestrator.run()`: taskهای هم‌زمان با زمان‌بندی از config: `price_guard` هر ۱۰ ثانیه (بررسی SL/TP/trailing همه پوزیشن‌ها با best bid؛ T-604)؛ `main_loop` هر ۶۰ ثانیه (equity، breaker، endgame step، reconcile زمان‌بندی‌شده)؛ handler رویداد `on_candle_close(15m)` → `DecisionPipeline.process_symbol` (T-602) برای هر بازار universe؛ رژیم ساعتی؛ universe هر ۶ ساعت؛ heartbeat (T-1102).
- shutdown تمیز روی SIGTERM/SIGINT: توقف ورود جدید، لغو سفارش‌های ورود باز (نه خروج)، flush لاگ‌ها، ذخیره state.
- شروع: reconcile (T-804) قبل از هر کاری؛ لاگ `startup` با sha/tag/config_hash/mode.
- حالت backtest: همان taskها با FakeClock/SimClock که زمان را رویداد به رویداد جلو می‌برد (بدون sleep واقعی).
**پذیرش:**
- [ ] `pytest tests/unit/engine/test_orchestrator.py -q`: با داده مصنوعی + FakeClock + FakeBroker و یک `StubPipeline` (در `tests/fakes/pipeline.py`؛ روی هر ۸امین کندل 15m یک ورود BTC/TMN با SL/TP ثابت می‌سازد، چون pipeline واقعی در T-602 ساخته می‌شود)، ۲ روز شبیه‌سازی‌شده بدون استثنا اجرا شود، حداقل یک ورود و یک خروج کامل رخ دهد، هر سفارش decision_id داشته باشد، و shutdown تمیز کار کند.
- [ ] Orchestrator باید pipeline را از طریق یک Protocol `PipelineLike` (متدهای `process_symbol` و `process_exit`) بگیرد تا StubPipeline و DecisionPipeline قابل جایگزینی باشند.

### T-602 — Decision Pipeline
`مالک: B | نوع: AGENT | اولویت: P0 | وابستگی: T-601, T-505, T-702, T-306, T-406 (نرم) | Milestone: M2`
**خروجی:** `src/siavash/engine/pipeline.py`، `tests/unit/engine/test_pipeline.py`.
**شرح:** `DecisionPipeline.process_symbol(symbol, ts)`: ساخت ویژگی‌ها (latest_row + cross + orderbook) → برای هر SignalGenerator → برای هر Candidate به ترتیب: فیلتر کارمزد → ML (اگر SKIPPED ادامه) → `p_win < θ` → رد ML → `ev_pct ≤ 0` → رد EV → Reviewer → REJECT → رد LLM → RiskManager → رد RISK → ساخت Position و `router.enter` در یک task جدا (non-blocking) → DecisionRecord با `outcome_action` و `final_reason` خوانا (قالب: `"{strategy} {شرط اصلی}; regime={…}; ML p={…}≥{θ}; LLM {verdict} x{mult}; size={…} TMN"`). **هر کاندید (پذیرفته یا رد) دقیقاً یک DecisionRecord.** حداکثر یک ورود برای هر بازار در هر کندل. همچنین `process_exit(position, exit_decision)` → DecisionRecord نوع EXIT + `router.exit`.
**پذیرش:**
- [ ] `pytest tests/unit/engine/test_pipeline.py -q`: یک تست برای رد در هر مرحله و یک مسیر کامل پذیرش؛ تعداد رکوردها = تعداد کاندیدها.

### T-1104 — CLI
`مالک: A | نوع: AGENT | اولویت: P0 | وابستگی: T-601 | Milestone: M2`
**خروجی:** `src/siavash/cli.py` (entrypoint `siavash` در pyproject)، `tests/unit/test_cli.py`.
**شرح:** typer: `run --mode {backtest,paper,live,replay} [--from --to]` (live: نیاز به env `CONFIRM_LIVE=YES` وگرنه خروج با خطا)، `status` (از store: breaker، equity، drawdown، پوزیشن‌ها، ۱۰ تصمیم آخر)، `pause`/`resume [--confirm]` (نوشتن در store که حلقه می‌خواند)، `flatten --confirm` (درخواست خروج کامل؛ موتور انجام می‌دهد با `ExitReason.OPERATOR`)، `report [--mode live]`، `replay --from --to`، `reflect --date`، `export-submission`. هر دستور اپراتوری → DecisionRecord نوع `OPERATOR_COMMAND`. Makefile را به این دستورات وصل کن.
**پذیرش:**
- [ ] `pytest tests/unit/test_cli.py -q` (typer CliRunner)؛ `run --mode live` بدون CONFIRM_LIVE خطا بدهد.

### T-1102 — Supervisor و Heartbeat
`مالک: A | نوع: AGENT | اولویت: P0 | وابستگی: T-601 | Milestone: M2`
**خروجی:** `src/siavash/engine/watchdog.py`، به‌روزرسانی healthcheck در `docker-compose.yml`، `tests/unit/engine/test_watchdog.py`.
**شرح:** نوشتن `state/heartbeat` (timestamp) هر ۳۰ ثانیه از main_loop؛ watchdog thread: اگر heartbeat > `watchdog_timeout_s (180)` قدیمی شد → لاگ critical + `os._exit(1)` تا Docker ری‌استارت کند. healthcheck: `find state/heartbeat -mmin -3`.
**پذیرش:**
- [ ] `pytest tests/unit/engine/test_watchdog.py -q`

### T-804 — Reconciliation
`مالک: A | نوع: AGENT | اولویت: P0 | وابستگی: T-1101, T-803 | Milestone: M2`
**خروجی:** `src/siavash/execution/reconcile.py`، `tests/unit/execution/test_reconcile.py`.
**شرح:** `reconcile(broker, store, positions)` در شروع و هر `reconcile_interval_min (5)`: (۱) سفارش‌های باز صرافی: مال ما (`is_ours` یا در id_map) ولی ناشناخته در state → وارد state؛ **غیر از ما → هشدار critical «ORDER_NOT_FROM_BOT» و دست نزن** (نشانه دخالت دستی)؛ (۲) سفارش‌هایی که در state باز هستند ولی در صرافی terminal → به‌روزرسانی با get_order و fillها؛ (۳) موجودی کوین‌ها vs Σ qty پوزیشن‌ها: اختلاف > max(min_qty, 0.5٪) → لاگ و اصلاح qty پوزیشن (اگر کمتر) یا ثبت «orphan balance» (اگر بیشتر؛ در endgame فروخته می‌شود). هر اصلاح → DecisionRecord نوع INFO.
**پذیرش:**
- [ ] `pytest tests/unit/execution/test_reconcile.py -q`: سه سناریو بالا.

### T-805 — متریک‌های سرعت
`مالک: A | نوع: AGENT | اولویت: P1 | وابستگی: T-602 | Milestone: M5`
**خروجی:** `src/siavash/reporting/latency.py`، `tests/unit/reporting/test_latency.py`.
**شرح:** ثبت زمان‌ها در `logs/latency.jsonl`: `candle_close → decision`، `decision → order_sent`، `order_sent → ack`، `order_sent → first_fill`، `exit_trigger → exit_fill`. تابع خلاصه p50/p95/max برای گزارش.
**پذیرش:**
- [ ] `pytest tests/unit/reporting/test_latency.py -q`

### T-1103 — هشدارها
`مالک: A | نوع: AGENT | اولویت: P1 | وابستگی: T-703 | Milestone: M5`
**خروجی:** `src/siavash/ops/alerts.py`، `tests/unit/ops/test_alerts.py`.
**شرح:** `Alerts` با backend قابل تنظیم: `webhook` (POST JSON به `ALERT_WEBHOOK_URL` — برای بله/تلگرام/Slack هر کدام از ایران قابل دسترس است)، `log_only` (پیش‌فرض). رویدادها: startup، entry، exit (با PnL)، breaker، reconcile critical، خطای تکراری، endgame، گزارش equity هر ساعت. ارسال non-blocking با صف و حداکثر ۱ پیام/۳ ثانیه؛ خطای ارسال هرگز موتور را متوقف نکند.
**پذیرش:**
- [ ] `pytest tests/unit/ops/test_alerts.py -q` (respx).

---

### E9 — بک‌تست

### T-902 — BacktestBroker، مدل fill، کارمزد و لغزش
`مالک: B | نوع: AGENT | اولویت: P0 | وابستگی: T-806, T-202 | Milestone: M3`
**خروجی:** `src/siavash/backtest/broker.py`، `src/siavash/backtest/market_data.py`، `tests/unit/backtest/test_broker.py`.
**شرح:** `HistoricalMarketData(processed_dir, clock)` پیاده‌سازی `MarketData` که فقط داده تا `clock.now()` برمی‌گرداند (ضد lookahead) و دفتر سفارش مصنوعی از کندل جاری می‌سازد (`mid = close`، spread = `bt.assumed_spread_pct` per symbol از config یا میانه اسپرد لایو ثبت‌شده). `BacktestBroker` پیاده‌سازی `Broker` روی کندل‌های 1m (اگر موجود) یا 15m: LIMIT BUY پر می‌شود اگر `low < price` در کندل **بعد** از ثبت (نه همان کندل)؛ MARKET/تهاجمی: قیمت = `open_next × (1 + spread/2 + bt.extra_slippage_pct (0.05)%)`؛ کارمزد maker/taker از config؛ SymbolGuard اجباری؛ از contract test عبور کند.
**پذیرش:**
- [ ] `pytest tests/unit/backtest/test_broker.py -q` (+ contract).

### T-903 — متریک‌های عملکرد
`مالک: B | نوع: AGENT | اولویت: P0 | وابستگی: T-902 | Milestone: M3`
**خروجی:** `src/siavash/reporting/metrics.py`، `tests/unit/reporting/test_metrics.py`.
**شرح:** `compute_metrics(fills_df, equity_df, decisions_df, capital_base) -> dict` (یک تابع برای بک‌تست، شدو و لایو): بازده خالص٪ (فرمول رسمی)، PnL تومانی، max drawdown٪ و مدت آن، Sharpe و Sortino (بر اساس بازده ساعتی، سالانه نشده + سالانه‌شده)، win rate، profit factor، میانگین سود/ضرر، expectancy، تعداد معاملات (round-trip) کل و در روز، حجم کل معاملات تومانی (مجموع notional همه fillها)، میانگین حجم هر معامله، exposure میانگین، کارمزد کل، و همه این‌ها به تفکیک استراتژی، بازار و رژیم؛ آمار pipeline: تعداد کاندید، رد در هر مرحله.
**پذیرش:**
- [ ] `pytest tests/unit/reporting/test_metrics.py -q` با معاملات ساختگی و مقادیر دستی‌محاسبه‌شده.

### T-901 — موتور بک‌تست
`مالک: B | نوع: AGENT | اولویت: P0 | وابستگی: T-601, T-602, T-902, T-903 | Milestone: M3`
**خروجی:** `src/siavash/backtest/engine.py`، `scripts/run_backtest.py`، `tests/unit/backtest/test_engine.py`.
**شرح:** `run_backtest(cfg, start, end, llm_mode: off|cached|live_sampled, sample_rate=0.2) -> BacktestResult` که **همان Orchestrator** را با `SimClock` + `HistoricalMarketData` + `BacktestBroker` + `InMemoryStore` اجرا می‌کند؛ لاگ‌ها در `reports/backtest/{run_id}/logs/` (همان فرمت لایو). `llm_mode=off`: reviewer و analyst با fallback/قاعده‌ای (`status=SKIPPED`). `live_sampled`: فقط نسبت `sample_rate` از کاندیدها به LLM واقعی (با کش)، بقیه fallback — برای کنترل هزینه.
**پذیرش:**
- [ ] `pytest tests/unit/backtest/test_engine.py -q`: روی داده مصنوعی ۳۰ روزه، اجرا کامل، معاملات > 0، دو اجرای پیاپی نتیجه یکسان (قطعی).
- [ ] `python scripts/run_backtest.py --synthetic --days 30 --llm off`

### T-905 — گزارش‌ساز بک‌تست
`مالک: B | نوع: AGENT | اولویت: P0 | وابستگی: T-901 | Milestone: M3`
**خروجی:** `src/siavash/backtest/report.py`، به‌روزرسانی `make backtest`.
**شرح:** `reports/backtest/{run_id}/REPORT.md` + `equity.png`, `drawdown.png`, `trades.csv`, `metrics.json`, `config_used.yaml`, `runtime.json` (git_sha, config_hash, model_version, prompt_versions, data range، data hash). جدول‌های تفکیکی. نمودارها با matplotlib (بدون نمایش، فقط ذخیره).
**پذیرش:**
- [ ] `make backtest ARGS="--synthetic --days 30 --llm off"` و وجود فایل‌های بالا.

### T-904 — Walk-forward، حساسیت پارامتر و مقایسه نسخه‌ها
`مالک: B | نوع: AGENT+HUMAN | اولویت: P1 | وابستگی: T-905, T-406 | Milestone: M4`
**خروجی:** `src/siavash/backtest/walkforward.py`، `scripts/run_walkforward.py`، `reports/backtest/walkforward/REPORT.md`.
**شرح (Agent):** (۱) بک‌تست پنجره‌ای روی کل داده (پنجره‌های ۷ روزه) و جدول بازده/DD هر پنجره؛ (۲) شبکه حساسیت برای `s3.z_entry ∈ {1.5,2,2.5,3}`، `s1.sl_atr ∈ {1,1.5,2}`، `ml.threshold ∈ {θ−0.05, θ, θ+0.05}` → heatmap بازده/DD (`sensitivity_*.png`)؛ (۳) مقایسه: «قاعده‌ای خالص» vs «+ML» vs «+ML+LLM (cached/live_sampled)» روی بازه یکسان؛ (۴) بنچمارک buy&hold BTC/TMN و «نقد ماندن». گزارش باید صادقانه بگوید کدام تنظیمات پایدار است. **Agent پارامترهای config را تغییر نمی‌دهد**؛ فقط در گزارش بخش «پیشنهاد» می‌نویسد.
**بخش انسان:** اجرای روی داده واقعی و تصمیم درباره پارامترهای نهایی (و commit آن‌ها با یادداشت CHANGELOG).
**پذیرش (Agent):**
- [ ] `python scripts/run_walkforward.py --synthetic --days 45 --quick`

---

### E10 — Shadow Testing

### T-1002 — اجرای Shadow
`مالک: B | نوع: HUMAN | اولویت: P0 | وابستگی: T-1106, T-1105, T-602 | Milestone: M5`
**شرح (انسان):** روی سرور نهایی: `make paper` (MODE=paper، LLM واقعی) به مدت ≥ ۱۲ تا ۲۴ ساعت. سپس `make shadow-report`. در طول لایو هم یک کانتینر دوم `bot-shadow` (MODE=paper، نسخه کاندید بعدی، دایرکتوری‌های logs/state جدا) اجرا کنید تا قبل از هر ارتقا مقایسه شود.
**پذیرش:** `reports/shadow/REPORT.md` بدون خطای بحرانی؛ تعداد کاندید و معامله معقول.

### T-1003 — مقایسه Backtest ↔ Shadow ↔ Live
`مالک: B | نوع: AGENT | اولویت: P1 | وابستگی: T-901, T-903 | Milestone: M5`
**خروجی:** `src/siavash/reporting/consistency.py`، `scripts/consistency_report.py`، تارگت `make shadow-report`.
**شرح:** ورودی: دایرکتوری لاگ shadow/live + داده لایو ذخیره‌شده (`data/live/`). اجرای بک‌تست روی همان بازه با همان داده (HistoricalMarketData از data/live) و `llm_mode=cached` (کش shadow)؛ مقایسه: درصد کاندیدهای یکسان (symbol, strategy, ts)، درصد تصمیم یکسان، لغزش واقعی vs فرض شده، بازده. خروجی `reports/consistency.md` با توضیح خودکار اختلاف‌های اصلی. همچنین تولید `reports/shadow/REPORT.md` با T-903.
**پذیرش:**
- [ ] `pytest tests/unit/reporting/test_consistency.py -q` (با لاگ‌های ساختگی که بخشی از آن‌ها یکسان است).

---

### E11/E12 — عملیات، گزارش و پایش

### T-1106 — نسخه‌گذاری دیپلوی
`مالک: A | نوع: AGENT | اولویت: P0 | وابستگی: T-004, T-003 | Milestone: M6`
**خروجی:** `scripts/deploy.sh`، بخش «Versions» در `CHANGELOG.md`.
**شرح:** اسکریپت: (۱) `git diff --quiet` وگرنه خروج؛ (۲) `make lint test`؛ (۳) پرسیدن نسخه `live-vX.Y` و توضیح تغییر؛ (۴) اضافه کردن ورودی به CHANGELOG (تاریخ، tag، sha، config_hash، model_version، توضیح) و commit؛ (۵) `git tag` و `git push --tags`؛ (۶) `docker compose build --build-arg GIT_SHA=$(git rev-parse --short HEAD)` و `VERSION_TAG` در env؛ (۷) `docker compose up -d`. اسکریپت `--dry-run` داشته باشد. Agent فقط `--dry-run` را تست می‌کند.
**پذیرش:**
- [ ] `bash -n scripts/deploy.sh` و `shellcheck scripts/deploy.sh` (اگر نصب است)

### T-1105 — RUNBOOK
`مالک: A | نوع: AGENT | اولویت: P0 | وابستگی: T-1104, T-1106 | Milestone: M6`
**خروجی:** `docs/RUNBOOK.md`.
**شرح:** بخش‌ها: پیش‌نیازها و راه‌اندازی سرور؛ شبکه (از T-109؛ اگر خالی است، placeholder «توسط انسان پر شود»)؛ تنظیم `.env` (هر متغیر)؛ اولین اجرا paper؛ go-live (ارجاع به چک‌لیست)؛ پایش (`siavash status`، لاگ‌ها، هشدارها)؛ رویه‌های اضطراری (pause، flatten، kill-switch، API down، LLM down، کرش)؛ ارتقای نسخه وسط چالش (shadow → مقایسه → deploy.sh)؛ endgame و تسویه؛ پشتیبان‌گیری لاگ‌ها (cron ساعتی rsync/tar به مقصد دوم).
**پذیرش:**
- [ ] همه دستورات ذکرشده در RUNBOOK در Makefile/CLI وجود داشته باشند: `python scripts/check_runbook_commands.py` (اسکریپت کوچکی که Agent می‌سازد و بلوک‌های کد RUNBOOK را با تارگت‌ها/دستورات موجود تطبیق می‌دهد).

### T-1201 — گزارش متریک‌های داوری
`مالک: B | نوع: AGENT | اولویت: P0 | وابستگی: T-903, T-805 | Milestone: M5`
**خروجی:** `src/siavash/reporting/judge_report.py`، تارگت `make report`.
**شرح:** `siavash report --logs logs/ --out reports/live/`: همه متریک‌های T-903 + بخش «شاخص‌های داوری» با دقیقاً همین عناوین: Number of Trades، Trading Volume (TMN)، Average Trade Volume، Net Return % (فرمول رسمی، با سرمایه مبنای ۵,۰۰۰,۰۰۰)، Max Drawdown %، تعداد معامله در هر روز چالش، API error rate، latency p50/p95، آمار نقش AI (کاندید، رد توسط ML/LLM/Risk، درصد APPROVE، تعداد fallback). نمودار equity و drawdown.
**پذیرش:**
- [ ] `pytest tests/unit/reporting/test_judge_report.py -q` با لاگ ساختگی.

### T-1203 — گزارش Counterfactual نقش AI
`مالک: B | نوع: AGENT | اولویت: P1 | وابستگی: T-1201, T-401 | Milestone: M5`
**خروجی:** `src/siavash/reporting/counterfactual.py`، بخش در `reports/live/REPORT.md`.
**شرح:** برای هر DecisionRecord با `rejected_at ∈ {ML, EV, LLM}`: با `label_candidate` (T-401) روی داده بعدی (data/live یا processed) نتیجه فرضی را حساب کن. جدول: «رد شده توسط ML: n، از این‌ها x زیان‌ده بودند (درست رد شده)، y سودده (فرصت از دست رفته)، PnL فرضی اجتناب‌شده»؛ همین برای LLM. خلاصه یک‌جمله‌ای برای STRATEGY.md.
**پذیرش:**
- [ ] `pytest tests/unit/reporting/test_counterfactual.py -q`

### T-1305 — دستور Replay برای داور
`مالک: B | نوع: AGENT | اولویت: P1 | وابستگی: T-1003, T-502 | Milestone: M7`
**خروجی:** `src/siavash/backtest/replay.py`، تارگت `make replay`.
**شرح:** `siavash replay --logs <dir> --from --to`: بارگذاری داده لایو ذخیره‌شده + کش LLM (mode=replay) + config و نسخه مدل ثبت‌شده در لاگ؛ اجرای موتور؛ مقایسه DecisionRecordهای بازتولیدشده با اصلی روی کلید (symbol, strategy, ts): درصد تطابق `outcome_action` و `rejected_at`؛ گزارش `reports/replay.md` با فهرست اختلاف‌ها و دلیل محتمل (مثلاً fill واقعی متفاوت).
**پذیرش:**
- [ ] `pytest tests/unit/backtest/test_replay.py -q`: لاگ تولیدشده توسط یک بک‌تست مصنوعی، replay شود و تطابق ≥ ۹۹٪.

### T-206 — Snapshot داده برای داوران
`مالک: B | نوع: AGENT | اولویت: P1 | وابستگی: T-202 | Milestone: M7`
**خروجی:** `scripts/make_snapshot.py`، `data/snapshot/README.md`، `data/snapshot/MANIFEST.json`.
**شرح:** بسته‌بندی `data/processed/` استفاده‌شده در بک‌تست نهایی و `data/live/` چالش در parquet فشرده (zstd) + sha256 هر فایل. اگر حجم > ۹۰MB: راهنمای git-lfs در README یا لینک دانلود ثابت (انسان آپلود می‌کند). `make download-data` اگر کلید API نیست، از snapshot استفاده کند.
**پذیرش:**
- [ ] `python scripts/make_snapshot.py --dry-run`

### T-1107 — Go-Live Checklist
`مالک: A | نوع: AGENT+HUMAN | اولویت: P0 | وابستگی: T-1002, T-704, T-1105 | Milestone: M6`
**خروجی (Agent):** `docs/GO_LIVE_CHECKLIST.md`، `scripts/preflight.py`.
**شرح (Agent):** `preflight.py` به‌صورت خودکار چک می‌کند و گزارش PASS/FAIL می‌دهد: تست‌ها سبز؛ `endpoints.VERIFIED == True`؛ `competition_end_utc` پر و در آینده؛ denylist بارگذاری‌شده و تعداد خطوط درست؛ `initial_live_risk_scale ≤ 0.5` برای اولین لایو؛ مقادیر risk.yaml ≤ سقف‌های بخش ۵؛ وجود گزارش shadow ≥ ۱۲ ساعت بدون خطای بحرانی؛ مدل ML (اگر enabled) موجود و feature_set منطبق؛ heartbeat/healthcheck؛ git clean و tag فعلی؛ کلید API فقط در `.env`؛ دسترسی شبکه (درخواست public والکس و یک فراخوانی ارزان LLM). چک‌لیست markdown موارد انسانی: «هیچ عضو تیم با حساب چالش در اپ/سایت معامله نمی‌کند»، «اعضای تیم روی حساب شخصی روی همان بازارها معامله هماهنگ نمی‌کنند»، «پشتیبان لاگ فعال است»، «هشدارها دریافت می‌شوند».
**بخش انسان:** اجرای `python scripts/preflight.py` روی سرور، تیک موارد انسانی، commit چک‌لیست، سپس `CONFIRM_LIVE=YES make live`.
**پذیرش (Agent):**
- [ ] `python scripts/preflight.py --offline` (فقط چک‌های بدون شبکه؛ اجازه FAIL برای موارد نیازمند داده واقعی، ولی بدون استثنا/crash)

### T-1204 — داشبورد ساده (اختیاری)
`مالک: A | نوع: AGENT | اولویت: P2 | وابستگی: T-1201 | Milestone: M7`
**خروجی:** `src/siavash/reporting/dashboard.py`، تارگت `make dashboard`.
**شرح:** صفحه HTML ایستا (بدون سرور؛ هر ۶۰ ثانیه بازسازی در `reports/live/dashboard.html`) شامل: equity/drawdown، پوزیشن‌ها، ۲۰ تصمیم آخر با `final_reason`، وضعیت breaker و رژیم. فقط برای ویدیو دمو؛ امتیاز UI ندارد.
**پذیرش:**
- [ ] `make dashboard` روی لاگ ساختگی فایل HTML تولید کند.

---

### E13 — تحویلی‌ها

### T-1301 — README
`مالک: A | نوع: AGENT | اولویت: P0 | وابستگی: T-1104, T-905 | Milestone: M7`
**خروجی:** `README.md`.
**شرح:** معرفی (۵ خط)؛ نمودار معماری متنی؛ پیش‌نیازها؛ «اجرای سریع برای داوران» (۳ دستور: clone → `make install` → `make backtest ARGS="--snapshot"` بدون کلید)؛ اجرای paper/live؛ replay؛ report؛ ساختار ریپو؛ لینک به STRATEGY.md، RUNBOOK، گزارش‌ها، لاگ‌های تحویلی و ویدیو (placeholder تا T-1304).
**پذیرش:**
- [ ] همه دستورات README در Makefile/CLI موجود: `python scripts/check_runbook_commands.py README.md`

### T-1302 — STRATEGY.md (داکیومنت داوری)
`مالک: B | نوع: AGENT+HUMAN | اولویت: P0 | وابستگی: T-904, T-1203, T-1201 | Milestone: M7`
**خروجی:** `docs/STRATEGY.md`.
**شرح (Agent):** بخش‌های الزامی چالش با همین عناوین: ۱) معماری و منطق اصلی الگوریتم؛ ۲) نحوه استفاده از هوش مصنوعی و ابزارهای والکس (API و MCP؛ کدام ابزار کجا)؛ ۳) شرایط تصمیم‌گیری برای خرید و فروش (جدول کامل هر استراتژی با پارامترهای **نسخه لایو** از config)؛ ۴) روش مدیریت سرمایه و ریسک (همه پارامترها و breakerها)؛ ۵) امکان اجرای مجدد و ارزیابی (دستورات). به‌علاوه: نتایج بک‌تست و walk-forward (لینک گزارش و جدول خلاصه)، shadow، لایو، counterfactual AI، تاریخچه نسخه‌ها (از CHANGELOG)، محدودیت‌ها و ریسک‌های شناخته‌شده (صادقانه)، جدول «معیار داوری → بخش این سند». اعداد را **از فایل‌های گزارش بخوان** (اسکریپت `scripts/fill_strategy_numbers.py` که placeholderهای `{{metric:...}}` را پر می‌کند)، نه دستی.
**بخش انسان:** بازبینی نهایی متن و اعداد بعد از پایان چالش.
**پذیرش (Agent):**
- [ ] `python scripts/fill_strategy_numbers.py --check` (هیچ placeholder پرنشده‌ای جز موارد مجاز «TBD after challenge» نمانده باشد)

### T-1303 — بسته لاگ تحویلی
`مالک: A | نوع: AGENT | اولویت: P0 | وابستگی: T-605, T-1201 | Milestone: M7`
**خروجی:** `scripts/export_submission.py`، تارگت `make export-submission`.
**شرح:** کپی به `submission/`: `logs/decisions.jsonl`, `orders.jsonl` (پاک‌سازی‌شده), `fills.csv`, `equity.csv`, `regime.jsonl`, `latency.jsonl`, `llm/calls.jsonl`, `llm/cache.sqlite`، `reports/live/`، `reports/backtest/` نهایی، `reports/shadow/`، `CHANGELOG.md` + `MANIFEST.md` (توضیح هر فایل و اسکیما و ارجاع به ضمیمه C) + `SHA256SUMS`. اسکن نهایی: هیچ رشته‌ای شبیه کلید API (regex طول > ۲۰ با الفبای کلید، مقدار env)، شماره موبایل یا کد ملی.
**پذیرش:**
- [ ] `pytest tests/unit/test_export_submission.py -q` (با یک کلید جعلی در لاگ ساختگی → export باید fail کند یا آن را ماسک کند).

### T-1304 — ویدیو دمو
`مالک: B | نوع: AGENT+HUMAN | اولویت: P0 | وابستگی: T-1201, T-1302 | Milestone: M7`
**خروجی (Agent):** `docs/DEMO_SCRIPT.md`.
**شرح (Agent):** سناریوی ۵ تا ۷ دقیقه‌ای با زمان‌بندی دقیقه‌ای و دستورات قابل اجرا برای هر صحنه: ۱) مسئله و تز (۴۵ث)؛ ۲) معماری (۶۰ث)؛ ۳) دنبال کردن یک معامله واقعی از لاگ: کندل → سیگنال → ML → LLM → ریسک → سفارش → fill → خروج (۹۰ث، با `jq` روی decisions.jsonl)؛ ۴) یک نمونه REJECT توسط LLM و گزارش counterfactual (۴۵ث)؛ ۵) ریسک و breakerها (۴۵ث)؛ ۶) بک‌تست/شدو/مقایسه (۴۵ث)؛ ۷) نتایج لایو و شاخص‌های داوری (۴۵ث)؛ ۸) اجرای مجدد با `make replay` (۳۰ث).
**بخش انسان:** ضبط، آپلود **قبل از ددلاین**، قرار دادن لینک در README.
**پذیرش (Agent):** فایل موجود با ۸ صحنه.

### T-1306 — چک‌لیست نهایی تحویل
`مالک: A | نوع: HUMAN | اولویت: P0 | وابستگی: T-1303, T-1304, T-1302 | Milestone: M7`
**شرح (انسان):** ریپو در دسترس داوران؛ tag نهایی = آخرین نسخه لایو (و مطابقت sha در لاگ‌ها)؛ STRATEGY.md نهایی؛ `make export-submission` و commit/آپلود بسته؛ ویدیو آپلود شده؛ endgame انجام و موجودی تمام‌تومانی (`reports/endgame.md`)؛ معرفی حساب والکس نماینده تیم برای دریافت سود.
**پذیرش:** همه موارد تیک خورده.


---

## 11. نقشه پوشش معیارهای داوری

| معیار | امتیاز | تسک‌های اصلی |
|---|---|---|
| درصد سود نهایی | ۹۰ | E3, E4, E5, T-904, T-1003 (بهبود مستمر با shadow)، T-704 (realized تومانی) |
| کنترل ضرر | ۳۰ | E7 کامل، T-1202 |
| فعال بودن | ۱۵ | سه استراتژی مکمل در رژیم‌های مختلف، T-702 (سقف‌ها)، T-1201 |
| پایداری اجرا | ۱۵ | T-102, T-103, T-802..T-804, T-1101, T-1102 |
| منطق استراتژی | ۴۰ | بخش ۴، T-305, T-605, T-1302 |
| استفاده واقعی AI | ۳۵ | E4, E5, T-1203, T-107 |
| ریسک و سرمایه | ۳۰ | E7، بخش ۵، T-1302 |
| اجرای مجدد | ۲۵ | T-004, T-206, T-502, T-1301, T-1305, T-1106 |
| بک‌تست و شدو | ۲۰ | E9, E10 |

---

## 12. ریسک‌های پروژه

| ریسک | اثر | راه‌حل |
|---|---|---|
| مستند API ناقص/متفاوت با فرض ما | تأخیر M1 | T-101 اول از همه؛ هیچ endpoint حدسی |
| داده تاریخی کم از والکس | بک‌تست ضعیف | داده کمکی USDT + تبدیل؛ ذکر صادقانه در مستند |
| دسترسی هم‌زمان به والکس و LLM از یک سرور | قطعی AI | T-109؛ fallback قطعی؛ کش |
| کارمزد بالا | سود صفر | فیلتر کارمزد، سفارش maker، معاملات کم‌تعداد با هدف بزرگ‌تر |
| overfit در بک‌تست | ضرر لایو | walk-forward، heatmap پایداری، shadow قبل از لایو |
| بازار ریزشی کل ۳ روز | ضرر | رژیم PANIC = بدون ورود؛ نقد ماندن هم یک تصمیم است (ولی حداقل فعالیت حفظ شود، با اندازه کوچک) |
| خطای انسانی (معامله دستی روی حساب) | حذف | هیچ‌کس وارد حساب چالش در اپ نشود؛ T-804 هشدار |
| تغییر کد وسط چالش و عدم تطابق با نسخه تحویلی | رد فنی | T-1106 تگ + CHANGELOG؛ همه نسخه‌ها در ریپو |
| فراموشی ددلاین ویدیو | حذف کامل | T-1304 زودتر از روز آخر |

---

## 13. ضمیمه‌ها

### ضمیمه A — `config/denylist.txt` (بازارهای ممنوعه طبق اعلام والکس)

> یک نماد در هر خط. هر بازاری که اینجا نیست و اسپات فعال است، مجاز است.

```
A/TMN
G/TMN
T/TMN
W/TMN
X/TMN
2Z/TMN
A/USDT
G/USDT
IO/TMN
OP/TMN
T/USDT
W/USDT
X/USDT
2Z/USDT
AMP/TMN
APT/TMN
ATH/TMN
BAT/TMN
BIO/TMN
C98/TMN
CAT/TMN
CHR/TMN
CRV/TMN
CVC/TMN
CVX/TMN
EDU/TMN
ENS/TMN
FET/TMN
FLR/TMN
GLM/TMN
GMT/TMN
GNO/TMN
GRT/TMN
HOT/TMN
ICP/TMN
ILV/TMN
INJ/TMN
IO/USDT
JST/TMN
JTO/TMN
JUP/TMN
KAS/TMN
KCS/TMN
KNC/TMN
LPT/TMN
LRC/TMN
MDT/TMN
MET/TMN
MMT/TMN
MNT/TMN
MTL/TMN
NFT/TMN
NMR/TMN
NOT/TMN
OMG/TMN
ONE/TMN
OP/USDT
PYR/TMN
QNT/TMN
RAY/TMN
REQ/TMN
RLC/TMN
RPL/TMN
RSR/TMN
SFP/TMN
SKL/TMN
SKY/TMN
SLP/TMN
SNX/TMN
SSV/TMN
TAO/TMN
TIA/TMN
TON/TMN
TRB/TMN
TWT/TMN
UMA/TMN
WIF/TMN
WIN/TMN
WOO/TMN
XMR/TMN
XPL/TMN
XTZ/TMN
YGG/TMN
ZEC/TMN
ZEN/TMN
ZIL/TMN
ZRO/TMN
ZRX/TMN
AERO/TMN
AEVO/TMN
AIOZ/TMN
ALGO/TMN
AMP/USDT
ANKR/TMN
API3/TMN
APT/USDT
ARKM/TMN
ATH/USDT
AVNT/TMN
BAND/TMN
BANK/TMN
BAT/USDT
BICO/TMN
BIO/USDT
BLUR/TMN
BOME/TMN
BONK/TMN
BTTC/TMN
C98/USDT
CATI/TMN
CAT/USDT
CELR/TMN
CGPT/TMN
CHR/USDT
COMP/TMN
CRV/USDT
CTSI/TMN
CVC/USDT
CVX/USDT
DASH/TMN
DEXE/TMN
DOGS/TMN
EDU/USDT
ENS/USDT
FET/USDT
FLOW/TMN
FLR/USDT
FRAX/TMN
GLM/USDT
GMT/USDT
GNO/USDT
GRAM/TMN
GRT/USDT
HOT/USDT
ICP/USDT
ILV/USDT
INJ/USDT
JST/USDT
JTO/USDT
JUP/USDT
KAIA/TMN
KAS/USDT
KCS/USDT
KITE/TMN
KMNO/TMN
KNC/USDT
LPT/USDT
LRC/USDT
MASK/TMN
MDT/USDT
MEME/TMN
MET/USDT
MMT/USDT
MNT/USDT
MOVE/TMN
MTL/USDT
NEAR/TMN
NEXO/TMN
NFT/USDT
NMR/USDT
NOT/USDT
OMG/USDT
ONDO/TMN
ONE/USDT
ORAI/TMN
ORCA/TMN
OSMO/TMN
PAXG/TMN
PNUT/TMN
POLS/TMN
PYR/USDT
PYTH/TMN
QNT/USDT
RAY/USDT
REQ/USDT
RLC/USDT
RPL/USDT
RSR/USDT
RUNE/TMN
SAFE/TMN
SFP/USDT
SKL/USDT
SKY/USDT
SLP/USDT
SNX/USDT
SOON/TMN
SSV/USDT
STRK/TMN
TAO/USDT
TIA/USDT
TON/USDT
TRAC/TMN
TRB/USDT
TWT/USDT
UMA/USDT
USDS/TMN
WBTC/TMN
WIF/USDT
WIN/USDT
WOO/USDT
XMR/USDT
XPL/USDT
XTZ/USDT
YFI/USDT
YGG/USDT
ZEC/USDT
ZEN/USDT
ZETA/TMN
ZIL/USDT
ZORA/TMN
ZRO/USDT
ZRX/USDT
AAPLX/TMN
AERO/USDT
AEVO/USDT
AIOZ/USDT
AIXBT/TMN
ALGO/USDT
AMZNX/TMN
ANKR/USDT
API3/USDT
ARKM/USDT
ASTER/TMN
AUDIO/TMN
AVNT/USDT
BAND/USDT
BANK/USDT
BICO/USDT
BLUR/USDT
BOME/USDT
BONK/USDT
BTTC/USDT
CATI/USDT
CELR/USDT
CGPT/USDT
COINX/TMN
COMP/USDT
CTSI/USDT
CYBER/TMN
DASH/USDT
DEXE/USDT
DOGS/USDT
ETHFI/TMN
FLOW/USDT
FRAX/USDT
GRAM/USDT
HMSTR/TMN
HOODX/TMN
JASMY/TMN
KAIA/USDT
KAITO/TMN
KITE/USDT
KMNO/USDT
LAYER/TMN
LINEA/TMN
MAGIC/TMN
MAJOR/TMN
MASK/USDT
MEME/USDT
METAX/TMN
METIS/TMN
MOVE/USDT
NEAR/USDT
NEIRO/TMN
NEXO/USDT
NVDAX/TMN
ONDO/USDT
ORAI/USDT
ORCA/USDT
OSMO/USDT
PAXG/USDT
PIXEL/TMN
PNUT/USDT
POLS/USDT
PROVE/TMN
PYTH/USDT
RUNE/USDT
SAFE/USDT
SENSO/TMN
SOON/USDT
STRK/USDT
SUPER/TMN
SYRUP/TMN
TOSHI/TMN
TRAC/USDT
TSLAX/TMN
TURBO/TMN
UNGON/TMN
USDS/USDT
WAVES/TMN
WBTC/USDT
ZETA/USDT
ZORA/USDT
AAPLX/USDT
AIXBT/USDT
AMZNX/USDT
ASTER/USDT
AUDIO/USDT
BANANA/TMN
COINX/USDT
CYBER/USDT
EIGEN/USDT
ETHFI/USDT
GIGGLE/TMN
HMSTR/USDT
HOODX/USDT
JASMY/USDT
KAITO/USDT
LAYER/USDT
LINEA/USDT
MAGIC/USDT
MAJOR/USDT
MEMEFI/TMN
METAX/USDT
METIS/USDT
MORPHO/TMN
NEIRO/USDT
NVDAX/USDT
PENDLE/TMN
PIXEL/USDT
PPLTON/TMN
PROVE/USDT
RENDER/TMN
SAHARA/TMN
SENSO/USDT
SUPER/USDT
SYRUP/USDT
TOSHI/USDT
TSLAX/USDT
TURBO/USDT
UNGON/USDT
WAVES/USDT
BANANA/USDT
GIGGLE/USDT
MEMEFI/USDT
MORPHO/USDT
PENDLE/USDT
PEOPLE/USDT
PPLTON/USDT
RENDER/USDT
SAHARA/USDT
VIRTUAL/TMN
VIRTUAL/USDT
1BBABYDOGE/TMN
1BBABYDOGE/USDT
```

> نکته: برخی جفت‌ها فقط در یک سمت ممنوع‌اند (مثلاً `YFI/USDT` و `EIGEN/USDT` و `PEOPLE/USDT` ممنوع هستند ولی نسخه TMN آن‌ها در لیست نیامده). SymbolGuard دقیقاً روی جفت کار می‌کند نه روی کوین.

### ضمیمه B — نمونه `config/risk.yaml`

```yaml
capital_base_tmn: 5000000
risk_per_trade_pct: 0.5
max_position_pct: 20
max_total_exposure_pct: 50
max_concurrent_positions: 3
max_positions_per_market: 1
daily_loss_limit_pct: 2.0
kill_switch_drawdown_pct: 5.0
cooldown_after_consecutive_losses: {losses: 2, minutes: 60}
cooldown_after_exit_minutes: 30
max_trades_per_hour: 4
max_trades_per_day: 25
max_spread_pct: 0.3
regime_multipliers: {TREND_UP: 1.0, RANGE: 0.7, HIGH_VOL: 0.5, PANIC: 0.0}
llm_size_multiplier_range: [0.25, 1.0]
initial_live_risk_scale: 0.5        # ضریب کلی در روز اول لایو
endgame:
  competition_end_utc: "YYYY-MM-DDTHH:MM:00Z"   # از برگزارکننده
  no_new_entries_hours_before: 6
  start_unwind_hours_before: 3
  force_liquidate_hours_before: 1
breakers:
  api_errors_window_min: 5
  api_errors_threshold: 5
  stale_data_seconds: 180
fees:
  maker_pct: 0.35    # مقدار بدبینانه؛ بعد از T-101 با کارمزد واقعی حساب جایگزین شود
  taker_pct: 0.35
```

### ضمیمه C — اسکیمای رکورد `decisions.jsonl`

```json
{
  "decision_id": "01JB...",
  "ts": "2026-11-05T09:15:00.123Z",
  "mode": "live",
  "git_sha": "abc1234",
  "version_tag": "live-v1.2",
  "config_hash": "sha256:...",
  "type": "ENTRY_CANDIDATE",
  "symbol": "BTC/TMN",
  "strategy": "S3_IMPLIED_DEV",
  "regime": {"label": "RANGE", "source": "llm", "rule_label": "RANGE", "risk_mult": 0.7},
  "signal": {
    "conditions": {"z_dev": -2.4, "spread_pct": 0.08, "usdt_pair_ret_1h": -0.002},
    "entry_ref": 7350000000, "sl": 7262000000, "tp": 7460000000,
    "expected_move_pct": 1.5, "round_trip_fee_pct": 0.5
  },
  "ml": {"status": "ok", "model_version": "lgbm_v3", "p_win": 0.62, "threshold": 0.55,
         "ev_pct": 0.31, "top_features": [["z_dev", -0.21], ["rsi_15m", 0.08]]},
  "llm_review": {"status": "ok", "model": "…", "prompt_version": "reviewer_v1",
                 "cache_key": "sha256:...", "verdict": "APPROVE", "size_multiplier": 0.75,
                 "reasons": ["…"], "risk_flags": []},
  "risk": {"status": "approved", "size_tmn": 760000, "checks_failed": [], "equity_tmn": 5032000},
  "outcome": {"action": "ORDER_PLACED", "client_order_ids": ["SIAV-01JB...-E-1"]},
  "final_reason": "S3 deviation z=-2.4 in RANGE regime; ML p=0.62≥0.55; LLM approved x0.75"
}
```
انواع `type`: `ENTRY_CANDIDATE`, `EXIT`, `REGIME_UPDATE`, `BREAKER`, `OPERATOR_COMMAND`, `ENDGAME`. برای کاندید رد شده، `outcome.action = "REJECTED"` و `rejected_at` (مثلاً `ML`, `EV`, `LLM`, `RISK_MAX_POSITIONS`).

### ضمیمه D — پرامپت‌ها و اسکیمای خروجی LLM (نسخه اولیه)

**TradeReviewer — system prompt (خلاصه):**
> تو بازبین ریسک یک سیستم معاملاتی اسپات (فقط خرید و فروش، بدون اهرم) در صرافی والکس هستی. یک کاندید معامله که از فیلترهای کمّی عبور کرده به تو داده می‌شود. وظیفه تو تشخیص ریسک‌هایی است که قواعد کمّی نمی‌بینند: ناسازگاری بین تایم‌فریم‌ها، نزدیکی به مقاومت‌های مهم، نوسان غیرعادی، تمرکز بیش از حد پرتفوی، عملکرد ضعیف اخیر همین استراتژی. فقط بر اساس داده‌های داده‌شده قضاوت کن و چیزی از خودت نساز. نمی‌توانی اندازه را بیشتر از ۱ کنی. فقط JSON معتبر طبق اسکیما برگردان.

**ورودی (user, JSON):** candidate (strategy, conditions, entry/sl/tp)، features خلاصه 15m/1h/4h، regime، ml (p_win, top_features)، portfolio (پوزیشن‌های باز، exposure، drawdown فعلی)، recent_performance (۱۰ معامله آخر همین استراتژی).

**خروجی:**
```json
{"verdict": "APPROVE", "size_multiplier": 0.75,
 "reasons": ["4h trend supportive", "deviation large vs 24h distribution"],
 "risk_flags": ["volatility above 7d median"], "confidence": 0.7}
```

**MarketAnalyst — خروجی:**
```json
{"regime": "RANGE", "risk_multiplier": 0.7,
 "enabled_strategies": ["S2_MEANREV", "S3_IMPLIED_DEV"],
 "reasons": ["BTC ADX(1h)=14", "breadth 48%"], "watch": ["USDT/TMN rising fast"]}
```

### ضمیمه E — محتوای `AGENTS.md` / `CLAUDE.md` (راهنمای Coding Agent)

```markdown
# AGENTS.md — Siavash Wallex Trading Agent

## Source of truth
WALLEX_AGENT_PLAN.md (repo root). Before any work read its sections:
"دستورالعمل اجرایی برای Coding Agent", 1, 3, 4, 5, 7, 8 (incl. 8.3 Contracts), 9.4.
Then read the full text of the task(s) you will implement in section 10.

## How to pick work
- Prompt names a task ID -> do only that task.
- Prompt says OWNER=A or OWNER=B -> autopilot, only that owner's tasks.
- Otherwise -> autopilot over all AGENT / AGENT+HUMAN tasks.
Autopilot: open PROGRESS.md, take the first TODO task in table 9.4 order whose
dependencies are DONE; implement; run its acceptance commands + `make lint test`;
mark DONE and commit "T-xxx: <title>"; repeat. Run `python scripts/check_progress.py`
to list next available tasks (after T-010 exists).
HUMAN tasks -> mark WAITING_HUMAN with exact instructions, continue with others.
Stuck after 3 serious attempts -> mark BLOCKED with the error, continue with others.
Running out of context -> leave the repo green, update PROGRESS.md, stop.

## Absolute rules
1. Never enable MODE=live, never place real orders, never run scripts/smoke_test.py or `make live`.
2. Never guess Wallex endpoints/fields. Use only src/siavash/exchange/endpoints.py and
   docs/wallex_api_notes.md. Unknown -> mock it, note it in PROGRESS.md.
3. Spot only. No futures/margin/leverage/withdraw/transfer/deposit code paths.
4. Every order passes SymbolGuard inside the broker/client layer and carries a decision_id.
5. The LLM never places orders and can only lower risk (size_multiplier <= 1).
6. No hardcoded parameters: everything from config/*.yaml.
7. Do not raise limits in config/risk.yaml above the plan's section 5 values.
8. Do not change src/siavash/core/* unless unavoidable; if you do, fix all users,
   keep tests green, and add "CONTRACT CHANGE:" to CHANGELOG.md.
9. Never log or commit secrets (.env, API keys).
10. No lookahead: features/signals at time t only use data closed at or before t.

## Conventions
- Python 3.11, full type hints, pydantic v2, async I/O with httpx, structlog JSON logs.
- UTC everywhere internally; Tehran time only for "trading day" boundaries in reports/breakers.
- Decimal for prices/qty/money in exchange & accounting layers; float in features/ML.
- Canonical symbols "BASE/QUOTE" (e.g. BTC/TMN); exchange format only inside exchange/.
- Unit tests in tests/unit (no network). Live tests in tests/integration with @pytest.mark.live.
- Use tests/fakes (FakeClock, InMemoryMarketData, FakeBroker, synthetic data) instead of network.

## Definition of done (every task)
1. All acceptance items of the task pass; `make lint` and `make test` pass.
2. Only the task's output files (plus tests, PROGRESS.md, CHANGELOG.md if needed) changed.
3. Docs touched by the change are updated.
4. PROGRESS.md updated; one commit "T-xxx: <title>".
```

### ضمیمه F — پرامپت‌های آماده برای دادن به Agent

**F1 — اتوپایلوت کامل (یک Agent، کل پروژه):**
```
فایل WALLEX_AGENT_PLAN.md در ریشه ریپو منبع حقیقت پروژه است.
بخش «دستورالعمل اجرایی برای Coding Agent» را بخوان و دقیقاً طبق «حلقه اتوپایلوت» عمل کن.
اگر ریپو خالی است از T-001 شروع کن. هیچ‌وقت ممنوعیت‌های مطلق را نقض نکن.
در پایان کار یا وقتی context رو به اتمام است، PROGRESS.md را به‌روز کن و خلاصه بده:
چه تسک‌هایی DONE شد، کدام BLOCKED (با دلیل)، و کدام WAITING_HUMAN (با کاری که من باید انجام دهم).
```

**F2 — حالت دو نفره (هر نفر در session خودش):**
```
فایل WALLEX_AGENT_PLAN.md منبع حقیقت است. OWNER=A
(یا OWNER=B)
طبق «حلقه اتوپایلوت» فقط تسک‌های مالک A را انجام بده.
اگر تسکی به خروجی مالک دیگر وابسته است که هنوز DONE نیست، از Fakeهای tests/fakes استفاده کن
یا تسک بعدی را بردار. قبل از شروع `git pull` و بعد از هر تسک `git push` کن.
```

**F3 — یک تسک مشخص:**
```
فایل WALLEX_AGENT_PLAN.md منبع حقیقت است. بخش‌های «دستورالعمل اجرایی»، ۱، ۳، ۷ و ۸.۳ را بخوان،
سپس فقط تسک T-xxx را از بخش ۱۰ پیاده کن. وقتی همه بندهای «پذیرش» آن و `make lint test` سبز شد،
PROGRESS.md را به‌روز و commit کن و متوقف شو.
```

**F4 — ادامه بعد از قطع شدن:**
```
WALLEX_AGENT_PLAN.md و PROGRESS.md را بخوان. اگر تسکی IN_PROGRESS است، اول وضعیت کد آن را بررسی کن
(git status، تست‌ها)، آن را تمام کن یا به آخرین حالت سالم برگردان، سپس اتوپایلوت را ادامه بده.
```

**F5 — بعد از اینکه مستندات والکس را گذاشتید:**
```
مستندات رسمی والکس را در docs/vendor/wallex/ (و MCP در docs/vendor/wallex_mcp/) گذاشتم.
T-101 را دوباره اجرا کن: endpoints.py و wallex_api_notes.md و fixtureها را با مستندات واقعی تطبیق بده،
VERIFIED=True کن، و همه تست‌های E1 را دوباره سبز کن. هر تفاوتی با فرضیات قبلی را در CHANGELOG بنویس.
```

### ضمیمه G — قالب `PROGRESS.md`

```markdown
# PROGRESS — Siavash Wallex Agent

> وضعیت‌ها: TODO | IN_PROGRESS | DONE | DONE (UNVERIFIED) | BLOCKED | WAITING_HUMAN
> این فایل را فقط Agentها و اعضای تیم به‌روز می‌کنند. ترتیب ردیف‌ها = ترتیب اجرا (بخش ۹.۴).

## خلاصه
- آخرین به‌روزرسانی: <ISO datetime>
- DONE: 0 / N
- منتظر انسان: —
- مسدود: —

## جدول
| # | ID | عنوان | مالک | نوع | وابستگی‌ها | وضعیت | commit | یادداشت |
|---|----|-------|------|-----|------------|-------|--------|---------|
| 1 | T-001 | اسکلت ریپو | A | AGENT | — | TODO | | |
| … | … | … | … | … | … | … | | |
```
(ردیف‌ها دقیقاً از جدول ۹.۴ کپی شوند.)


### ضمیمه H — endpointهای API والکس (✅ از Swagger رسمی)

> ✅ منبع: فایل‌های Swagger رسمی در `docs/vendor/wallex/` (`wallex-basic-services.swagger.json` و `wallex-spot.swagger.json`). در صورت تفاوت، Swagger برنده است. host در Swagger `api.wallex.com` است ولی MCP روی `api.wallex.ir` است؛ در T-108 هر دو تست و یکی انتخاب شود (مقدار در `WALLEX_BASE_URL`).

| کاربرد | متد و مسیر | نکات |
|---|---|---|
| لیست بازارها | `GET /hector/web/v1/markets` | `result.markets[]` با `symbol, base_asset, quote_asset, price_precision, amount_precision (تعداد رقم اعشار → tick = 10^-precision), is_spot, is_margin, quote_volume_24h, price`. **حداقل مقدار/ارزش سفارش ندارد** (از config با مقدار محافظه‌کار؛ در T-108 تأیید شود) |
| دفتر سفارش | `GET /v1/depth?symbol=BTCTMN` | `result.ask[]`, `result.bid[]` با `price, quantity, sum` |
| دفتر سفارش همه بازارها | `GET /depth/all` | `result` دیکشنری نماد → `{ask, bid}` (برای انتخاب universe) |
| معاملات اخیر | `GET /v1/trades?symbol=` | `result.latestTrades[]` با `price, quantity, isBuyOrder, timestamp` |
| کندل | `GET /v1/udf/history?symbol=&resolution=&from=&to=` | قالب UDF: `{s,t[],o[],h[],l[],c[],v[]}` (اعداد به‌صورت string)؛ resolution مثل `1, 5, 15, 60, D` |
| احراز هویت | هدر `x-api-key` | |
| موجودی | `GET /v1/account/balances` | `result.balances` دیکشنری asset → `{value (کل), locked}`؛ free = value − locked |
| کارمزد | `GET /v1/account/fee` | `result[SYMBOL] = {makerFeeRate, takerFeeRate}` (کسر؛ 0.002 = 0.2٪) — **منبع کارمزد config همین است** |
| ثبت سفارش | `POST /v1/account/orders` | بدنه (همه string): `symbol, side, type (LIMIT/MARKET/STOP_LIMIT/STOP_MARKET), price, quantity, client_id, stop_Price`. ربات فقط LIMIT و MARKET استفاده می‌کند |
| وضعیت سفارش | `GET /v1/account/orders/{client_id}` | `OrderResource`: `clientOrderId, status, active, origQty, executedQty, executedPrice, executedSum, fee, fills[] (price, quantity, sum, fee, feeAsset, isBuyer, timestamp)` |
| لغو سفارش | `DELETE /v1/account/orders/{client_id}` | |
| سفارش‌های باز | `GET /v1/account/openOrders?symbol=` | |
| تاریخچه سفارش‌ها | `GET /v1/account/orders?market=&side=&type=&page=&per_page=&from=&to=` | `per_page` حداکثر ۱۰۰؛ `from/to` با فرمت `YYYY-MM-DD`. **endpoint جدا برای معاملات کاربر وجود ندارد؛ fillها از `fills[]` سفارش‌ها استخراج شوند** |
| ⛔ ممنوع | `/v1/account/wallets/*`, `/v1/account/ibans`, `/v1/account/money-withdrawal`, `/v1/account/card-numbers` | هرگز در کد استفاده نشوند؛ در `endpoints.py` هم تعریف نشوند |
| نامعلوم (در T-108 تعیین شود) | — | حداقل سفارش، مقادیر `status`، حروف `side`/`type` در بدنه (BUY یا buy)، طول مجاز `client_id`، rate limit |

> این فهرست از اطلاعات عمومی قبلی جمع‌آوری شده و **ممکن است قدیمی یا نادرست باشد**. فقط برای ساخت نسخه اولیه `endpoints.py` با `VERIFIED = False` و fixtureهای mock استفاده شود. مستندات رسمی‌ای که برگزارکننده داده، همیشه برنده است.

| کاربرد | متد و مسیر (پایه: `https://api.wallex.ir`) | نکات |
|---|---|---|
| لیست بازارها | `GET /v1/markets` | `result.symbols` دیکشنری با کلید نماد (مثل `BTCTMN`) و فیلدهایی مثل `baseAsset`, `quoteAsset`, `stats` (bid/ask/last/24h volume)، احتمالاً `minQty`/`minNotional`/دقت |
| دفتر سفارش | `GET /v1/depth?symbol=BTCTMN` | `result.ask[]` و `result.bid[]` با `price`, `quantity`, `sum` |
| آخرین معاملات | `GET /v1/trades?symbol=BTCTMN` | `result.latestTrades[]` با `price`, `quantity`, `isBuyOrder`, `timestamp` |
| کندل | `GET /v1/udf/history?symbol=BTCTMN&resolution=15&from=<unix>&to=<unix>` | قالب TradingView UDF: `{s,t[],o[],h[],l[],c[],v[]}`؛ resolutionهای مجاز و حداکثر بازه را تأیید کنید |
| احراز هویت | هدر `X-API-Key: <key>` | |
| موجودی | `GET /v1/account/balances` | |
| ثبت سفارش | `POST /v1/account/orders` | بدنه احتمالی: `symbol, side (buy/sell), type (limit/market), price, quantity, client_id` |
| لغو سفارش | `DELETE /v1/account/orders` | با شناسه سفارش/clientOrderId |
| وضعیت سفارش | `GET /v1/account/orders/{id}` | |
| سفارش‌های باز | `GET /v1/account/openOrders?symbol=` | |
| معاملات کاربر | `GET /v1/account/trades?symbol=` | |
| کارمزد کاربر | `GET /v1/account/fee` (احتمالی) | |

### ضمیمه I — نمونه `config/strategy.yaml` و `config/base.yaml`

```yaml
# config/strategy.yaml
strategies:
  enabled: [S1_TREND_BREAKOUT, S2_MEAN_REVERSION, S3_IMPLIED_DEV]
fee_multiple: 2.5                 # expected_move >= fee_multiple * round_trip_fee
universe:
  candidates: [BTC/TMN, ETH/TMN, USDT/TMN, SOL/TMN, XRP/TMN, DOGE/TMN, TRX/TMN, ADA/TMN,
               BNB/TMN, LTC/TMN, SHIB/TMN, AVAX/TMN, LINK/TMN, DOT/TMN, BCH/TMN,
               BTC/USDT, ETH/USDT, SOL/USDT, XRP/USDT, DOGE/USDT, TRX/USDT, ADA/USDT,
               BNB/USDT, LTC/USDT, SHIB/USDT, AVAX/USDT, LINK/USDT, DOT/USDT, BCH/USDT]
  # هر نماد قبل از استفاده با SymbolGuard و registry بررسی می‌شود؛ نمادهای ناموجود حذف می‌شوند
  max_markets: 8
  depth_multiple: 5
  refresh_hours: 6
regime:
  rules:
    panic_btc_ret_1h_pct: -3.0
    panic_btc_ret_4h_pct: -5.0
    high_vol_rv_percentile: 90
    trend_adx_min: 20
    trend_breadth_min: 55
  panic_exit_min_profit_pct: 0.3
s1:   # Trend Breakout
  donchian_n: 20
  adx_min: 20
  vol_z_min: 1.0
  sl_atr: 1.5
  tp1_atr: 2.0
  tp1_fraction: 0.5
  tp_atr: 4.0
  trail_atr: 1.5
  time_stop_min: 720
s2:   # Mean Reversion
  bb_n: 20
  bb_k: 2.0
  rsi_max: 30
  adx_max: 20
  min_4h_ema200_dist_pct: -2.0
  sl_atr: 1.2
  time_stop_min: 360
s3:   # Implied Deviation
  z_window_bars: 96
  z_entry: 2.0
  min_usdt_ret_1h_pct: -1.5
  tp_pct: 1.5
  sl_pct: 1.2
  time_stop_min: 180
ml:
  enabled: false                  # تا T-404 بعد از walk-forward روشن شود
  model_version: null
  threshold: 0.55
  train_days: 45
  test_days: 7
  embargo_hours: 12
  min_trades_per_day: 3
  lgbm: {num_leaves: 15, max_depth: 5, learning_rate: 0.05, n_estimators: 300,
         min_child_samples: 40, subsample: 0.8, subsample_freq: 1, colsample_bytree: 0.8,
         reg_lambda: 1.0, seed: 42, deterministic: true, num_threads: 1}
reviewer_key_features: [15m_rsi14, 15m_atr14_pct, 15m_bb_pos, 15m_vol_z20, 15m_ret_4,
  1h_adx14, 1h_ema20_slope, 1h_ret_4, 4h_ema200_dist, 4h_rsi14, x_implied_z,
  x_btc_ret_1h, x_btc_ret_4h, x_breadth_ema50, x_rv_percentile_7d, ob_spread_pct, ob_imbalance_5]
```

```yaml
# config/base.yaml
mode: paper                       # با env MODE بازنویسی می‌شود
timezone: Asia/Tehran             # فقط برای مرز «روز معاملاتی»
paths: {logs: logs, state: state, data: data, models: models, reports: reports}
loop:
  price_guard_s: 10
  main_loop_s: 60
  poll_seconds: 20
  book_poll_seconds: 15
  buffer_bars: 500
  reconcile_interval_min: 5
  equity_interval_s: 300
  watchdog_timeout_s: 180
  analyst_interval_min: 60
exchange:
  use_proxy: false
  rate_limit: {public_per_s: 5, public_burst: 10, private_per_s: 2, private_burst: 4}
  timeout_s: 10
execution:
  entry_timeout_s: 90
  aggressive_timeout_s: 30
  max_slippage_pct: 0.15
  exit_max_slippage_pct: 0.3
  order_poll_seconds: 5
paper:
  fill_check_seconds: 5
  initial_balances: {TMN: 5000000}
bt:
  assumed_spread_pct: {default: 0.15, BTC/TMN: 0.05, ETH/TMN: 0.08, USDT/TMN: 0.03}
  extra_slippage_pct: 0.05
llm:
  analyst: {enabled: true}
  reviewer: {enabled: true, allow_tools: false, timeout_s: 20}
  timeout_s: 20
  max_tool_calls: 3
  tools_backend: auto             # auto | mcp | native
  use_proxy: true
  cache_mode: live                # live | replay | record_only
alerts:
  backend: log_only               # log_only | webhook
  min_interval_s: 3
```

