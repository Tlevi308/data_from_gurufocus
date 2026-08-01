# GuruFocus — פאנל רבעוני מצומצם

הפרויקט מושך נתונים רבעוניים מ־GuruFocus, מחשב NOPAT, ‏IC_RAW, ‏ROIC, ‏EV/FCF ויחס חוב להון עצמי, ומייצא Excel ו־CSV עם שדות המקור סמוך לחישובים.

## הרצה

```powershell
python run.py
```

ההגדרות נקראות מ־`config.yaml` ומ־`.env`. המטמון המקומי נמצא תחת `data/raw` ומונע בקשות API חוזרות בזמן פיתוח.

## נוסחאות פעילות

NOPAT ו־ROIC משתמשים בנתוני הרבעון הנוכחי. מכפיל EV/FCF משתמש ב־FCF של ארבעת הרבעונים האחרונים, בהתאם למתודולוגיית GuruFocus.

```text
calc_tax_expense_quarterly = -tax_provision

calc_raw_tax_rate_quarterly =
    calc_tax_expense_quarterly / pretax_income

calc_nopat_quarterly =
    ebit * (1 - calc_raw_tax_rate_quarterly)

calc_ic_raw =
    total_current_assets - total_current_liabilities + net_ppe + goodwill

calc_average_ic_raw_quarterly =
    (calc_ic_raw[t-1] + calc_ic_raw[t]) / 2

calc_roic_pretax_quarterly_ic_raw =
    ebit / calc_average_ic_raw_quarterly

calc_roic_posttax_quarterly_ic_raw =
    calc_nopat_quarterly / calc_average_ic_raw_quarterly

calc_debt_value_quarterly =
    short_term_debt_and_capital_lease + long_term_debt_and_capital_lease

equity =
    GuruFocus total_equity (normally total_assets - total_liabilities)

calc_debt_to_equity_quarterly =
    calc_debt_value_quarterly / total_stockholders_equity

calc_enterprise_value_quarterly =
    market_cap + calc_debt_value_quarterly - cash_and_cash_equivalents
    - short_term_investments

calc_free_cash_flow_ttm =
    free_cash_flow[t] + free_cash_flow[t-1]
    + free_cash_flow[t-2] + free_cash_flow[t-3]

calc_ev_to_fcf_quarterly =
    calc_enterprise_value_quarterly / calc_free_cash_flow_ttm
```

ממוצע IC מחושב רק כאשר הרשומה הקודמת היא הרבעון העוקב בפועל. מכנה אפס או חסר מחזיר ערך ריק. `tax_provision` שלילי ב־GuruFocus מייצג הוצאת מס חיובית, ולכן מתבצע היפוך סימן.

## נתונים מ־valuations API

העמודות הבאות נשלפות מה־endpoint ההיסטורי של valuations ומחוברות לפי תאריך סוף הרבעון:

- `market_cap`
- `valuations__valuation_ratios__enterprise_value_to_fcf`
- `valuations__per_share_data__shares_outstanding`
- `valuations__per_share_data__month_end_stock_price`
- `valuations__ratios__debt_to_equity`

השם המלא שומר בשקיפות את ה־section ואת מפתח ה־API המקורי.

## סדר עמודות Data

אחרי עמודות הזיהוי ועד `run_date`, הפלט מסודר כך:

```text
tax_provision
calc_tax_expense_quarterly
pretax_income
calc_raw_tax_rate_quarterly
ebit
calc_nopat_quarterly
total_current_assets
cash_and_cash_equivalents
short_term_investments
total_current_liabilities
short_term_debt
intangible_assets
goodwill
net_ppe
calc_ic_raw
calc_average_ic_raw_quarterly
calc_roic_pretax_quarterly_ic_raw
calc_roic_posttax_quarterly_ic_raw
short_term_debt_and_capital_lease
long_term_debt_and_capital_lease
calc_debt_value_quarterly
total_assets
total_liabilities
equity
total_stockholders_equity
calc_debt_to_equity_quarterly
valuations__ratios__debt_to_equity
market_cap
calc_debt_value_quarterly
cash_and_cash_equivalents
short_term_investments
calc_enterprise_value_quarterly
free_cash_flow
calc_free_cash_flow_ttm
calc_ev_to_fcf_quarterly
valuations__valuation_ratios__enterprise_value_to_fcf
valuations__per_share_data__shares_outstanding
valuations__per_share_data__month_end_stock_price
```

`calc_ev_to_fcf_quarterly` והמכפיל המדווח מה־API מוצגים באקסל כמספר רגיל (`0.00`) ללא הסיומת `x`.

כל שדות הנתונים הפיננסיים והמחושבים נשמרים בקוד כ־`float` ומוצגים ב־Excel
וב־CSV עם שתי ספרות אחרי הנקודה (`0.00`). שדות זיהוי, תאריכים ותוויות תקופה
נשמרים בפורמט הטבעי שלהם.

## בדיקות

```powershell
python -m pytest -q
```

הבדיקות מכסות את הנוסחאות, רציפות הרבעונים, מיפוי שדות ה־API, סדר העמודות ופורמט האקסל.
