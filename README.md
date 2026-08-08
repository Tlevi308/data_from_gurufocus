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

## פירוק השינוי הרבעוני ב־NOPAT וב־ROIC

`gurufocus/decomposition.py` מייחס את השינוי מרבעון לרבעון לגורמים שמאחוריו:
‏EBIT, שיעור המס, וההון המושקע. הייחוס נעשה בערך Shapley — ממוצע התרומה
השולית של כל גורם על פני כל סדרי השינוי האפשריים. זהו הייחוס היחיד שאינו תלוי
בסדר ושסכום התרומות בו שווה בדיוק לשינוי הכולל.

```text
E = ebit,  T = calc_raw_tax_rate_quarterly,  Q = 1 - T
I = calc_average_ic_raw_quarterly          ← נקרא כפי שהוא, ללא מיצוע נוסף

NOPAT = E * Q          ≡ calc_nopat_quarterly
ROIC  = E * Q / I      ≡ calc_roic_posttax_quarterly_ic_raw

# פירוק NOPAT (שני גורמים)
calc_nopat_ebit_contribution = (E1 - E0) * (Q0 + Q1) / 2
calc_nopat_tax_contribution  = (Q1 - Q0) * (E0 + E1) / 2

# פירוק ROIC (שלושה גורמים, משקלי Shapley 1/3 ו-1/6)
calc_roic_ebit_contribution = (E1 - E0) *
    ( (1/3)(Q0/I0) + (1/6)(Q1/I0) + (1/6)(Q0/I1) + (1/3)(Q1/I1) )
calc_roic_tax_contribution  = (Q1 - Q0) *
    ( (1/3)(E0/I0) + (1/6)(E1/I0) + (1/6)(E0/I1) + (1/3)(E1/I1) )
calc_roic_ic_contribution   = (1/I1 - 1/I0) *
    ( (1/3)(E0*Q0) + (1/6)(E1*Q0) + (1/6)(E0*Q1) + (1/3)(E1*Q1) )
```

המס נכנס כ־`Q = 1 - T` ולא כ־`T`, כדי ששני המדדים יישארו מכפלתיים גם כאשר
שיעור המס שלילי או גבוה מ־100%.

**אין מיצוע כפול.** `calc_average_ic_raw_quarterly` הוא כבר הממוצע של יתרות
הפתיחה והסגירה; הפירוק קורא את העמודה הזאת בשתי נקודות זמן ותו לא. גם השינויים
עצמם הם העמודות המפורסמות בהפרש (`.diff()`) ולא חישוב מחדש, כך שעמודת השארית
בודקת את הפירוק מול המספר שנכתב לאקסל.

### תנועה גולמית איננה השפעה כלכלית

`EBIT_UP` אינו זהה ל־`EBIT_EFFECT_POSITIVE`:

- כאשר `Q` שלילי (שיעור מס מעל 100%), עלייה ב־EBIT **מקטינה** את NOPAT.
- כאשר NOPAT שלילי, ירידה בהון המושקע הופכת את ROIC **לשלילי יותר**.

לכן כל עמודת `*_effect` נגזרת מסימן תרומת Shapley, וכל עמודת `*_movement`
מדווחת את הכיוון הגולמי. שתיהן נכתבות כדי שניתן יהיה להשוות ביניהן.

### ספי מהותיות

תרומה נחשבת `NEUTRAL`, ושינוי נחשב `STABLE`, כאשר `|x| <= tolerance`. השוואה
ישירה לאפס אינה תקפה: רעש של נקודה צפה היה מסווג שגיאת עיגול כאפקט אמיתי.
ברירות המחדל כלכליות ולא רק נומריות (‏10 נקודות בסיס ב־ROIC, ‏0.5% ב־NOPAT)
וניתנות לכוונון בבלוק `decomposition` שב־`config.yaml`.

### מקרי קצה

| מצב | התנהגות |
| --- | --- |
| אין רבעון קודם רציף | `UNCLASSIFIED_NONCONSECUTIVE`, כל המספרים ריקים, כל 63 עמודות הדמה 0 |
| ערך קלט חסר | `UNCLASSIFIED_MISSING_DATA` — אין השלמה ואין ייחוס |
| `I = 0` | `UNCLASSIFIED_ZERO_IC` ל־ROIC בלבד; פירוק NOPAT ממשיך לעבוד |
| `I < 0` | הפירוק מחושב, אך `calc_roic_economic_interpretation_valid = False` |
| `T < 0` או `T > 1` | הערך **אינו** נחתך; מסומן ב־`calc_tax_rate_quality_flag` |
| מעבר סימן כלשהו | נתמך — הכול הפרשים מוחלטים, אין שימוש באחוזי שינוי |

### העמודות שנוספו

107 עמודות: 44 עמודות ניתוח בשם מלא, ו־63 עמודות דמה (‏9 + 27 + 27) שנוצרות
במכפלה קרטזית ולכן מכסות כל שילוב אפשרי. בכל שורה מסווגת בדיוק עמודת דמה אחת
מכל משפחה מקבלת 1. הרשימה המלאה והמסודרת נמצאת ב־`decomposition_columns()`.

עמודות מפתח: `calc_roic_effect_structure`, `calc_roic_dominant_driver`,
`calc_roic_offset_ratio`, `calc_roic_business_classification`, ו־
`calc_roic_explanation` — משפט קצר באנגלית לכל תצפית, הנגזר מהתרומות ולא
מהתנועות הגולמיות.

## עלות ההון (WACC) והסינון ROIC > WACC

`gurufocus/wacc.py` מחשב אומדן אחיד של עלות ההון — לא הערכת שווי — כדי
שהתנאי `ROIC > WACC` יאמר את אותו דבר על פני כל המאגר.

```text
WACC = E/(D+E) × Re  +  D/(D+E) × Rd × (1−T)

E  = market_cap                             שווי שוק, לא הון עצמי חשבונאי
D  = calc_debt_value_quarterly              חוב קצר וארוך כולל חכירות
Re = Rf + ERP                               ללא CAPM וללא בטא
Rd = Σ(−interest_expense)[t−3..t] / ((D[t−4] + D[t]) / 2)
T  = Σ tax_expense[t−3..t] / Σ pretax_income[t−3..t],  אפס אם המכנה ≤ 0
```

‏Rf ו־ERP נקבעים בבלוק `wacc` שבראש `config.yaml` וזה המקום היחיד שנוגעים בו.
שניהם נכתבים גם כעמודות בכל שורה, כך שאפשר להשוות שתי ריצות ולראות מיד אם
ההפרש נובע מהנתונים או מההנחה.

### יישור יחידות

‏WACC הוא שיעור שנתי וה־ROIC בפרויקט רבעוני, ולכן שתי ההמרות נכתבות במפורש:

```text
calc_roic_*_annualized_ic_raw = (1 + ROIC רבעוני)^4 − 1
calc_wacc_quarterly           = (1 + WACC שנתי)^(1/4) − 1
```

שתיהן מוחזרות כ־`NaN` כשהבסיס `1 + r` אינו חיובי: בחזקה זוגית ROIC רבעוני של
‎−200% היה יוצא בדיוק 0%, ובשורש רביעי מספר שלילי אינו מוגדר כלל.

מכיוון שההיוון מונוטוני, `ROIC > WACC` נותן את אותה תשובה בשתי היחידות —
רק גודל הפער משתנה. יש בדיקה שמאמתת זאת בכל ריצה.

### דגל האיכות

`calc_wacc_quality_flag` **אינו משנה אף מספר**. ה־API מדווח `interest_expense`
כאפס בהרבה רבעונים גם כשקיים חוב מהותי — 48 מתוך 120 ב־INTC, ‏46 ב־JNJ, וחמשת
האחרונים באפל מול חוב של 84 מיליארד דולר. הערך נלקח כפי שהוא ו־`Rd = 0`, אבל
השורה מסומנת `DEBT_WITHOUT_INTEREST` כדי שתוכלו לסנן. בלי הדגל, `Rd = 0` של
אפל נראה זהה ל־`Rd = 0` אמיתי של חברה בלי חוב.

## נתוני סקטור ותעשייה מ־Profile API

לכל טיקר מתבצעת קריאה ל־`GET /stocks/{symbol}/profile`. השדות
`general.sector` ו־`general.industry` מועתקים לכל שורות התקופות של אותו טיקר
ומוצגים בתחילת הפלט בסדר: `symbol`, `company`, `sector`, `industry`.
תשובת ה־API נשמרת במטמון נפרד בשם `{symbol}__profile.json`.

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
<9 עמודות פירוק NOPAT — ראו NOPAT_BRIDGE_COLUMNS>
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
calc_roic_pretax_annualized_ic_raw
calc_roic_posttax_quarterly_ic_raw
calc_roic_posttax_annualized_ic_raw
<35 עמודות פירוק ROIC — ראו ROIC_BRIDGE_COLUMNS>
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
<19 עמודות WACC — ראו WACC_CORE_COLUMNS>
```

`calc_ev_to_fcf_quarterly` והמכפיל המדווח מה־API מוצגים באקסל כמספר רגיל (`0.00`) ללא הסיומת `x`.

63 עמודות הדמה של הפירוק אינן נכתבות לגיליון `Data` אלא לגיליון `Decomposition`
נפרד, מזוהות לפי `symbol` ו־`period_key`. ב־CSV וב־parquet הן נשארות במקומן.

כל שדות הנתונים הפיננסיים והמחושבים נשמרים בקוד כ־`float`. תצוגת ברירת המחדל
היא שתי ספרות (`0.00`), עם שני חריגים:

- **תרומות, משקלים ושאריות** מוצגות בשש ספרות (`0.000000`) — תרומת ROIC של 31
  נקודות בסיס הייתה נראית כאפס בפורמט הרגיל. ב־CSV הן נכתבות בדיוק מלא, כי
  `float_format` חל על הקובץ כולו.
- **עמודות הסיווג** הן טקסט ובוליאני ומוחרגות מהמרת ה־float. בלי ההחרגה הזאת
  כל תווית הייתה הופכת ל־`NaN` בשקט והפלט היה נראה תקין אך ריק.

שדות זיהוי, תאריכים ותוויות תקופה נשמרים בפורמט הטבעי שלהם.

## בדיקות

```powershell
python -m pytest -q
```

הבדיקות מכסות את הנוסחאות, רציפות הרבעונים, מיפוי שדות ה־API, סדר העמודות ופורמט האקסל.

`tests/test_decomposition.py` מכסה בנוסף את 25 תרחישי הפירוק, ובכללם המקרים
הנוגדים את האינטואיציה (ירידה בהון מושקע כשה־NOPAT שלילי, עלייה ב־EBIT כששיעור
המס מעל 100%), ומאמת שלושה מאפיינים מבניים: סכום התרומות שווה בדיוק לשינוי
הכולל, בכל שורה מסווגת נדלקת בדיוק עמודת דמה אחת מכל משפחה, והפירוק הווקטורי
זהה לממוצע על פני ששת סדרי השינוי המחושב ישירות ב־`itertools.permutations`.

את שלוש הזהויות האלה בודק גם גיליון `Checks` בכל ריצה על נתונים אמיתיים.
