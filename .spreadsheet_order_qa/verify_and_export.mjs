import fs from "node:fs/promises";
import path from "node:path";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const sourcePath = path.resolve(
  "output",
  "GuruFocus_quarterly_2026-08-01_v2.xlsx",
);
const qaDir = path.resolve(".spreadsheet_order_qa");
const finalDir = path.resolve(
  "outputs",
  "019fb547-8314-7b90-b3c9-65f7dde85cc4",
);
const finalPath = path.join(
  finalDir,
  "GuruFocus_quarterly_2026-08-01_float_2dp.xlsx",
);

await fs.mkdir(qaDir, { recursive: true });
await fs.mkdir(finalDir, { recursive: true });

const input = await FileBlob.load(sourcePath);
const workbook = await SpreadsheetFile.importXlsx(input);

for (const sheetName of ["Data", "Coverage", "Nulls", "Checks", "Manifest"]) {
  workbook.worksheets.getItem(sheetName).freezePanes.freezeRows(1);
}

const dataSheet = workbook.worksheets.getItem("Data");

// Keep the Excel workbook auditable: calculated columns are live formulas
// tied directly to the displayed source columns. The CSV retains code results.
const formulaColumns = [
  ["J2", "J2:J107", '=IF(I2="","",-I2)'],
  ["L2", "L2:L107", '=IF(OR(J2="",K2="",K2=0),"",J2/K2)'],
  ["N2", "N2:N107", '=IF(OR(M2="",L2=""),"",M2*(1-L2))'],
  ["W2", "W2:W107", '=IF(COUNT(O2,R2,U2,V2)<4,"",O2-R2+V2+U2)'],
  ["X3", "X3:X107", '=IF(OR(W2="",W3="",A2<>A3,F3*4+VALUE(RIGHT(G3,1))<>F2*4+VALUE(RIGHT(G2,1))+1),"",(W2+W3)/2)'],
  ["Y2", "Y2:Y107", '=IF(OR(M2="",X2="",X2=0),"",M2/X2)'],
  ["Z2", "Z2:Z107", '=IF(OR(N2="",X2="",X2=0),"",N2/X2)'],
  ["AC2", "AC2:AC107", '=IF(COUNT(AA2,AB2)<2,"",AA2+AB2)'],
  ["AH2", "AH2:AH107", '=IF(OR(AC2="",AG2="",AG2=0),"",AC2/AG2)'],
  ["AN2", "AN2:AN107", '=IF(COUNT(AJ2,AK2,AL2,AM2)<4,"",AJ2+AK2-AL2-AM2)'],
  ["AP5", "AP5:AP107", '=IF(OR(COUNT(AO2:AO5)<4,A2<>A5,F5*4+VALUE(RIGHT(G5,1))<>F2*4+VALUE(RIGHT(G2,1))+3),"",SUM(AO2:AO5))'],
  ["AQ2", "AQ2:AQ107", '=IF(OR(AN2="",AP2="",AP2=0),"",AN2/AP2)'],
];
for (const [anchor, target, formula] of formulaColumns) {
  dataSheet.getRange(anchor).formulas = [[formula]];
  dataSheet.getRange(target).fillDown();
}
dataSheet.getRange("I2:AT107").format.numberFormat = "0.00";

dataSheet.getRange("A1:AT1").format = {
  fill: "#1F4E78",
  font: { bold: true, color: "#FFFFFF" },
  verticalAlignment: "center",
};

for (const column of ["J", "L", "N", "W", "X", "Y", "Z", "AC", "AH", "AN", "AP", "AQ"]) {
  dataSheet.getRange(`${column}1`).format = {
    fill: "#E2F0D9",
    font: { bold: true, color: "#1F1F1F" },
  };
}
for (const column of ["AI", "AR", "AS", "AT"]) {
  dataSheet.getRange(`${column}1`).format = {
    fill: "#FFF2CC",
    font: { bold: true, color: "#1F1F1F" },
  };
}

dataSheet.getRange("AA1:AB107").format.columnWidth = 40;
dataSheet.getRange("AD1:AI107").format.columnWidth = 38;
dataSheet.getRange("AK1:AK107").format.columnWidth = 35;
dataSheet.getRange("AN1:AN107").format.columnWidth = 40;
dataSheet.getRange("AP1:AR107").format.columnWidth = 48;
dataSheet.getRange("AS1:AT107").format.columnWidth = 50;

for (const [sheetName, headerRange] of [
  ["Coverage", "A1:H1"],
  ["Nulls", "A1:E1"],
  ["Checks", "A1:F1"],
  ["Manifest", "A1:S1"],
]) {
  workbook.worksheets.getItem(sheetName).getRange(headerRange).format = {
    fill: "#1F4E78",
    font: { bold: true, color: "#FFFFFF" },
  };
}

const overview = await workbook.inspect({
  kind: "workbook,sheet",
  include: "id,name",
  maxChars: 5000,
});
const identityTaxNopat = await workbook.inspect({
  kind: "table",
  range: "Data!A1:N8",
  include: "values,formulas",
  tableMaxRows: 8,
  tableMaxCols: 14,
  maxChars: 10000,
});
const investedCapitalRoic = await workbook.inspect({
  kind: "table",
  range: "Data!O1:Z8",
  include: "values,formulas",
  tableMaxRows: 8,
  tableMaxCols: 12,
  maxChars: 12000,
});
const evFcf = await workbook.inspect({
  kind: "table",
  range: "Data!AJ1:AT8",
  include: "values,formulas",
  tableMaxRows: 8,
  tableMaxCols: 11,
  maxChars: 14000,
});
const debtToEquity = await workbook.inspect({
  kind: "table",
  range: "Data!AA1:AI8",
  include: "values,formulas",
  tableMaxRows: 8,
  tableMaxCols: 9,
  maxChars: 12000,
});
const latestDebtAndEv = await workbook.inspect({
  kind: "table",
  range: "Data!AA105:AT107",
  include: "values,formulas",
  tableMaxRows: 3,
  tableMaxCols: 20,
  maxChars: 12000,
});
const selectedFormulas = {
  taxAndNopat: dataSheet.getRange("I1:N3").formulas,
  investedCapitalAndRoic: dataSheet.getRange("W1:Z4").formulas,
  debtToEquity: dataSheet.getRange("AC1:AI3").formulas,
  enterpriseValueAndFcf: dataSheet.getRange("AN1:AQ6").formulas,
};
const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 300 },
  summary: "final formula error scan",
  maxChars: 5000,
});

const renderJobs = [
  ["data_identity_tax_nopat.png", { sheetName: "Data", range: "A1:N20", scale: 1.3 }],
  ["data_ic_raw_roic.png", { sheetName: "Data", range: "O1:Z20", scale: 1.3 }],
  ["data_debt_to_equity.png", { sheetName: "Data", range: "AA1:AI20", scale: 1.2 }],
  ["data_ev_fcf.png", { sheetName: "Data", range: "AJ1:AT20", scale: 1.15 }],
  ["data_debt_ev_latest.png", { sheetName: "Data", range: "AA100:AT107", scale: 1.1 }],
  ["coverage.png", { sheetName: "Coverage", autoCrop: "all", scale: 1 }],
  ["nulls.png", { sheetName: "Nulls", autoCrop: "all", scale: 1 }],
  ["checks.png", { sheetName: "Checks", autoCrop: "all", scale: 1 }],
  ["manifest.png", { sheetName: "Manifest", autoCrop: "all", scale: 1 }],
];

for (const [fileName, options] of renderJobs) {
  const preview = await workbook.render({ ...options, format: "png" });
  const bytes = new Uint8Array(await preview.arrayBuffer());
  await fs.writeFile(path.join(qaDir, fileName), bytes);
}

const exported = await SpreadsheetFile.exportXlsx(workbook);
await exported.save(finalPath);

const finalInput = await FileBlob.load(finalPath);
const finalWorkbook = await SpreadsheetFile.importXlsx(finalInput);
const finalDataSheet = finalWorkbook.worksheets.getItem("Data");
const finalErrors = await finalWorkbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 300 },
  summary: "exported workbook formula error scan",
  maxChars: 5000,
});
const finalVerification = {
  sheets: finalWorkbook.worksheets.items.map((sheet) => sheet.name),
  dataRange: finalDataSheet.getUsedRange().address,
  debtFormula: finalDataSheet.getRange("AH106").formulas,
  enterpriseValueFormula: finalDataSheet.getRange("AN106").formulas,
  evFcfFormula: finalDataSheet.getRange("AQ106").formulas,
  numberFormats: {
    rawValue: finalDataSheet.getRange("I106").format.numberFormat,
    ratio: finalDataSheet.getRange("AH106").format.numberFormat,
    enterpriseValue: finalDataSheet.getRange("AN106").format.numberFormat,
    multiple: finalDataSheet.getRange("AQ106").format.numberFormat,
  },
  errors: finalErrors.ndjson,
};

const summary = {
  sourcePath,
  finalPath,
  overview: overview.ndjson,
  identityTaxNopat: identityTaxNopat.ndjson,
  investedCapitalRoic: investedCapitalRoic.ndjson,
  evFcf: evFcf.ndjson,
  debtToEquity: debtToEquity.ndjson,
  latestDebtAndEv: latestDebtAndEv.ndjson,
  selectedFormulas,
  errors: errors.ndjson,
  finalVerification,
  renders: renderJobs.map(([fileName]) => path.join(qaDir, fileName)),
};
await fs.writeFile(
  path.join(qaDir, "artifact_verification.json"),
  JSON.stringify(summary, null, 2),
  "utf8",
);

console.log(JSON.stringify({
  finalPath,
  renderCount: renderJobs.length,
  errors: errors.ndjson,
}, null, 2));
