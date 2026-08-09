# מילון השדות — פאנל רבעוני

תיעוד מלא של כל עמודה בפלט: מה היא אומרת, איך היא מחושבת, ואילו ערכים היא
יכולה לקבל.

**סה"כ 174 עמודות** בפאנל הרבעוני:

| קבוצה | כמות | מקור |
| --- | --- | --- |
| זיהוי ומבנה | 10 | `profile` API + פירוק התאריך |
| שדות גולמיים | 19 | `fundamentals` API |
| שדות valuations | 5 | `valuations` API |
| חישובים בסיסיים | 12 | `gurufocus/calculations.py` |
| פירוק NOPAT | 9 | `gurufocus/decomposition.py` |
| פירוק ROIC | 35 | `gurufocus/decomposition.py` |
| עלות ההון (WACC) | 21 | `gurufocus/wacc.py` |
| עמודות דמה | 63 | `gurufocus/decomposition.py` |

**מוסכמות כלליות**

- כל הערכים הכספיים ביחידות הדיווח של ה-API (עבור AAPL — מיליוני דולר).
- ערך חסר הוא תמיד `NaN` ריק, לעולם לא 0. מכנה אפס או חסר מחזיר `NaN`.
- אין עיגול בשום שלב — לא בחישוב ולא בכתיבה. העיגול קיים רק בתצוגת האקסל:
  `0.00` ברוב העמודות, `0.000000` בתרומות, במשקלים ובשיעורי הריבית — תרומה
  של 31 נקודות בסיס ועלות חוב של 2.7% היו מוצגות כאפס בפורמט הרגיל. התא
  עצמו מחזיק את הערך המלא.
- **קובץ ה-CSV זהה לגיליון `Data` שבאקסל**: אותן עמודות, אותו סדר ואותם
  ערכים. שניהם נכתבים מאותה טבלה, ו-63 עמודות הדמה (פרק 8) מופרדות משניהם
  אל גיליון `Decomposition` ואל פלט ה-parquet.
  שים לב שב-CSV הערך נכתב בדיוק מלא (`313.3299865722656`) ואילו אקסל *מציג*
  `313.33` — אותו מספר, תצוגה שונה. פורמט xlsx אף שומר 16 ספרות מובהקות ולא
  17, ולכן ה-CSV הוא למעשה הצד המדויק מבין השניים; ההפרש הוא ULP בודד,
  בסדר גודל של ‎1e-16.
- ⚠️ **בקריאת ה-CSV ב-pandas השתמשו ב-`float_precision="round_trip"`.**
  הפרסר המהיר שהוא ברירת המחדל של `read_csv` אינו מעוגל נכון ומחזיר כ-13
  ספרות מובהקות בלבד — סטייה של ‎1e-13‎ יחסית שאינה קיימת בקובץ עצמו:

  ```python
  pd.read_csv(path, encoding="utf-8-sig", float_precision="round_trip")
  ```
- שתי קבוצות ערכים אינן מגיעות מה-API אלא מקובץ ההגדרות: ספי המהותיות של
  הפירוק (נספח א) ו-`Rf`/`ERP` של עלות ההון (פרק 7.2). שתיהן נכתבות כעמודות
  בכל שורה, כדי שכל תצפית תתעד את ההנחה שלפיה חושבה.
- כל ההשוואות בין רבעונים נעשות מול הרבעון הפיסקלי הקודם **ברצף** בלבד.

---

## 1. עמודות זיהוי ומבנה

| עמודה | טיפוס | משמעות | ערכים אפשריים |
| --- | --- | --- | --- |
| `symbol` | טקסט | הטיקר. נכפה מהבקשה ולא נלקח מה-API, כדי שכל שורות הריצה יישאו את הסמל שביקשנו | `AAPL`, `MSFT`, … |
| `company` | טקסט | שם החברה מ-`basic_information` | `Apple Inc` |
| `sector` | טקסט | סקטור מ-endpoint‏ `profile`, סעיף `general` | `Technology`, `Healthcare`, … · ריק אם ה-API לא החזיר |
| `industry` | טקסט | ענף מ-endpoint‏ `profile`, סעיף `general` | `Consumer Electronics`, … · ריק אם ה-API לא החזיר |
| `fiscal_period_end_date` | טקסט `YYYY-MM-DD` | תאריך סוף התקופה הפיסקלית. ה-API מחזיר `YYYY-MM` והפירוק ממיר לסוף החודש | `2026-06-30` |
| `filing_date` | טקסט `YYYY-MM-DD` | תאריך ההגשה לרשות | ריק כשה-API לא דיווח |
| `period_key` | טקסט | מפתח התקופה אחרי יישור | `2026Q1` … |
| `period_year` | מספר שלם | שנת התקופה אחרי יישור | 2000–2026 |
| `period_quarter` | טקסט | הרבעון אחרי יישור | `Q1` · `Q2` · `Q3` · `Q4` |
| `run_date` | טקסט `YYYY-MM-DD` | תאריך הרצת הפייפליין | — |

**היישור** (`alignment.quarter_shift_months`, ברירת מחדל 2): מסיטים את סוף
התקופה אחורה בשני חודשים ולוקחים את הרבעון הקלנדרי של התוצאה, כך שכל רבעון
פיסקלי משויך לרבעון של החודש הראשון שהוא מכסה. למשל 30/09 ← יולי ← `Q3`.

הכלל מוגדר פעם אחת ב-[`gurufocus/alignment.py`](../gurufocus/alignment.py) כדי
שיהיה לו מקור אמת יחיד: `period_key` הוא מפתח החיבור של הפאנל, וכלל מיפוי
מקביל שסוטה ממנו היה מחזיר חיבור ריק בלי שום שגיאה.

---

## 2. שדות גולמיים מ-fundamentals

כולם מספריים (`float`), ביחידות הדיווח של ה-API. עמודה שה-API לא החזיר תהיה
`NaN` לכל אורך הסדרה — היא לא נופלת בשגיאה.

### מאזן — נכסים

| עמודה | מפתח ב-API | משמעות | ערכים |
| --- | --- | --- | --- |
| `total_current_assets` | `total_current_assets` | סך הנכסים השוטפים | בדרך כלל חיובי |
| `cash_and_cash_equivalents` | `cash_and_cash_equivalents` | מזומן ושווי מזומן | ‎≥ 0 |
| `short_term_investments` | `marke_table_securities` | ניירות ערך סחירים לזמן קצר | ‎≥ 0 |
| `net_ppe` | `net_ppe` | רכוש קבוע מוחשי נטו. **אינו** כולל מוניטין | ‎≥ 0 |
| `goodwill` | `good_will` | מוניטין | ‎≥ 0 |
| `intangible_assets` | `intangibles` | נכסים בלתי מוחשיים | ‎≥ 0 |
| `total_assets` | `total_assets` | סך הנכסים | חיובי |

### מאזן — התחייבויות והון

| עמודה | מפתח ב-API | משמעות | ערכים |
| --- | --- | --- | --- |
| `total_current_liabilities` | `total_current_liabilities` | סך ההתחייבויות השוטפות | ‎≥ 0 |
| `short_term_debt` | `short_term_debt` | חוב לזמן קצר, ללא חכירות | ‎≥ 0 |
| `short_term_debt_and_capital_lease` | `short_term_debt_and_capital_lease_obligation` | חוב קצר **כולל** התחייבויות חכירה | ‎≥ 0 |
| `long_term_debt_and_capital_lease` | `long_term_debt_and_capital_lease_obligation` | חוב ארוך **כולל** התחייבויות חכירה | ‎≥ 0 |
| `total_liabilities` | `total_liabilities` | סך ההתחייבויות | חיובי |
| `equity` | `total_equity` | הון עצמי כולל | יכול להיות שלילי |
| `total_stockholders_equity` | `total_stockholders_equity` | הון עצמי המיוחס לבעלי המניות | יכול להיות שלילי |

### דוח רווח והפסד ותזרים

| עמודה | מפתח ב-API | משמעות | ערכים |
| --- | --- | --- | --- |
| `ebit` | `ebit` | רווח תפעולי לפני ריבית ומס, **לרבעון בודד** | חיובי או שלילי |
| `pretax_income` | `pretax_income` | רווח לפני מס לרבעון | חיובי או שלילי |
| `tax_provision` | `tax_provision` | הפרשה למס. ⚠️ **GuruFocus מדווח הוצאת מס בסימן שלילי** | בדרך כלל שלילי; חיובי = הטבת מס |
| `interest_expense` | `interest_expense` | הוצאות ריבית לרבעון. ⚠️ **מדווח בסימן שלילי**, כמו `tax_provision`. ⚠️ **מדווח כאפס בהרבה רבעונים גם כשקיים חוב** — ראו פרק 7 | בדרך כלל שלילי או אפס; חיובי = הכנסות ריבית נטו |
| `free_cash_flow` | `total_free_cash_flow` | תזרים מזומנים חופשי לרבעון | חיובי או שלילי |

---

## 3. שדות מ-valuations API

מחוברים לפי תאריך סוף הרבעון. שם העמודה שומר בשקיפות את ה-section ואת מפתח
ה-API המקורי.

| עמודה | section / key | משמעות | ערכים |
| --- | --- | --- | --- |
| `market_cap` | `valuationand_quality.mktcap` | שווי שוק לסוף הרבעון | חיובי |
| `valuations__valuation_ratios__enterprise_value_to_fcf` | `valuation_ratios.enterprise_value_to_fcf` | מכפיל EV/FCF **כפי שמדווח על ידי GuruFocus** — לצורך השוואה מול החישוב שלנו | חיובי או שלילי |
| `valuations__per_share_data__shares_outstanding` | `per_share_data.shares_outstanding` | מספר מניות במחזור | חיובי |
| `valuations__per_share_data__month_end_stock_price` | `per_share_data.month_end_stock_price` | מחיר המניה לסוף החודש | חיובי |
| `valuations__ratios__debt_to_equity` | `ratios.debt_to_equity` | יחס חוב להון **כפי שמדווח על ידי GuruFocus** | ‎≥ 0 |

---

## 4. חישובים בסיסיים

מקור: [`gurufocus/calculations.py`](../gurufocus/calculations.py)

| עמודה | חישוב | משמעות וערכים |
| --- | --- | --- |
| `calc_tax_expense_quarterly` | `-tax_provision` | הוצאת המס לרבעון בסימן חיובי טבעי. היפוך הסימן נדרש כי GuruFocus מדווח הוצאה כשלילית |
| `calc_raw_tax_rate_quarterly` | `calc_tax_expense_quarterly / pretax_income` | שיעור המס האפקטיבי לרבעון. **אינו נחתך לטווח [0,1]** — יכול לצאת שלילי (הטבת מס) או מעל 1. `NaN` כאשר `pretax_income = 0` |
| `calc_nopat_quarterly` | `ebit * (1 - calc_raw_tax_rate_quarterly)` | רווח תפעולי נקי אחרי מס. חיובי או שלילי |
| `calc_ic_raw` | `total_current_assets - total_current_liabilities + net_ppe + goodwill` | הון מושקע ליום המאזן. `net_ppe` אינו כולל מוניטין ולכן המוניטין נוסף במפורש. **יכול לצאת שלילי** כשההתחייבויות השוטפות גדולות מהנכסים |
| `calc_average_ic_raw_quarterly` | `(calc_ic_raw[t-1] + calc_ic_raw[t]) / 2` | ממוצע יתרת פתיחה וסגירה. מחושב **רק** כשהרשומה הקודמת היא הרבעון הקודם ברצף, אחרת `NaN` |
| `calc_roic_pretax_quarterly_ic_raw` | `ebit / calc_average_ic_raw_quarterly` | תשואה על ההון המושקע לפני מס. **רבעונית** — מונה של רבעון בודד על יתרה ממוצעת. התאום השנתי שלה הוא `calc_roic_pretax_annualized_ic_raw` שבפרק 7 |
| `calc_roic_posttax_quarterly_ic_raw` | `calc_nopat_quarterly / calc_average_ic_raw_quarterly` | תשואה על ההון המושקע אחרי מס. זו העמודה שהפירוק בפרק 6 מסביר, ומולה נבחן הסינום מול WACC בפרק 7 |
| `calc_debt_value_quarterly` | `short_term_debt_and_capital_lease + long_term_debt_and_capital_lease` | ערך החוב הכולל בספרים, כולל התחייבויות חכירה |
| `calc_debt_to_equity_quarterly` | `calc_debt_value_quarterly / total_stockholders_equity` | יחס חוב להון. `NaN` כשההון אפס; שלילי כשההון שלילי |
| `calc_enterprise_value_quarterly` | `market_cap + calc_debt_value_quarterly - cash_and_cash_equivalents - short_term_investments` | שווי פעילות לסוף הרבעון |
| `calc_free_cash_flow_ttm` | `Σ free_cash_flow[t-3 … t]` | תזרים חופשי בארבעת הרבעונים האחרונים. מחושב **רק** כשכל ארבעת הרבעונים רצופים, אחרת `NaN` |
| `calc_ev_to_fcf_quarterly` | `calc_enterprise_value_quarterly / calc_free_cash_flow_ttm` | מכפיל: EV לסוף הרבעון חלקי FCF שנתי. **שלילי כש-FCF שלילי** — אז המכפיל חסר משמעות. `NaN` כש-FCF אפס |

---

## 5. פירוק השינוי ב-NOPAT

מקור: [`gurufocus/decomposition.py`](../gurufocus/decomposition.py)

NOPAT מושפע **משני גורמים בלבד**: EBIT ושיעור המס. ההון המושקע אינו משפיע
עליו. סימונים: `E = ebit`, `T = calc_raw_tax_rate_quarterly`, `Q = 1 - T`,
כאשר 0 = הרבעון הקודם ו-1 = הרבעון הנוכחי.

| עמודה | חישוב | ערכים אפשריים |
| --- | --- | --- |
| `calc_nopat_decomposition_status` | קובע האם ניתן להשוות לרבעון הקודם | `VALID` · `UNCLASSIFIED_NONCONSECUTIVE` (אין רבעון קודם ברצף) · `UNCLASSIFIED_MISSING_DATA` (אחד מ-`E0,E1,T0,T1` חסר) |
| `calc_nopat_change_quarterly` | `calc_nopat_quarterly.diff()` | ההפרש המוחלט. `NaN` כשהסטטוס אינו `VALID` |
| `calc_nopat_change_direction` | לפי הסף בפרק 8 | `INCREASE` · `DECREASE` · `STABLE` · `UNCLASSIFIED` |
| `calc_nopat_ebit_contribution` | `(E1 - E0) · (Q0 + Q1) / 2` | תרומת EBIT לשינוי, בערכי כסף |
| `calc_nopat_tax_contribution` | `(Q1 - Q0) · (E0 + E1) / 2` | תרומת שיעור המס לשינוי, בערכי כסף |
| `calc_nopat_decomposition_residual` | `Δ − C_ebit − C_tax` | שארית בדיקה. **אפס עד כדי דיוק מספרי** (‎~1e-13 בפועל) |
| `calc_nopat_ebit_effect` | סימן `calc_nopat_ebit_contribution` | `POSITIVE` · `NEGATIVE` · `NEUTRAL` · `UNCLASSIFIED` |
| `calc_nopat_tax_effect` | סימן `calc_nopat_tax_contribution` | אותם ארבעה ערכים |
| `calc_nopat_effect_combination` | שרשור שני הסימנים | אחד מ-**9**: `EBIT_{POS\|NEG\|ZERO}__TAX_{POS\|NEG\|ZERO}`, או `UNCLASSIFIED` |

> **חשוב:** `calc_nopat_ebit_effect` אינו זהה לכיוון התנועה של EBIT. כאשר
> שיעור המס מעל 100% ‏(`Q < 0`), **עלייה** ב-EBIT **מקטינה** את NOPAT ולכן
> התרומה שלילית.

---

## 6. פירוק השינוי ב-ROIC אחרי מס

ROIC מושפע **משלושה גורמים**: EBIT, שיעור המס, וההון המושקע.
`I = calc_average_ic_raw_quarterly`, נקרא כפי שהוא בשתי נקודות זמן וללא
מיצוע נוסף.

### 6.1 סטטוס והשינוי

| עמודה | חישוב | ערכים אפשריים |
| --- | --- | --- |
| `calc_roic_decomposition_status` | סדר הבדיקה: רציפות ← נתון חסר ← הון אפס | `VALID` · `UNCLASSIFIED_NONCONSECUTIVE` · `UNCLASSIFIED_MISSING_DATA` · `UNCLASSIFIED_ZERO_IC` |
| `calc_roic_posttax_change_quarterly` | `calc_roic_posttax_quarterly_ic_raw.diff()` | הפרש היחס. `NaN` כשהסטטוס אינו `VALID` |
| `calc_roic_posttax_change_direction` | לפי הסף בפרק 8 | `INCREASE` · `DECREASE` · `STABLE` · `UNCLASSIFIED` |

> שורה מסווגת דורשת **שלושה** רבעונים רצופים: `I0` הוא עמודת הממוצע הקיימת
> ברבעון הקודם, והיא עצמה ריקה מיד אחרי פער. זו תכונה של עמודת ה-ROIC
> הקיימת, לא של הפירוק.

### 6.2 שלוש התרומות

| עמודה | חישוב | ערכים |
| --- | --- | --- |
| `calc_roic_ebit_contribution` | `(E1−E0) · [ ⅓(Q0/I0) + ⅙(Q1/I0) + ⅙(Q0/I1) + ⅓(Q1/I1) ]` | ביחידות ROIC (יחס) |
| `calc_roic_tax_contribution` | `(Q1−Q0) · [ ⅓(E0/I0) + ⅙(E1/I0) + ⅙(E0/I1) + ⅓(E1/I1) ]` | ביחידות ROIC |
| `calc_roic_ic_contribution` | `(1/I1 − 1/I0) · [ ⅓(E0·Q0) + ⅙(E1·Q0) + ⅙(E0·Q1) + ⅓(E1·Q1) ]` | ביחידות ROIC |
| `calc_roic_decomposition_residual` | `ΔROIC − C_ebit − C_tax − C_ic` | **אפס עד כדי דיוק מספרי** (‎~1e-17 בפועל) |

המשקלים ⅓ ו-⅙ הם משקלי Shapley: ממוצע התרומה השולית של כל גורם על פני כל
ששת סדרי השינוי האפשריים. זה הייחוס היחיד שאינו תלוי בסדר ושסכום התרומות בו
שווה **בדיוק** לשינוי הכולל.

### 6.3 סימני ההשפעה

| עמודה | חישוב | ערכים |
| --- | --- | --- |
| `calc_roic_ebit_effect` | סימן `calc_roic_ebit_contribution` | `POSITIVE` · `NEGATIVE` · `NEUTRAL` · `UNCLASSIFIED` |
| `calc_roic_tax_effect` | סימן `calc_roic_tax_contribution` | אותם ערכים |
| `calc_roic_ic_effect` | סימן `calc_roic_ic_contribution` | אותם ערכים |
| `calc_roic_effect_combination` | שרשור שלושת הסימנים | אחד מ-**27**: `EBIT_x__TAX_y__IC_z` כאשר x,y,z ∈ {`POS`,`NEG`,`ZERO`}, או `UNCLASSIFIED` |

> **חשוב:** ירידה בהון המושקע אינה תמיד השפעה חיובית. כאשר NOPAT שלילי,
> מכנה קטן יותר הופך את ROIC **לשלילי יותר** ולכן התרומה שלילית.

### 6.4 תנועה גולמית של המשתנים

עמודות אלה מדווחות את **כיוון השינוי** של המשתנה עצמו, בניגוד לעמודות
ה-`effect` שמדווחות את ההשפעה על ROIC. שתיהן קיימות כדי שניתן יהיה להשוות.

| עמודה | חישוב | ערכים |
| --- | --- | --- |
| `calc_ebit_movement` | סימן `E1 − E0` | `UP` · `DOWN` · `FLAT` · `UNCLASSIFIED` |
| `calc_tax_rate_movement` | סימן `T1 − T0` — של **שיעור המס** `T`, לא של `Q` | `UP` · `DOWN` · `FLAT` · `UNCLASSIFIED` · ירידה בשיעור המס היא `DOWN` גם כשהיא מגדילה את ROIC |
| `calc_ic_movement` | סימן `I1 − I0` | `UP` · `DOWN` · `FLAT` · `UNCLASSIFIED` |
| `calc_raw_movement_combination` | שרשור שלוש התנועות | אחד מ-**27**: `EBIT_x__TAX_y__IC_z` כאשר x,y,z ∈ {`UP`,`DOWN`,`FLAT`}, או `UNCLASSIFIED` |

`calc_ebit_movement` ו-`calc_tax_rate_movement` תלויים בסטטוס ה-NOPAT בלבד,
ולכן שורדים גם כשההון המושקע אפס או חסר. `calc_ic_movement` תלוי בסטטוס
ה-ROIC.

### 6.5 מבנה ההשפעות

| עמודה | חישוב | ערכים |
| --- | --- | --- |
| `calc_roic_has_opposing_effects` | קיימת תרומה חיובית **וגם** תרומה שלילית | `True` · `False` · ריק כשהסטטוס אינו `VALID` |
| `calc_roic_positive_driver_count` | ספירת התרומות החיוביות | 0 · 1 · 2 · 3 |
| `calc_roic_negative_driver_count` | ספירת התרומות השליליות | 0 · 1 · 2 · 3 |
| `calc_roic_neutral_driver_count` | ספירת התרומות בתוך סף המהותיות | 0 · 1 · 2 · 3 |
| `calc_roic_active_driver_count` | חיוביות + שליליות | 0 · 1 · 2 · 3 |
| `calc_roic_effect_structure` | סולם עדיפויות, ראו פרק 9 | 9 ערכים — ראו למטה |

ערכי `calc_roic_effect_structure`:

| ערך | מתי |
| --- | --- |
| `NO_MATERIAL_CHANGE` | כל שלוש התרומות ניטרליות |
| `SINGLE_POSITIVE_DRIVER` | גורם אחד חיובי, השאר ניטרליים |
| `ALL_POSITIVE` | כל התרומות הפעילות חיוביות (שתיים או שלוש) |
| `SINGLE_NEGATIVE_DRIVER` | גורם אחד שלילי, השאר ניטרליים |
| `ALL_NEGATIVE` | כל התרומות הפעילות שליליות |
| `MIXED_NET_INCREASE` | תרומות מנוגדות ו-ROIC עלה |
| `MIXED_NET_DECREASE` | תרומות מנוגדות ו-ROIC ירד |
| `MIXED_FULL_OFFSET` | תרומות מנוגדות ו-ROIC יציב |
| `UNCLASSIFIED` | הסטטוס אינו `VALID` |

### 6.6 דומיננטיות וקיזוז

| עמודה | חישוב | ערכים |
| --- | --- | --- |
| `calc_roic_total_absolute_contribution` | `\|C_ebit\| + \|C_tax\| + \|C_ic\|` | ‎≥ 0 |
| `calc_roic_ebit_absolute_share` | `\|C_ebit\| / TotalAbs` | ‎[0, 1] · `NaN` כשכל התרומות ניטרליות |
| `calc_roic_tax_absolute_share` | `\|C_tax\| / TotalAbs` | ‎[0, 1] |
| `calc_roic_ic_absolute_share` | `\|C_ic\| / TotalAbs` | ‎[0, 1] · שלושת המשקלים מסתכמים ל-1 |
| `calc_roic_dominant_driver` | הגורם בעל התרומה המוחלטת הגדולה ביותר | `EBIT` · `TAX` · `IC` · `BALANCED` · `NONE` · `UNCLASSIFIED` |
| `calc_roic_dominant_driver_effect` | סימן התרומה של הגורם הדומיננטי | `POSITIVE` · `NEGATIVE` · `NEUTRAL` (כש-`NONE`) · `UNCLASSIFIED` (כש-`BALANCED`) |
| `calc_roic_offset_ratio` | `1 − \|ΔROIC\| / TotalAbs` | ‎[0, 1] · `NaN` כשאין תרומה מהותית |

**משקל מוחלט ולא `C_i / ΔROIC`** — כשיש השפעות מנוגדות היחס הרגיל יוצא שלילי
או גדול מ-100% ולא ניתן לדרג לפיו.

**`calc_roic_offset_ratio`:** קרוב ל-0 = התרומות פעלו באותו כיוון; קרוב ל-1 =
הן קיזזו זו את זו. אי-שוויון המשולש מבטיח שהערך בתחום ‎[0, 1].

**`BALANCED`** נקבע כשההפרש בין שני המשקלים הגדולים אינו עולה על
`dominance` (ברירת מחדל 0.05 = חמש נקודות אחוז). **`NONE`** — כל התרומות
ניטרליות.

### 6.7 מצב רווחיות ואיכות נתונים

| עמודה | חישוב | ערכים |
| --- | --- | --- |
| `calc_ebit_sign_regime` | מעבר הסימן של EBIT בין הרבעונים | 9 שילובים של `{PROFIT,LOSS,ZERO}_TO_{PROFIT,LOSS,ZERO}` + `UNCLASSIFIED` |
| `calc_nopat_sign_regime` | מעבר הסימן של NOPAT | אותם 10 ערכים |
| `calc_tax_rate_quality_flag` | תקינות שיעור המס **ברבעון הנוכחי** | `VALID` · `NEGATIVE_TAX_RATE` · `ABOVE_100_PERCENT` · `MISSING` |
| `calc_roic_quality_flag` | תקינות הזוג להשוואה | `VALID` · `NEGATIVE_IC_MECHANICAL_ONLY` · `TAX_RATE_OUT_OF_RANGE` · `UNCLASSIFIED` |
| `calc_roic_economic_interpretation_valid` | `calc_roic_quality_flag == VALID` | `True` · `False` · ריק כשהסטטוס אינו `VALID` |

שיעור מס מחוץ לטווח **אינו נחתך** — הוא מחושב כפי שדווח ורק מסומן. הון מושקע
שלילי מייצר יחס שניתן לחשב אך אין לקרוא אותו כיעילות כלכלית.

### 6.8 סיווג והסבר

| עמודה | טיפוס | משמעות |
| --- | --- | --- |
| `calc_roic_business_classification` | טקסט | סיווג עסקי מסכם, אחד מ-**35** ערכים. ממצה — אף שורה אינה נופלת מחוץ לסולם |
| `calc_roic_explanation` | טקסט | משפט קצר באנגלית לכל תצפית, נגזר מהתרומות ולא מהתנועות הגולמיות |

35 הערכים של `calc_roic_business_classification`:

**לא ניתן להשוות (3)**
`UNCLASSIFIED_NONCONSECUTIVE` · `UNCLASSIFIED_MISSING_DATA` ·
`UNCLASSIFIED_ZERO_IC`

**מצבים שבהם היחס מכני ולא כלכלי (5)**
`ROIC_MECHANICAL_NEGATIVE_IC` — הון מושקע שלילי
`OPERATING_TURNAROUND` — NOPAT עבר מהפסד לרווח
`PROFIT_TO_LOSS_DETERIORATION` — NOPAT עבר מרווח להפסד
`MECHANICAL_IMPROVEMENT_WITH_NEGATIVE_NOPAT` — ROIC עלה אך NOPAT נותר שלילי
`MECHANICAL_DETERIORATION_WITH_NEGATIVE_NOPAT` — ROIC ירד ו-NOPAT שלילי

**יציב (2)**
`ROIC_STABLE_NO_CHANGE` · `ROIC_STABLE_OFFSETTING_EFFECTS`

**עלייה (13)**
`ROIC_INCREASE_ALL_DRIVERS_POSITIVE`
`ROIC_INCREASE_EBIT_DOMINANT_WITH_TAX_AND_IC_SUPPORT`
`ROIC_INCREASE_EBIT_DOMINANT_WITH_TAX_DRAG`
`ROIC_INCREASE_EBIT_DOMINANT_WITH_IC_DRAG`
`ROIC_INCREASE_EBIT_DOMINANT_WITH_TAX_AND_IC_DRAG`
`ROIC_INCREASE_TAX_DOMINANT_WITH_EBIT_DECLINE`
`ROIC_INCREASE_TAX_DOMINANT_WITH_IC_DRAG`
`ROIC_INCREASE_IC_DOMINANT_WITH_EBIT_DECLINE`
`ROIC_INCREASE_IC_DOMINANT_WITH_TAX_DRAG`
`ROIC_INCREASE_DESPITE_DOMINANT_EBIT_DRAG`
`ROIC_INCREASE_DESPITE_DOMINANT_TAX_DRAG`
`ROIC_INCREASE_DESPITE_DOMINANT_IC_DRAG`
`ROIC_INCREASE_MIXED_EFFECTS`

**ירידה (11)**
`ROIC_DECREASE_ALL_DRIVERS_NEGATIVE`
`ROIC_DECREASE_EBIT_DOMINANT_DESPITE_TAX_SUPPORT`
`ROIC_DECREASE_EBIT_DOMINANT_DESPITE_IC_SUPPORT`
`ROIC_DECREASE_TAX_DOMINANT_DESPITE_EBIT_IMPROVEMENT`
`ROIC_DECREASE_TAX_DOMINANT_DESPITE_IC_SUPPORT`
`ROIC_DECREASE_IC_DOMINANT_DESPITE_EBIT_IMPROVEMENT`
`ROIC_DECREASE_IC_DOMINANT_DESPITE_TAX_SUPPORT`
`ROIC_DECREASE_DESPITE_DOMINANT_EBIT_SUPPORT`
`ROIC_DECREASE_DESPITE_DOMINANT_TAX_SUPPORT`
`ROIC_DECREASE_DESPITE_DOMINANT_IC_SUPPORT`
`ROIC_DECREASE_MIXED_EFFECTS`

**ברירת מחדל (1)**
`UNCLASSIFIED`

ה-`DESPITE_DOMINANT_*` מכסים את המקרה שבו התרומה הגדולה ביותר מושכת לכיוון
ההפוך מהשינוי — למשל ROIC עלה בזמן ש-EBIT היה התורם הגדול ביותר והוא שלילי.
בלעדיהם השורה הייתה מסווגת כאילו EBIT הוביל את העלייה, וזה היפוך של הסיפור.

---

## 7. עלות ההון (WACC)

מקור: [`gurufocus/wacc.py`](../gurufocus/wacc.py)

זו **אינה** הערכת שווי. זהו אומדן אחיד של עלות ההון, מחושב באותה דרך לכל חברה
ולכל רבעון, כדי שהסינון `ROIC > WACC` יאמר את אותו דבר על פני כל המאגר.

```text
WACC = E/(D+E) × Re  +  D/(D+E) × Rd × (1−T)

E  = שווי שוק, לא הון עצמי חשבונאי
D  = חוב נושא ריבית, כולל התחייבויות חכירה
Re = Rf + ERP                          ללא CAPM וללא בטא
Rd = ריבית TTM / חוב נושא ריבית ממוצע
T  = הוצאת מס TTM / רווח לפני מס TTM, עם רצפה באפס
```

### 7.1 יישור יחידות — למה יש עמודות "annualized"

‏Re, ‏Rd ולכן גם WACC הם שיעורים **שנתיים**. עמודות ה־ROIC בפרויקט הן רבעוניות.
השוואה ישירה ביניהן הייתה מציגה חברה עם 8% ROIC שנתי כ־2% ומפילה אותה מול
WACC של 7.25%.

לכן לכל ROIC רבעוני יש תאום שנתי, ול־WACC יש תאום רבעוני:

| עמודה | חישוב | ערכים |
| --- | --- | --- |
| `calc_roic_pretax_annualized_ic_raw` | `(1 + calc_roic_pretax_quarterly_ic_raw)^4 − 1` | `NaN` כאשר היחס הרבעוני ‎≤ ‎−100% |
| `calc_roic_posttax_annualized_ic_raw` | `(1 + calc_roic_posttax_quarterly_ic_raw)^4 − 1` | `NaN` כאשר היחס הרבעוני ‎≤ ‎−100% |
| `calc_wacc_quarterly` | `(1 + calc_wacc_annual)^(1/4) − 1` | `NaN` כאשר WACC השנתי ‎≤ ‎−100% |

**ההגנה הזאת הכרחית.** בחזקה זוגית, ROIC רבעוני של ‎−200% היה יוצא בדיוק 0%
ו־‎−150% היה נראה טוב יותר מ־‎−100%. בשורש רביעי, מספר שלילי אינו מוגדר בממשיים
כלל.

**פסק הדין זהה בשתי היחידות.** הפונקציה `x → (1+x)^4 − 1` עולה ממש, ולכן
`ROIC_annual > WACC_annual` אם ורק אם `ROIC_quarterly > WACC_quarterly`. רק גודל
הפער משתנה. יש בדיקה אוטומטית שמאמתת זאת בכל ריצה.

### 7.2 ההנחות

שתי ההנחות היחידות שאינן נגזרות מהדוחות הכספיים. נכתבות כעמודות בכל שורה,
כדי שכל תצפית תתעד את ההנחה שלפיה תומחרה.

| עמודה | חישוב | ערכים |
| --- | --- | --- |
| `calc_wacc_risk_free_rate` | ‎`Rf` מקובץ ההגדרות | ברירת מחדל 0.0425 = 4.25% |
| `calc_wacc_equity_risk_premium` | ‎`ERP` מקובץ ההגדרות | ברירת מחדל 0.0300 = 3.00% |
| `calc_wacc_cost_of_equity` | `Rf + ERP` | ברירת מחדל 0.0725 = 7.25% |

**איפה קובעים אותן.** בבלוק `wacc` שבראש [`config.yaml`](../config.yaml), בחלק
"מה שמשנים ביום-יום". זה המקום היחיד — אין ערכים קשיחים בקוד:

```yaml
wacc:
  # הריבית חסרת הסיכון
  risk_free_rate: 0.0425
  # פרמיית הסיכון של שוק המניות
  equity_risk_premium: 0.0300
  #  ->  Re = 7.25%
```

שתי הגנות על הקובץ:

- **שברים עשרוניים ולא אחוזים.** ערך גדול מ־1 נעצר בשגיאה מפורשת. ‏`4.25`
  במקום `0.0425` היה מייצר עלות הון של 425% בשקט.
- **מפתח לא מוכר נחסם.** ‏`risk_free` במקום `risk_free_rate` יעצור את הריצה
  ולא יחזור בשקט לברירת המחדל.

ריצה חוזרת עם ריבית אחרת תיתן WACC אחר. מכיוון שההנחות יושבות בכל שורה, אפשר
להשוות שני קבצי פלט ולראות מיד אם ההפרש נובע מהנתונים או מההנחה.

### 7.3 מבנה ההון

| עמודה | חישוב | ערכים |
| --- | --- | --- |
| `calc_wacc_equity_value` | `market_cap` | ‏E. **שווי שוק ולא הון עצמי חשבונאי** |
| `calc_wacc_total_capital` | `E + calc_debt_value_quarterly` | ‏D+E |
| `calc_wacc_equity_weight` | `E / (D+E)` | ‎[0, 1] · מסתכם ל־1 עם משקל החוב |
| `calc_wacc_debt_weight` | `D / (D+E)` | ‎[0, 1] |
| `calc_wacc_average_debt` | `(D[t−4] + D[t]) / 2` | משמש **רק** במכנה של Rd. דורש 5 רבעונים רצופים |

**‏D הוא `calc_debt_value_quarterly` הקיים** — חוב קצר וארוך, שניהם כוללים
התחייבויות חכירה. אומת שהוא זהה בדיוק לסכום ארבעת שדות המקור ב־1,051 רבעונים,
ולכן אין בגיליון הגדרת חוב שנייה שעלולה לסטות.

**המשקלים משתמשים ביתרת סוף התקופה, לא בממוצע.** שווי השוק הוא ערך נקודתי,
ומיצוע צד אחד מול ערך נקודתי בצד השני היה מעוות את מבנה ההון. הממוצע קיים רק
כי המונה של Rd הוא זרימה של שנה שלמה.

### 7.4 עלות החוב והמס

| עמודה | חישוב | ערכים |
| --- | --- | --- |
| `calc_interest_expense_ttm` | `Σ (−interest_expense)[t−3..t]` | היפוך הסימן כמו ב־`calc_tax_expense_quarterly`. `NaN` בלי 4 רבעונים רצופים |
| `calc_wacc_cost_of_debt` | `calc_interest_expense_ttm / calc_wacc_average_debt` | ‏Rd. שלילי כשה־API מדווח ריבית חיובית |
| `calc_wacc_tax_rate` | `Σ tax_expense[t−3..t] / Σ pretax_income[t−3..t]` | ‏T. **אפס** כשהמכנה ‎≤ 0. אינו נחתך מלמעלה |
| `calc_wacc_after_tax_cost_of_debt` | `Rd × (1 − T)` | |

**המס הוא TTM ולא רבעוני** כי המונה של Rd הוא TTM, ומשום ששיעור מס אפקטיבי של
רבעון בודד קופץ פראית על הסדרי מס חד־פעמיים — קיימים רבעונים מעל 100%.

**חברה מפסידה אינה מקבלת מגן מס** ‏(T=0), ולכן מלוא עלות החוב עוברת ל־WACC. זה
הכיוון השמרני עבור סף שמאשר השקעות.

### 7.5 התוצאה והסינון

| עמודה | חישוב | ערכים |
| --- | --- | --- |
| `calc_wacc_annual` | `w_e × Re + w_d × Rd × (1−T)` | שיעור שנתי |
| `calc_wacc_quarterly` | `(1 + WACC)^(1/4) − 1` | שיעור רבעוני |
| `calc_wacc_quality_flag` | ראו למטה | 7 ערכים |
| `calc_wacc_inputs_complete` | הדגל הוא `VALID` או `NO_DEBT` | `True` · `False` |
| `calc_roic_minus_wacc_annualized` | `ROIC שנתי − WACC שנתי` | הפער השנתי |
| `calc_roic_minus_wacc_quarterly` | `ROIC רבעוני − WACC רבעוני` | הפער הרבעוני |
| `calc_creates_value` | `ROIC רבעוני > WACC רבעוני` | `True` · `False` · ריק |

`calc_creates_value` מחושב על **הזוג הרבעוני**. לא כי הוא נכון יותר — התוצאה
זהה — אלא כי הוא שורד גם ברבעון שבו ההפסד עולה על בסיס ההון וההיוון השנתי
מוחזר כ־`NaN`.

חברה בלי חוב מקבלת `WACC = Re` כבר מהרבעון הראשון, בלי להמתין שנה לעלות חוב
שאינה נחוצה לה.

### 7.6 ערכי `calc_wacc_quality_flag`

הדגל **אינו משנה אף מספר**. תפקידו להפריד בין מצבים שנראים זהים בעמודת עלות
החוב.

| ערך | מתי | האם WACC מחושב |
| --- | --- | --- |
| `VALID` | הכול תקין | כן |
| `NO_DEBT` | ‏D = 0, ולכן WACC = Re | כן |
| `DEBT_WITHOUT_INTEREST` | יש חוב אך הריבית מדווחת כאפס | כן, אך **מוטה כלפי מטה** |
| `NEGATIVE_INTEREST_EXPENSE` | ה־API דיווח ריבית חיובית (הכנסות ריבית נטו) | כן, עם Rd שלילי |
| `INSUFFICIENT_HISTORY` | אין 5 רבעונים רצופים לחוב הממוצע או 4 לריבית | לא |
| `MISSING_MARKET_CAP` | אין שווי שוק | לא |
| `MISSING_DEBT` | אין נתון חוב | לא |

**‏`DEBT_WITHOUT_INTEREST` אינו נדיר.** ה־API מדווח ריבית אפס גם כשקיים חוב
משמעותי:

| טיקר | רבעונים עם חוב ובלי ריבית | חוב אחרון |
| --- | --- | --- |
| INTC | 48 / 120 | $50.5B |
| JNJ | 46 / 120 | $49.0B |
| F | 40 / 120 | $161.0B |
| AAPL | 15 / 120, כולל האחרונים | $84.3B |

הערך מה־API נלקח כפי שהוא ו־`Rd = 0`, אבל השורה מסומנת. ההטיה חסומה ל־
`w_d × Rd × (1−T)`: אצל אפל היא כ־0.14 נקודת אחוז (החוב הוא 2% מהמבנה), אצל
חברה ממונפת היא יכולה להגיע לנקודת אחוז שלמה. **סננו לפי
`calc_wacc_inputs_complete` כשהדיוק חשוב.**

---

## 8. עמודות הדמה

63 עמודות `int8` בערך 0 או 1, בשלוש משפחות. נוצרות במכפלה קרטזית
(`itertools.product`) ולכן מכסות כל שילוב אפשרי.

**בכל שורה מסווגת נדלקת בדיוק עמודה אחת מכל משפחה. בשורה שאינה מסווגת כל
העמודות במשפחה מקבלות 0.**

הן מיועדות לצריכה תוכנתית ולא לקריאת אדם, ולכן הן מופרדות **גם מגיליון
`Data` וגם מקובץ ה-CSV**: ב-Excel הן עוברות לגיליון `Decomposition` נפרד עם
`symbol` ו-`period_key`, ובפלט ה-parquet הן נשארות בטבלה הראשית.

⚠️ מי שצרך אותן מה-CSV — הן זמינות בגיליון `Decomposition` ובקובץ ה-parquet
(`output.formats: [parquet]`).

| משפחה | תבנית | כמות | מקור |
| --- | --- | --- | --- |
| `calc_nopat_combo__` | `ebit_{pos\|neg\|zero}__tax_{pos\|neg\|zero}` | 3² = **9** | `calc_nopat_effect_combination` |
| `calc_raw_combo__` | `ebit_{up\|down\|flat}__tax_{…}__ic_{…}` | 3³ = **27** | `calc_raw_movement_combination` |
| `calc_roic_combo__` | `ebit_{pos\|neg\|zero}__tax_{…}__ic_{…}` | 3³ = **27** | `calc_roic_effect_combination` |

<details>
<summary>הרשימה המלאה של 63 השמות</summary>

**NOPAT (9)**
```
calc_nopat_combo__ebit_pos__tax_pos
calc_nopat_combo__ebit_pos__tax_neg
calc_nopat_combo__ebit_pos__tax_zero
calc_nopat_combo__ebit_neg__tax_pos
calc_nopat_combo__ebit_neg__tax_neg
calc_nopat_combo__ebit_neg__tax_zero
calc_nopat_combo__ebit_zero__tax_pos
calc_nopat_combo__ebit_zero__tax_neg
calc_nopat_combo__ebit_zero__tax_zero
```

**תנועה גולמית (27)**
```
calc_raw_combo__ebit_up__tax_up__ic_up
calc_raw_combo__ebit_up__tax_up__ic_down
calc_raw_combo__ebit_up__tax_up__ic_flat
calc_raw_combo__ebit_up__tax_down__ic_up
calc_raw_combo__ebit_up__tax_down__ic_down
calc_raw_combo__ebit_up__tax_down__ic_flat
calc_raw_combo__ebit_up__tax_flat__ic_up
calc_raw_combo__ebit_up__tax_flat__ic_down
calc_raw_combo__ebit_up__tax_flat__ic_flat
calc_raw_combo__ebit_down__tax_up__ic_up
calc_raw_combo__ebit_down__tax_up__ic_down
calc_raw_combo__ebit_down__tax_up__ic_flat
calc_raw_combo__ebit_down__tax_down__ic_up
calc_raw_combo__ebit_down__tax_down__ic_down
calc_raw_combo__ebit_down__tax_down__ic_flat
calc_raw_combo__ebit_down__tax_flat__ic_up
calc_raw_combo__ebit_down__tax_flat__ic_down
calc_raw_combo__ebit_down__tax_flat__ic_flat
calc_raw_combo__ebit_flat__tax_up__ic_up
calc_raw_combo__ebit_flat__tax_up__ic_down
calc_raw_combo__ebit_flat__tax_up__ic_flat
calc_raw_combo__ebit_flat__tax_down__ic_up
calc_raw_combo__ebit_flat__tax_down__ic_down
calc_raw_combo__ebit_flat__tax_down__ic_flat
calc_raw_combo__ebit_flat__tax_flat__ic_up
calc_raw_combo__ebit_flat__tax_flat__ic_down
calc_raw_combo__ebit_flat__tax_flat__ic_flat
```

**השפעה על ROIC (27)**
```
calc_roic_combo__ebit_pos__tax_pos__ic_pos
calc_roic_combo__ebit_pos__tax_pos__ic_neg
calc_roic_combo__ebit_pos__tax_pos__ic_zero
calc_roic_combo__ebit_pos__tax_neg__ic_pos
calc_roic_combo__ebit_pos__tax_neg__ic_neg
calc_roic_combo__ebit_pos__tax_neg__ic_zero
calc_roic_combo__ebit_pos__tax_zero__ic_pos
calc_roic_combo__ebit_pos__tax_zero__ic_neg
calc_roic_combo__ebit_pos__tax_zero__ic_zero
calc_roic_combo__ebit_neg__tax_pos__ic_pos
calc_roic_combo__ebit_neg__tax_pos__ic_neg
calc_roic_combo__ebit_neg__tax_pos__ic_zero
calc_roic_combo__ebit_neg__tax_neg__ic_pos
calc_roic_combo__ebit_neg__tax_neg__ic_neg
calc_roic_combo__ebit_neg__tax_neg__ic_zero
calc_roic_combo__ebit_neg__tax_zero__ic_pos
calc_roic_combo__ebit_neg__tax_zero__ic_neg
calc_roic_combo__ebit_neg__tax_zero__ic_zero
calc_roic_combo__ebit_zero__tax_pos__ic_pos
calc_roic_combo__ebit_zero__tax_pos__ic_neg
calc_roic_combo__ebit_zero__tax_pos__ic_zero
calc_roic_combo__ebit_zero__tax_neg__ic_pos
calc_roic_combo__ebit_zero__tax_neg__ic_neg
calc_roic_combo__ebit_zero__tax_neg__ic_zero
calc_roic_combo__ebit_zero__tax_zero__ic_pos
calc_roic_combo__ebit_zero__tax_zero__ic_neg
calc_roic_combo__ebit_zero__tax_zero__ic_zero
```

</details>

---

## 9. נספח א — ספי המהותיות

תרומה נחשבת `NEUTRAL`, ושינוי נחשב `STABLE` או `FLAT`, כאשר
`|x| ≤ tolerance`. השוואה ישירה לאפס אינה תקפה: רעש של נקודה צפה היה מסווג
שגיאת עיגול כאפקט כלכלי אמיתי.

```
tolerance = max(absolute, relative × scale)
```

| משפחה | ‎`scale` | ברירות מחדל | מה זה אומר |
| --- | --- | --- | --- |
| ROIC | `max(\|ROIC0\|, \|ROIC1\|)` | `1e-4` / `1e-3` | 10 נקודות בסיס של ROIC |
| NOPAT | `max(\|N0\|, \|N1\|, 1.0)` | `1e-9` / `5e-3` | 0.5% מה-NOPAT הגדול מבין השניים |
| סכומים (EBIT, IC) | `max(\|x0\|, \|x1\|, 1.0)` | `1e-9` / `5e-3` | 0.5% מהערך |
| שיעור מס | `max(\|T0\|, \|T1\|)` | `1e-4` / `1e-3` | נקודת בסיס אחת |
| מעבר סימן | `max(\|x0\|, \|x1\|, 1.0)` | `1e-9` / `1e-9` | כמעט מדויק — "EBIT אפס" הוא קביעה על הערך המדווח |
| דומיננטיות | — | `0.05` | חמש נקודות אחוז בין שני המשקלים הגדולים |

**שתי חוקיות scale שונות**, כי היחידות שונות: ל-ROIC אין רצפה של 1.0 (יחס
בסדר גודל 0.05 — רצפה כזאת הייתה מציפה את הסף המוחלט ומכריזה על תזוזה של 90
נקודות בסיס כלא-מהותית), ולסכומים כספיים יש רצפה כזאת.

ניתן לכוונן הכול בבלוק `decomposition` שב-[`config.yaml`](../config.yaml).

---

## 10. נספח ב — סולם הסיווג העסקי

סדר קפדני, ההתאמה הראשונה קובעת. עקיפות המשטר קודמות לחשבון הגורמים, כי
"ROIC עלה" הוא כותרת מטעה כשה-NOPAT עדיין שלילי או ההון המושקע שלילי — היחס
זז, הכלכלה לא.

```
0.  הסטטוס אינו VALID              → מחרוזת הסטטוס עצמה
1.  הון מושקע שלילי                → ROIC_MECHANICAL_NEGATIVE_IC
2.  NOPAT מהפסד לרווח              → OPERATING_TURNAROUND
3.  NOPAT מרווח להפסד              → PROFIT_TO_LOSS_DETERIORATION
4.  NOPAT שלילי + ROIC עלה         → MECHANICAL_IMPROVEMENT_WITH_NEGATIVE_NOPAT
5.  NOPAT שלילי + ROIC ירד         → MECHANICAL_DETERIORATION_WITH_NEGATIVE_NOPAT
6.  ROIC יציב                      → ROIC_STABLE_NO_CHANGE | ROIC_STABLE_OFFSETTING_EFFECTS
7.  ROIC עלה                       → לפי המבנה, הדומיננטי, וסימני הגרר
8.  ROIC ירד                       → מראה של 7
```

שלבים 7 ו-8 מסתיימים תמיד ב-`…_MIXED_EFFECTS`, ולכן הסולם **טוטלי**: אף
תצפית אינה יכולה ליפול דרכו. בדיקה אוטומטית מוודאת שהעמודה לעולם אינה ריקה
ותמיד מכילה ערך מתוך `BUSINESS_CLASSIFICATIONS`.

בתוך כל כיוון, הגורם הדומיננטי רשאי לתת את שמו לתווית **רק אם ההשפעה שלו
מסכימה עם כיוון השינוי**. אחרת התווית היא `…_DESPITE_DOMINANT_…`.

---

## 11. נספח ג — בדיקות שרצות על כל פלט

גיליון `Checks` בכל חוברת Excel כולל:

| בדיקה | מה היא מוודאת |
| --- | --- |
| NOPAT Shapley contributions sum to the total change | `C_ebit + C_tax = ΔNOPAT` |
| Post-tax ROIC Shapley contributions sum to the total change | `C_ebit + C_tax + C_ic = ΔROIC` |
| NOPAT / Raw movement / ROIC effect combination selects exactly one column | בדיוק עמודת דמה אחת דולקת בכל שורה מסווגת |
| ROIC absolute contribution shares sum to one | שלושת המשקלים מסתכמים ל-1 |
| ROIC offset ratio lies between zero and one | תוצאה של אי-שוויון המשולש |
| WACC capital weights sum to one | `w_e + w_d = 1` |
| WACC matches its weights and component costs | ‏`w_e × Re + w_d × Rd × (1−T)` |
| Quarterly WACC compounds back to the annual WACC | ‏`(1 + WACC_q)^4 = WACC_a` |
| Value-creation verdict is the same annually and quarterly | ההיוון מונוטוני ולכן אינו יכול להפוך את סימן הפער |
| Three-quarter price change compounds from three quarterly changes | ‏`(1+r_3q) = (1+r_t)(1+r_t-1)(1+r_t-2)`. המחירים האמצעיים מצטמצמים, ולכן הזהות מדויקת מעצם הבנייה — וזו בדיוק הסיבה שהיא תופסת חיווט של שתי העמודות ללאגים שונים או למקורות שונים |
