import fs from "node:fs/promises";
import path from "node:path";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const workspace = "C:\\Users\\User\\data_from_gurufocus";
const inputPath = path.join(
  workspace,
  "output",
  "GuruFocus_quarterly_2026-07-31_v3.xlsx",
);
const outputDir = path.join(workspace, ".spreadsheet_qa");

const input = await FileBlob.load(inputPath);
const workbook = await SpreadsheetFile.importXlsx(input);
const dataSheet = workbook.worksheets.getItem("Data");
const used = dataSheet.getUsedRange();
const values = used.values;
const headers = values[0].map((value) => String(value ?? ""));
const rows = values.slice(1);
const index = Object.fromEntries(headers.map((header, i) => [header, i]));

const required = [
  "period_key",
  "total_current_assets",
  "total_current_liabilities",
  "cash_and_cash_equivalents",
  "short_term_investments",
  "short_term_debt",
  "net_ppe",
  "goodwill",
  "intangible_assets",
  "calc_ebit_ttm",
  "calc_pretax_income_ttm",
  "calc_tax_expense_ttm",
  "calc_raw_tax_rate_ttm",
  "calc_nopat_ttm",
  "calc_operating_nwc",
  "calc_identifiable_intangibles",
  "calc_ic_end",
  "calc_ic_end_ex_goodwill",
  "calc_average_ic_ttm",
  "calc_average_ic_ttm_ex_goodwill",
  "calc_ic_raw",
  "calc_average_ic_raw_ttm",
  "calc_roic_pretax_ttm",
  "calc_roic_posttax_ttm",
  "calc_roic_pretax_ttm_ex_goodwill",
  "calc_roic_posttax_ttm_ex_goodwill",
  "calc_roic_pretax_ttm_ic_raw",
  "calc_roic_posttax_ttm_ic_raw",
];
const missing = required.filter((column) => !(column in index));
if (missing.length) {
  throw new Error(`Missing required columns: ${missing.join(", ")}`);
}

const forbidden = [
  "calc_tax_rate_used",
  "calc_tax_status",
  "calc_tax_flags",
  "calc_tax_rate_fallback",
  "calc_nopat_ttm_raw",
  "calc_average_ic",
  "calc_ic_is_negative",
];
const unexpected = forbidden.filter((column) => column in index);
if (unexpected.length) {
  throw new Error(`Old calculated columns still present: ${unexpected.join(", ")}`);
}

const number = (row, column) => {
  const value = row[index[column]];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
};
const residuals = new Map();
const check = (name, actual, expected) => {
  if (actual === null || expected === null || !Number.isFinite(expected)) return;
  const residual = Math.abs(actual - expected);
  residuals.set(name, Math.max(residuals.get(name) ?? 0, residual));
};

for (let i = 0; i < rows.length; i += 1) {
  const row = rows[i];
  const tca = number(row, "total_current_assets");
  const tcl = number(row, "total_current_liabilities");
  const cash = number(row, "cash_and_cash_equivalents");
  const investments = number(row, "short_term_investments");
  const debt = number(row, "short_term_debt");
  const ppe = number(row, "net_ppe");
  const goodwill = number(row, "goodwill");
  const intangibles = number(row, "intangible_assets");
  const ebitTtm = number(row, "calc_ebit_ttm");
  const pretaxTtm = number(row, "calc_pretax_income_ttm");
  const taxExpenseTtm = number(row, "calc_tax_expense_ttm");
  const taxRate = number(row, "calc_raw_tax_rate_ttm");
  const nopatTtm = number(row, "calc_nopat_ttm");

  if ([tca, tcl, cash, investments, debt, ppe].every((v) => v !== null)) {
    check(
      "OperatingNWC",
      number(row, "calc_operating_nwc"),
      tca - cash - investments - tcl + debt,
    );
  }
  if (intangibles !== null && goodwill !== null) {
    check(
      "IdentifiableIntangibles",
      number(row, "calc_identifiable_intangibles"),
      intangibles - goodwill,
    );
  }
  if ([tca, tcl, ppe, goodwill].every((v) => v !== null)) {
    check("IC_RAW", number(row, "calc_ic_raw"), tca - tcl + ppe + goodwill);
  }
  if (taxExpenseTtm !== null && pretaxTtm !== null && pretaxTtm !== 0) {
    check("RawTaxRate", taxRate, taxExpenseTtm / pretaxTtm);
  }
  if (ebitTtm !== null && taxRate !== null) {
    check("NOPAT", nopatTtm, ebitTtm * (1 - taxRate));
  }

  if (i >= 4) {
    for (const [source, average, name] of [
      ["calc_ic_end", "calc_average_ic_ttm", "AverageIC"],
      [
        "calc_ic_end_ex_goodwill",
        "calc_average_ic_ttm_ex_goodwill",
        "AverageIC_exGW",
      ],
    ]) {
      const points = [i - 4, i - 3, i - 2, i - 1, i].map((rowIndex) =>
        number(rows[rowIndex], source),
      );
      if (points.every((v) => v !== null)) {
        const expected =
          (0.5 * points[0] + points[1] + points[2] + points[3] + 0.5 * points[4]) /
          4;
        check(name, number(row, average), expected);
      }
    }
  }
  if (i >= 3) {
    const rawPoints = [i - 3, i - 2, i - 1, i].map((rowIndex) =>
      number(rows[rowIndex], "calc_ic_raw"),
    );
    if (rawPoints.every((v) => v !== null)) {
      check(
        "AverageIC_RAW",
        number(row, "calc_average_ic_raw_ttm"),
        rawPoints.reduce((sum, value) => sum + value, 0) / 4,
      );
    }
  }

  for (const [denominator, pretax, posttax, label] of [
    [
      "calc_average_ic_ttm",
      "calc_roic_pretax_ttm",
      "calc_roic_posttax_ttm",
      "IC",
    ],
    [
      "calc_average_ic_ttm_ex_goodwill",
      "calc_roic_pretax_ttm_ex_goodwill",
      "calc_roic_posttax_ttm_ex_goodwill",
      "IC_exGW",
    ],
    [
      "calc_average_ic_raw_ttm",
      "calc_roic_pretax_ttm_ic_raw",
      "calc_roic_posttax_ttm_ic_raw",
      "IC_RAW",
    ],
  ]) {
    const denominatorValue = number(row, denominator);
    if (denominatorValue !== null && denominatorValue !== 0) {
      if (ebitTtm !== null) {
        check(`ROIC_pretax_${label}`, number(row, pretax), ebitTtm / denominatorValue);
      }
      if (nopatTtm !== null) {
        check(
          `ROIC_posttax_${label}`,
          number(row, posttax),
          nopatTtm / denominatorValue,
        );
      }
    }
  }
}

for (const [name, residual] of residuals) {
  if (residual > 1e-8) {
    throw new Error(`${name} residual too large: ${residual}`);
  }
}

const qaColumns = [
  "period_key",
  "calc_ebit_ttm",
  "calc_tax_expense_ttm",
  "calc_pretax_income_ttm",
  "calc_raw_tax_rate_ttm",
  "calc_nopat_ttm",
  "calc_average_ic_ttm",
  "calc_average_ic_ttm_ex_goodwill",
  "calc_average_ic_raw_ttm",
  "calc_roic_pretax_ttm",
  "calc_roic_posttax_ttm",
  "calc_roic_pretax_ttm_ex_goodwill",
  "calc_roic_posttax_ttm_ex_goodwill",
  "calc_roic_pretax_ttm_ic_raw",
  "calc_roic_posttax_ttm_ic_raw",
];
const qa = workbook.worksheets.add("QA_CALC");
const qaRows = [
  qaColumns,
  ...rows.slice(-6).map((row) => qaColumns.map((column) => row[index[column]])),
];
qa.getRangeByIndexes(0, 0, qaRows.length, qaColumns.length).values = qaRows;
qa.getRangeByIndexes(0, 0, 1, qaColumns.length).format = {
  fill: "#1F4E78",
  font: { bold: true, color: "#FFFFFF" },
  wrapText: true,
};
qa.getRangeByIndexes(1, 4, 6, 1).format.numberFormat = "0.00%";
for (let column = 9; column < qaColumns.length; column += 1) {
  qa.getRangeByIndexes(1, column, 6, 1).format.numberFormat = "0.00%";
}
qa.getRangeByIndexes(0, 0, qaRows.length, qaColumns.length).format.autofitColumns();
qa.getRangeByIndexes(0, 0, qaRows.length, qaColumns.length).format.autofitRows();
qa.freezePanes.freezeRows(1);

const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 300 },
  summary: "final formula error scan",
});

const styleColumn = qaColumns.indexOf("calc_roic_pretax_ttm") + 1;
const toColumn = (column) => {
  let result = "";
  let value = column;
  while (value > 0) {
    value -= 1;
    result = String.fromCharCode(65 + (value % 26)) + result;
    value = Math.floor(value / 26);
  }
  return result;
};
const roicLetter = toColumn(styleColumn);
const style = await workbook.inspect({
  kind: "computedStyle",
  sheetId: "QA_CALC",
  range: `${roicLetter}2:${roicLetter}7`,
  maxChars: 2500,
});

const renders = [
  ["Data", "A1:Q12", "data_source_preview.png"],
  [
    "Data",
    `${toColumn(index.calc_tax_expense_ttm + 1)}1:${toColumn(
      index.calc_roic_posttax_ttm_ic_raw + 1,
    )}${rows.length + 1}`,
    "data_calculations_full_preview.png",
  ],
  ["QA_CALC", `A1:${toColumn(qaColumns.length)}7`, "data_calculations_preview.png"],
  ["Coverage", undefined, "coverage_preview.png"],
  ["Nulls", undefined, "nulls_preview.png"],
  ["Checks", undefined, "checks_preview.png"],
  ["Manifest", undefined, "manifest_preview.png"],
  ["RawKeys", undefined, "raw_keys_preview.png"],
];
const rendered = [];
for (const [sheetName, range, fileName] of renders) {
  try {
    const preview = await workbook.render({
      sheetName,
      ...(range ? { range } : { autoCrop: "all" }),
      scale: 1,
      format: "png",
    });
    await fs.writeFile(
      path.join(outputDir, fileName),
      new Uint8Array(await preview.arrayBuffer()),
    );
    rendered.push(fileName);
  } catch (error) {
    if (sheetName === "QA_CALC" || sheetName === "Data") throw error;
  }
}

const latest = rows.at(-1);
const latestValues = Object.fromEntries(
  qaColumns.map((column) => [column, latest[index[column]]]),
);
const summary = {
  workbook: inputPath,
  rowCount: rows.length,
  columnCount: headers.length,
  residuals: Object.fromEntries(residuals),
  latestValues,
  formulaErrorScan: errors.ndjson,
  roicComputedStyle: style.ndjson,
  rendered,
};
await fs.writeFile(
  path.join(outputDir, "verification_summary.json"),
  JSON.stringify(summary, null, 2),
  "utf8",
);
console.log(JSON.stringify({
  rowCount: summary.rowCount,
  columnCount: summary.columnCount,
  maxResidual: Math.max(...Object.values(summary.residuals)),
  latestValues,
  rendered,
}));
process.exit(0);
