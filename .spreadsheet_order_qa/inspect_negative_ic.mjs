import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const workbookPath =
  "C:\\Users\\User\\data_from_gurufocus\\outputs\\019fb547-8314-7b90-b3c9-65f7dde85cc4\\GuruFocus_quarterly_2026-07-31_ordered_calculations.xlsx";

const workbook = await SpreadsheetFile.importXlsx(
  await FileBlob.load(workbookPath),
);

const identity = await workbook.inspect({
  kind: "table",
  range: "Data!A1:R3",
  include: "values,formulas",
  tableMaxRows: 3,
  tableMaxCols: 18,
  maxChars: 5000,
});
const calculation = await workbook.inspect({
  kind: "table",
  range: "Data!BY1:CQ3",
  include: "values,formulas",
  tableMaxRows: 3,
  tableMaxCols: 19,
  maxChars: 7000,
});

console.log(identity.ndjson);
console.log(calculation.ndjson);
