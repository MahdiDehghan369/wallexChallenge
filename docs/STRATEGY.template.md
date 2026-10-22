# Siavash — سند استراتژی (داوری چالش والکس)

> این فایل از `docs/STRATEGY.template.md` با `python scripts/fill_strategy_numbers.py` ساخته می‌شود؛
> همه اعداد از فایل‌های گزارش و config خوانده می‌شوند، نه دستی. موارد «TBD after challenge» بعد از پایان چالش پر می‌شوند.

## ۱) معماری و منطق اصلی الگوریتم

ربات فقط **اسپات** و فقط روی بازارهای تومانی (`{{cfg:risk.allowed_quotes}}`) معامله می‌کند؛ سرمایه مبنا
`{{cfg:risk.capital_base_tmn}}` تومان. هر ۱۵ دقیقه، با بسته شدن کندل، این زنجیره اجرا می‌شود:

```text
کندل بسته‌شده → ویژگی‌ها (15m/1h/4h، قیمت ضمنی USDT، رژیم بازار)
  → سه مولد سیگنال (S1 روند، S2 بازگشت به میانگین، S3 انحراف از قیمت ضمنی)
  → فیلتر کارمزد → امتیاز ML → بازبینی LLM → RiskManager (اندازه، سقف‌ها، breakerها)
  → Router (LIMIT passive و سپس aggressive) → پیگیری fill → مدیریت خروج (SL/TP/trailing/time-stop)
```

- یک Orchestrator برای همه حالت‌ها (live / paper / backtest / replay)؛ فقط broker و منبع داده عوض می‌شود.
- انتخاب بازار: از بین `{{cfg:strategy.universe.top_volume_n}}` بازار پرحجم تومانی، حداکثر
  `{{cfg:strategy.universe.max_markets}}` بازار با عمق کافی؛ `{{cfg:strategy.universe.always_keep}}` همیشه پایش می‌شوند.
- رژیم بازار (TREND_UP / RANGE / HIGH_VOL / PANIC) با قواعد قطعی روی BTC و breadth بازار تعیین می‌شود و
  تحلیلگر LLM فقط می‌تواند آن را محافظه‌کارانه‌تر کند. استراتژی‌های فعال در هر رژیم:
  TREND_UP: `{{cfg:strategy.regime.enabled_strategies.TREND_UP}}` · RANGE: `{{cfg:strategy.regime.enabled_strategies.RANGE}}` ·
  HIGH_VOL: `{{cfg:strategy.regime.enabled_strategies.HIGH_VOL}}` · PANIC: هیچ.
- همه تصمیم‌ها (حتی ردشده‌ها) با دلیل خوانا در `logs/decisions.jsonl` ثبت می‌شوند.

## ۲) نحوه استفاده از هوش مصنوعی و ابزارهای والکس

| جزء | چه می‌کند | محدودیت |
|---|---|---|
| ML Scorer (LightGBM، walk-forward) | احتمال موفقیت پس از کارمزد هر کاندید + توضیح SHAP | فقط فیلتر؛ فعال: `{{cfg:strategy.ml.enabled}}` (θ = `{{cfg:strategy.ml.threshold}}`) |
| TradeReviewer (LLM) | APPROVE/REJECT و ضریب اندازه با دلیل | ضریب فقط در بازه `{{cfg:risk.llm_size_multiplier_range}}` — هرگز ریسک را بالا نمی‌برد |
| MarketAnalyst (LLM + ابزار) | رژیم و استراتژی‌های مجاز | فقط محدودتر از قاعده |
| DailyReflector (LLM) | گزارش روزانه و پیشنهاد پارامتر | فقط پیشنهاد در `strategy.tunable_ranges`؛ هیچ چیز خودکار اعمال نمی‌شود |

- ارائه‌دهنده LLM: BoxAPI (`{{cfg:llm.base_url}}`)، مدل `{{cfg:llm.model}}`، fallback `{{cfg:llm.fallback_model}}`،
  `temperature=0`، کش پاسخ با کلید hash(model + prompt_version + input) برای replay دقیق.
- بودجه کل LLM ۳ دلار: سقف `{{cfg:llm.budget.max_calls_per_hour}}` فراخوانی در ساعت، `{{cfg:llm.budget.max_calls_per_day}}`
  در روز و `{{cfg:llm.budget.max_tokens_per_day}}` توکن در روز؛ با رسیدن به سقف، fallback قطعی.
- **API والکس** (Swagger رسمی): بازارها `/hector/web/v1/markets`، دفتر سفارش `/v1/depth`، کندل `/v1/udf/history`
  (15m از کندل 1m ساخته می‌شود)، موجودی، کارمزد حساب، ثبت/لغو/وضعیت سفارش با `client_id`.
- **Wallex MCP** (`https://api.wallex.ir/mcp`): فقط ابزارهای فقط‌خواندنی در اختیار LLM؛ هیچ ابزار سفارش‌گذاری.

## ۳) شرایط تصمیم‌گیری برای خرید و فروش

پارامترها از `config/strategy.yaml` نسخه فعلی. ورود فقط وقتی که حرکت مورد انتظار ≥ `{{cfg:strategy.fee_multiple}}` × کارمزد رفت‌وبرگشت باشد.

| استراتژی | شرط ورود (روی کندل بسته‌شده 15m) | حد ضرر | حد سود | time-stop |
|---|---|---|---|---|
| S1 Trend Breakout | شکست سقف Donchian(`{{cfg:strategy.s1.donchian_n}}`)، EMA20>EMA50 در 1h، ADX(1h) > `{{cfg:strategy.s1.adx_min}}`، z حجم > `{{cfg:strategy.s1.vol_z_min}}` | `{{cfg:strategy.s1.sl_atr}}`×ATR | TP1 در `{{cfg:strategy.s1.tp1_atr}}`×ATR (`{{cfg:strategy.s1.tp1_fraction}}` حجم)، هدف `{{cfg:strategy.s1.tp_atr}}`×ATR، trailing `{{cfg:strategy.s1.trail_atr}}`×ATR | `{{cfg:strategy.s1.time_stop_min}}` دقیقه |
| S2 Mean Reversion | قیمت زیر باند پایین بولینگر(`{{cfg:strategy.s2.bb_n}}`, `{{cfg:strategy.s2.bb_k}}`)، RSI < `{{cfg:strategy.s2.rsi_max}}`، ADX(1h) < `{{cfg:strategy.s2.adx_max}}`، فاصله از EMA200(4h) > `{{cfg:strategy.s2.min_4h_ema200_dist_pct}}`٪ | `{{cfg:strategy.s2.sl_atr}}`×ATR | میانه بولینگر | `{{cfg:strategy.s2.time_stop_min}}` دقیقه |
| S3 Implied Deviation | قیمت تومانی ارزان‌تر از قیمت ضمنی (ارز/USDT × USDT/TMN)، z < −`{{cfg:strategy.s3.z_entry}}` روی `{{cfg:strategy.s3.z_window_bars}}` کندل، بازده 1h جفت USDT > `{{cfg:strategy.s3.min_usdt_ret_1h_pct}}`٪ | `{{cfg:strategy.s3.sl_pct}}`٪ | بازگشت z به صفر (حداکثر `{{cfg:strategy.s3.tp_pct}}`٪) | `{{cfg:strategy.s3.time_stop_min}}` دقیقه |

خروج: SL/TP/trailing هر `{{cfg:base.loop.price_guard_s}}` ثانیه با best bid بررسی می‌شود (سمت ربات، چون stop اسپات استفاده نمی‌شود)؛
در PANIC پوزیشن‌های سودده با سود ≥ `{{cfg:strategy.regime.panic_exit_min_profit_pct}}`٪ بسته می‌شوند؛ endgame در بخش ۴.

## ۴) روش مدیریت سرمایه و ریسک

`size = min(ریسک٪ × equity / SL٪ ، سقف پوزیشن × equity ، exposure آزاد) × ضریب رژیم × ضریب LLM`

| پارامتر | مقدار |
|---|---|
| ریسک هر معامله | `{{cfg:risk.risk_per_trade_pct}}`٪ equity |
| سقف هر پوزیشن / کل exposure | `{{cfg:risk.max_position_pct}}`٪ / `{{cfg:risk.max_total_exposure_pct}}`٪ |
| حداکثر پوزیشن هم‌زمان / در هر بازار | `{{cfg:risk.max_concurrent_positions}}` / `{{cfg:risk.max_positions_per_market}}` |
| حد ضرر روزانه (توقف ورود تا روز بعد تهران) | `{{cfg:risk.daily_loss_limit_pct}}`٪ |
| Kill-switch (فروش همه + توقف کامل) | افت `{{cfg:risk.kill_switch_drawdown_pct}}`٪ از اوج |
| Cooldown | `{{cfg:risk.cooldown_after_consecutive_losses.minutes}}` دقیقه بعد از `{{cfg:risk.cooldown_after_consecutive_losses.losses}}` ضرر پیاپی؛ `{{cfg:risk.cooldown_after_exit_minutes}}` دقیقه بعد از خروج از یک بازار |
| سقف تعداد معامله | `{{cfg:risk.max_trades_per_hour}}` در ساعت، `{{cfg:risk.max_trades_per_day}}` در روز |
| ضریب رژیم | `{{cfg:risk.regime_multipliers}}` |
| سقف اسپرد | `{{cfg:risk.max_spread_pct}}`٪ |
| breaker فنی | ≥ `{{cfg:risk.breakers.api_errors_threshold}}` خطای API در `{{cfg:risk.breakers.api_errors_window_min}}` دقیقه یا داده کهنه‌تر از `{{cfg:risk.breakers.stale_data_seconds}}` ثانیه → توقف ورود |
| روز اول لایو | ضریب کلی `{{cfg:risk.initial_live_risk_scale}}` |
| Endgame | بدون ورود از T−`{{cfg:risk.endgame.no_new_entries_hours_before}}`h، خروج تدریجی از T−`{{cfg:risk.endgame.start_unwind_hours_before}}`h، فروش اجباری از T−`{{cfg:risk.endgame.force_liquidate_hours_before}}`h |
| کارمزد فرض‌شده | maker `{{cfg:risk.fees.maker_pct}}`٪ / taker `{{cfg:risk.fees.taker_pct}}`٪ |

هر سفارش از SymbolGuard (denylist، فقط اسپات تومانی) و decision_id عبور می‌کند؛ هیچ مسیر futures/margin/برداشت در کد وجود ندارد.

## ۵) امکان اجرای مجدد و ارزیابی

```bash
make install
make backtest ARGS="--snapshot --llm off"
make replay ARGS="--logs submission/logs --live-data data/live"
make report
```

- داده‌های منجمد در `data/snapshot/` (sha256 در MANIFEST.json)؛ بدون کلید API قابل اجرا.
- `make replay` تصمیمات را با کش LLM بازتولید و درصد تطابق را گزارش می‌کند (تست واحد: ≥ ۹۹٪).
- بسته لاگ تحویلی: `make export-submission` → `submission/` (MANIFEST.md و SHA256SUMS).

## نتایج

### داده و ML

- دیتاست: `{{md:reports/ml_dataset.md:samples: \*\*(\d+)\*\*}}` کاندید برچسب‌خورده روی داده واقعی؛ میانگین بازده خالص بدون فیلتر
  `{{md:reports/ml_dataset.md:unfiltered mean net return: ([-\d.]+) %}}`٪ در هر معامله.
- walk-forward ML: AUC خارج از نمونه `{{md:reports/ml_walkforward.md:pooled OOF AUC: ([\d.]+)}}`؛ توصیه فعال‌سازی:
  `{{json:models/lgbm_v1/metadata.json:recommend_enabled}}` (جزئیات `reports/ml_walkforward.md`).

### بک‌تست و walk-forward

- پنجره‌ها: `{{md:reports/backtest/walkforward/REPORT.md:windows: (\d+/\d+) engine windows lost money}}` پنجره با زیان؛
  میانگین `{{md:reports/backtest/walkforward/REPORT.md:mean ([-\d.]+) % per window}}`٪ در هر پنجره.
- حساسیت پارامتر: `{{md:reports/backtest/walkforward/REPORT.md:sensitivity: (\d+/\d+) parameter cells}}` خانه سودده.
- مقایسه نسخه‌ها (بازده خالص٪): قاعده‌ای `{{md:reports/backtest/walkforward/REPORT.md:\| rule-only \| ([-\d.]+) \|}}`؛
  بنچمارک buy & hold بیت‌کوین `{{md:reports/backtest/walkforward/REPORT.md:buy & hold BTC/TMN ([-\d.]+) %}}`٪، نقد ماندن ۰٪.

### Shadow و لایو

- Shadow: بازده `{{json:reports/shadow/metrics.json:net_return_pct}}`٪.
- لایو: Net Return `{{json:reports/live/metrics.json:judge.Net Return %}}`٪، Max Drawdown `{{json:reports/live/metrics.json:judge.Max Drawdown %}}`٪،
  تعداد معامله `{{json:reports/live/metrics.json:judge.Number of Trades}}`، حجم `{{json:reports/live/metrics.json:judge.Trading Volume (TMN)}}` تومان.

### Counterfactual نقش AI

- LLM: از `{{json:reports/live/metrics.json:counterfactual.LLM.rejected}}` رد، `{{json:reports/live/metrics.json:counterfactual.LLM.correct_rejections}}`
  زیان‌ده بودند و `{{json:reports/live/metrics.json:counterfactual.LLM.missed_opportunities}}` فرصت از دست رفت.
- ML: از `{{json:reports/live/metrics.json:counterfactual.ML.rejected}}` رد، `{{json:reports/live/metrics.json:counterfactual.ML.correct_rejections}}` درست بودند.

## تاریخچه نسخه‌ها

{{changelog}}

## محدودیت‌ها و ریسک‌های شناخته‌شده

- با کارمزد بدبینانه فعلی (`{{cfg:risk.fees.maker_pct}}`٪ هر طرف) و اسپرد، هر سه استراتژی روی ۱۲۰ روز داده واقعی بازده
  خالص منفی داشتند؛ ML زیان را کم می‌کند ولی سودده نمی‌کند، پس غیرفعال ماند. نتیجه با کارمزد واقعی حساب باید دوباره سنجیده شود.
- حداقل سفارش اسپات در Swagger نیست؛ مقدار محافظه‌کارانه از config (`{{cfg:base.exchange.min_notional}}`).
- کندل‌های والکس پراکنده‌اند (بدون معامله = بدون کندل)؛ پر کردن رو به جلو فقط وقتی کندل بعدی واقعی رسیده باشد.
- شبیه‌سازی fill در بک‌تست از اسپرد فرضی استفاده می‌کند؛ لغزش واقعی در `reports/consistency.md` مقایسه می‌شود.

## معیار داوری → بخش این سند

| معیار | بخش |
|---|---|
| منطق الگوریتم و معماری | ۱ |
| نقش واقعی هوش مصنوعی و ابزارهای والکس | ۲، Counterfactual |
| شرایط خرید و فروش | ۳ |
| مدیریت سرمایه و ریسک | ۴ |
| تکرارپذیری و ارزیابی | ۵، نتایج |
| شاخص‌های معاملاتی (بازده، افت، تعداد و حجم معامله) | نتایج → Shadow و لایو |
