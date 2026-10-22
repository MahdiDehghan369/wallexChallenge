# PROGRESS — Siavash Wallex Agent

> وضعیت‌ها: TODO | IN_PROGRESS | DONE | DONE (UNVERIFIED) | BLOCKED | WAITING_HUMAN
> این فایل را فقط Agentها و اعضای تیم به‌روز می‌کنند. ترتیب ردیف‌ها = ترتیب اجرا (بخش ۹.۴).

## خلاصه
- آخرین به‌روزرسانی: 2026-10-22T06:17:49Z
- DONE: 28 / 84
- منتظر انسان: T-101, T-109
- مسدود: —

## جدول
| # | ID | عنوان | مالک | نوع | وابستگی‌ها | وضعیت | commit | یادداشت |
|---|----|-------|------|-----|------------|-------|--------|---------|
| 1 | T-001 | اسکلت ریپو | A | AGENT | — | DONE | f077fe6 | Python 3.11 venv (uv) در WSL؛ نصب از mirror-pypi.runflare.com چون files.pythonhosted.org در دسترس نیست |
| 2 | T-002 | سیستم پیکربندی | A | AGENT | T-001 | DONE | d07b56f |  |
| 3 | T-003 | لاگ ساختاریافته | A | AGENT | T-002 | DONE | 12a93b1 |  |
| 4 | T-007 | قراردادهای کد مشترک | A | AGENT | T-001 | DONE | 0fd5c22 |  |
| 5 | T-008 | Fakeها و داده مصنوعی | A | AGENT | T-007 | DONE | dcc2d48 | پیاده‌سازی synthetic در src/siavash/data/synthetic.py (برای اسکریپت‌های --synthetic)؛ tests/fakes/synthetic.py re-export |
| 6 | T-605 | نویسنده لاگ تصمیم | A | AGENT | T-003, T-007 | DONE | 09ac157 |  |
| 7 | T-004 | Docker و Makefile کامل | A | AGENT | T-001 | DONE | 85109aa | docker build سبز با build-arg: BASE_IMAGE=docker.arvancloud.ir/python:3.11-slim، DEBIAN_MIRROR=mirror.arvancloud.ir، PIP_INDEX_URL=runflare (Docker Hub مستقیم 403) |
| 8 | T-005 | فایل راهنمای Agent | A | AGENT | T-001 | DONE | 2db9483 |  |
| 9 | T-010 | فایل PROGRESS.md | A | AGENT | T-001 | DONE | a3e4f78 |  |
| 10 | T-301 | کتابخانه اندیکاتورها | B | AGENT | T-001 | DONE | 2508390 |  |
| 11 | T-303 | Feature Builder چندتایم‌فریمی | B | AGENT | T-301, T-007 | DONE | b019752 |  |
| 12 | T-205 | قیمت ضمنی و انحراف | B | AGENT | T-303 | DONE | 98dcf51 |  |
| 13 | T-304 | ویژگی‌های رژیم و رژیم‌یاب قاعده‌ای | B | AGENT | T-303 | DONE | 46ba3e8 | ویژگی x_btc_ema_trend_1h و نگاشت regime.enabled_strategies در strategy.yaml اضافه شد |
| 14 | T-305 | Signal Generatorها | B | AGENT | T-304, T-205 | DONE | 4e8b415 | ویژگی کمکی x_implied_dev_mean_pct برای TP در z=0 اضافه شد |
| 15 | T-306 | فیلتر کارمزد | B | AGENT | T-305 | DONE | 92129cb | با کارمزد بدبینانه 0.35/0.35 حداقل حرکت 1.75٪ است و همه کاندیدهای S3 (سقف TP=1.5٪) رد می‌شوند — کارمزد واقعی لازم است |
| 16 | T-101 | کشف و مستندسازی API والکس | A | AGENT+HUMAN | T-001 | DONE (UNVERIFIED) | a2d3365 | WAITING_HUMAN: مستندات رسمی API در docs/vendor/wallex/، MCP در docs/vendor/wallex_mcp/، کارمزد maker/taker در docs/vendor/wallex/fees.txt و نمونه پاسخ‌های واقعی در docs/vendor/wallex/samples/ بگذارید؛ بعد پرامپت F5. بخش‌های balances/orders/trades/errors فعلاً MOCK SCHEMA هستند |
| 17 | T-102 | کلاینت HTTP پایه | A | AGENT | T-101, T-003 | DONE | 58837ac |  |
| 18 | T-103 | متادیتای بازار و گرد کردن | A | AGENT | T-102 | DONE | 1e27baf |  |
| 19 | T-104 | endpointهای داده بازار | A | AGENT | T-103 | DONE | 346e8f4 | ticker_cache_s به base.yaml اضافه شد |
| 20 | T-105 | endpointهای حساب و سفارش | A | AGENT | T-103 | DONE | 68bd18e | OrderIdMap در state/id_map.sqlite |
| 21 | T-106 | SymbolGuard | A | AGENT | T-105 | DONE | 56780c8 | allowed_quotes و allow_usdt_liquidation به risk.yaml اضافه شد؛ USDT/TMN فقط SELL در endgame |
| 22 | T-109 | بررسی شبکه و محل اجرا | A | HUMAN | — | WAITING_HUMAN |  | انسان: VPS نهایی را انتخاب کنید و از همان سرور دسترسی و latency به API والکس (curl https://api.wallex.ir/v1/markets) و API مدل LLM (مستقیم یا با HTTPS_PROXY) را بررسی کنید؛ نتیجه را در docs/RUNBOOK.md بخش «شبکه» بنویسید |
| 23 | T-108 | Smoke test حساب واقعی | A | AGENT+HUMAN | T-106, T-605, T-109 | TODO |  | منتظر T-109 (HUMAN)؛ بخش Agent بعد از T-109 انجام می‌شود |
| 24 | T-201 | دانلودر داده تاریخی | B | AGENT+HUMAN | T-104 | DONE | e1587da | بخش انسان هم انجام شد: Agent داده عمومی ۱۲۰ روز (15m,1h) ۲۹ نماد را با endpointهای عمومی دانلود کرد (بدون کلید) — reports/data_coverage.md؛ روی سرور نهایی هم make download-data اجرا شود |
| 25 | T-202 | اعتبارسنجی و پاک‌سازی داده | B | AGENT | T-201 | DONE | 7f2deb1 | روی داده واقعی اجرا شد: reports/data_quality.md (گپ مشترک صرافی 2026-09-22 12:45) |
| 26 | T-203 | سرویس داده لایو | A | AGENT | T-104, T-008 | DONE | 4ae26b8 | باکت‌های گم‌شده (بازار کم‌معامله) فقط بعد از رسیدن کندل بعدی ffill می‌شوند |
| 27 | T-204 | انتخاب Universe | B | AGENT | T-203, T-106 | DONE | 0f8de8c | top_volume_n به strategy.yaml اضافه شد |
| 28 | T-302 | ویژگی‌های دفتر سفارش | B | AGENT | T-303 | DONE | d471c8f |  |
| 29 | T-806 | LiveBroker و تست قراردادی Broker | A | AGENT | T-106, T-008 | DONE | 493f84c | tests/fakes/wallex_server.py: سرور جعلی stateful برای contract |
| 30 | T-1001 | PaperBroker (شبیه‌سازی با دفتر سفارش واقعی) | A | AGENT | T-806, T-203 | DONE | 12f9b06 | prepare_order مشترک (guard+گرد کردن+حداقل) در exchange/account.py |
| 31 | T-801 | Order Router | A | AGENT | T-806, T-103 | TODO |  |  |
| 32 | T-802 | Idempotency و بازیابی سفارش | A | AGENT | T-801 | TODO |  |  |
| 33 | T-803 | رهگیری سفارش و fillها | A | AGENT | T-802, T-605 | TODO |  |  |
| 34 | T-603 | Position Manager | A | AGENT | T-803 | TODO |  |  |
| 35 | T-604 | منطق خروج | A | AGENT | T-603 | TODO |  |  |
| 36 | T-1101 | State ماندگار | A | AGENT | T-603 | TODO |  |  |
| 37 | T-701 | اندازه پوزیشن | A | AGENT | T-103, T-007 | TODO |  |  |
| 38 | T-702 | چک‌های پیش از معامله و RiskManager | A | AGENT | T-701, T-106 | TODO |  |  |
| 39 | T-1202 | ردیاب Equity و Drawdown | A | AGENT | T-1101, T-203 | TODO |  |  |
| 40 | T-703 | Circuit Breakerها | A | AGENT | T-1202, T-102 | TODO |  |  |
| 41 | T-704 | Endgame Liquidator | A | AGENT | T-801, T-1202 | TODO |  |  |
| 42 | T-705 | تست‌های جامع ریسک | A | AGENT | T-702, T-703, T-704 | TODO |  |  |
| 43 | T-804 | Reconciliation | A | AGENT | T-1101, T-803 | TODO |  |  |
| 44 | T-601 | Orchestrator | A | AGENT | T-203, T-604, T-703, T-704, T-804, T-1001, T-1101 | TODO |  |  |
| 45 | T-501 | لایه انتزاعی LLM | B | AGENT | T-002, T-003 | TODO |  |  |
| 46 | T-502 | کش و Replay پاسخ‌های LLM | B | AGENT | T-501 | TODO |  |  |
| 47 | T-503 | اسکیماها، اعتبارسنجی و fallback قطعی | B | AGENT | T-502 | TODO |  |  |
| 48 | T-507 | پرامپت‌های نسخه‌دار | B | AGENT | T-503 | TODO |  |  |
| 49 | T-107 | یکپارچه‌سازی Wallex MCP | B | AGENT | T-104, T-501 | TODO |  |  |
| 50 | T-505 | TradeReviewer Agent | B | AGENT | T-507, T-107 (نرم) | TODO |  |  |
| 51 | T-504 | MarketAnalyst Agent | B | AGENT | T-507, T-304, T-107 (نرم) | TODO |  |  |
| 52 | T-602 | Decision Pipeline | B | AGENT | T-601, T-505, T-702, T-306, T-406 (نرم) | TODO |  |  |
| 53 | T-1104 | CLI | A | AGENT | T-601 | TODO |  |  |
| 54 | T-1102 | Supervisor و Heartbeat | A | AGENT | T-601 | TODO |  |  |
| 55 | T-805 | متریک‌های سرعت | A | AGENT | T-602 | TODO |  |  |
| 56 | T-1103 | هشدارها | A | AGENT | T-703 | TODO |  |  |
| 57 | T-902 | BacktestBroker، مدل fill، کارمزد و لغزش | B | AGENT | T-806, T-202 | TODO |  |  |
| 58 | T-903 | متریک‌های عملکرد | B | AGENT | T-902 | TODO |  |  |
| 59 | T-901 | موتور بک‌تست | B | AGENT | T-601, T-602, T-902, T-903 | TODO |  |  |
| 60 | T-905 | گزارش‌ساز بک‌تست | B | AGENT | T-901 | TODO |  |  |
| 61 | T-401 | برچسب‌گذاری Triple-Barrier | B | AGENT | T-305, T-306 | TODO |  |  |
| 62 | T-402 | ساخت دیتاست | B | AGENT | T-401, T-202 | TODO |  |  |
| 63 | T-403 | آموزش Walk-forward | B | AGENT | T-402 | TODO |  |  |
| 64 | T-404 | کالیبراسیون و انتخاب آستانه | B | AGENT | T-403 | TODO |  |  |
| 65 | T-405 | رجیستری مدل و SHAP | B | AGENT | T-404 | TODO |  |  |
| 66 | T-406 | ML Scorer | B | AGENT | T-405 | TODO |  |  |
| 67 | T-904 | Walk-forward، حساسیت پارامتر و مقایسه نسخه‌ها | B | AGENT+HUMAN | T-905, T-406 | TODO |  |  |
| 68 | T-1106 | نسخه‌گذاری دیپلوی | A | AGENT | T-004, T-003 | TODO |  |  |
| 69 | T-1105 | RUNBOOK | A | AGENT | T-1104, T-1106 | TODO |  |  |
| 70 | T-1003 | مقایسه Backtest ↔ Shadow ↔ Live | B | AGENT | T-901, T-903 | TODO |  |  |
| 71 | T-1002 | اجرای Shadow | B | HUMAN | T-1106, T-1105, T-602 | TODO |  |  |
| 72 | T-1201 | گزارش متریک‌های داوری | B | AGENT | T-903, T-805 | TODO |  |  |
| 73 | T-1203 | گزارش Counterfactual نقش AI | B | AGENT | T-1201, T-401 | TODO |  |  |
| 74 | T-1107 | Go-Live Checklist | A | AGENT+HUMAN | T-1002, T-704, T-1105 | TODO |  |  |
| 75 | T-506 | DailyReflector Agent | B | AGENT | T-1201, T-507 | TODO |  |  |
| 76 | T-1305 | دستور Replay برای داور | B | AGENT | T-1003, T-502 | TODO |  |  |
| 77 | T-206 | Snapshot داده برای داوران | B | AGENT | T-202 | TODO |  |  |
| 78 | T-006 | CI | A | AGENT | T-001 | TODO |  |  |
| 79 | T-1301 | README | A | AGENT | T-1104, T-905 | TODO |  |  |
| 80 | T-1302 | STRATEGY.md (داکیومنت داوری) | B | AGENT+HUMAN | T-904, T-1203, T-1201 | TODO |  |  |
| 81 | T-1303 | بسته لاگ تحویلی | A | AGENT | T-605, T-1201 | TODO |  |  |
| 82 | T-1204 | داشبورد ساده (اختیاری) | A | AGENT | T-1201 | TODO |  |  |
| 83 | T-1304 | ویدیو دمو | B | AGENT+HUMAN | T-1201, T-1302 | TODO |  |  |
| 84 | T-1306 | چک‌لیست نهایی تحویل | A | HUMAN | T-1303, T-1304, T-1302 | TODO |  |  |
