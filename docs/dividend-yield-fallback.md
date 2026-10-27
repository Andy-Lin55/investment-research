# HK Dividend Yield Fallback

The weekly financial snapshot keeps a finite, non-negative primary dividend yield,
including a genuine zero. If that field is missing or invalid, or the primary HK
financial endpoint fails, the HK fetcher tries a cash-dividend calculation.

## Calculation

- Use Eastmoney's latest completed trading-day close, with adjustment disabled.
- Reject prices older than ten calendar days, non-positive prices, wrong-security
  responses and RMB counters. Same-day intraday bars are not used.
- Read complete dividend history for the HK ordinary-share security. An empty or
  incomplete response is missing data, not proof of zero dividends.
- Sum cash distributions with ex-dividend dates in `(price date minus one calendar
  year, price date]`, including special cash distributions. Do not annualize.
- Accept only explicit per-share HKD amounts, or explicit HKD equivalents in the
  source's foreign-currency cash-dividend plan. No guessed FX rate, ADS conversion,
  stock dividend, or per-ten-share conversion is performed.
- Deduplicate identical distributions; reject conflicting versions, missing dates,
  unsupported plans and unknown currency conversions within the relevant window.
- Write `cash dividends / unadjusted HKD close * 100`, rounded to four decimals.
  `DividendYield` stores percentage points (for example, `3.7206`, not `0.037206`).

Calculation logs include the price date, price, trailing window, cash amounts and
individual source plans. Failed calculations omit the field and preserve other
financial metrics. The existing Notion updater then leaves an unavailable field
untouched; it does not replace missing data with zero.

Fallback HTTP calls have ten-second timeouts and at most three attempts on a
transport disconnect or timeout, with short bounded backoff.

This fallback is scoped to HK ordinary shares. A-share and US sources are unchanged.

## Daily Briefing Schedule

Daily Pipeline is requested at 05:00 Asia/Shanghai on Monday-Friday, using the
previous day's 21:00 UTC schedule. GitHub's scheduler may delay or drop events;
this earlier request is a buffer, not a guarantee of delivery before 09:30.
