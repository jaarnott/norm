/** Real get_profit_and_loss documents (Glass Goose, read 23 Oct 2026), trimmed to what the screen reads. */
import type { PlDoc } from './statement';

export const SEP_2026: PlDoc = {
  organisation: "Cook Brothers Bars Federal Street Limited",
  currency: "NZD",
  from: "2026-09-01",
  to: "2026-09-30",
  columns: ["30 Sep 26"],
  totals: {"income": [210572.62], "cost_of_sales": [104220.01], "gross_profit": [106352.61], "operating_expenses": [66884.7], "net_profit": [39467.91]},
  sections: [
    { title: "Income", total: [210572.62], rows: [
      { name: "Bar Sales", values: [151969.69] },
      { name: "Kitchen Sales", values: [58602.93] },
    ] },
    { title: "Cost of Goods", total: [44718.05], rows: [
      { name: "Bar Purchases", values: [28488.87] },
      { name: "Kitchen Purchases", values: [18205.24] },
      { name: "Supplier Rebates", values: [-1976.06] },
    ] },
    { title: "Salaries & Wages", total: [59501.96], rows: [
      { name: "Bar & Floor Team", values: [31766.74] },
      { name: "Kitchen Team", values: [25251.44] },
      { name: "Leave & Entitlements", values: [2483.78] },
    ] },
    { title: "Less Operating Expenses", total: null, rows: [
      { name: "Loss on Disposal of Fixed Assets", values: [2333.39] },
    ] },
    { title: "Administrative & General", total: [15788.64], rows: [
      { name: "Management Contract", values: [15000.0] },
      { name: "Phone & Fax", values: [124.85] },
      { name: "Printing & Stationery", values: [663.79] },
    ] },
    { title: "Advertising & Promotions", total: [6281.09], rows: [
      { name: "DJ's", values: [5092.5] },
      { name: "Marketing", values: [1188.59] },
    ] },
    { title: "Employment Related Expenses", total: [954.91], rows: [
      { name: "ACC", values: [655.71] },
      { name: "Uniforms", values: [299.2] },
    ] },
    { title: "Operating Costs", total: [3016.55], rows: [
      { name: "Bank Fees", values: [-3149.84] },
      { name: "Cleaning", values: [1007.0] },
      { name: "Glassware", values: [377.07] },
      { name: "Licences", values: [-408.7] },
      { name: "Operating Equipment - Bar", values: [712.2] },
      { name: "Operating Equipment - Kitchen", values: [544.55] },
      { name: "Repairs and Maintenance - Bar", values: [2442.07] },
      { name: "Rubbish Removal", values: [973.33] },
      { name: "Subscriptions", values: [201.38] },
      { name: "Till and Eftpos Rental", values: [317.49] },
    ] },
    { title: "Property Costs", total: [32642.01], rows: [
      { name: "Insurance", values: [553.59] },
      { name: "Rates", values: [5022.91] },
      { name: "Rent", values: [27065.51] },
    ] },
    { title: "Utility Costs", total: [5868.11], rows: [
      { name: "CO2", values: [391.06] },
      { name: "Electricity", values: [2929.05] },
      { name: "LPG Gas", values: [2548.0] },
    ] },
  ],
};

export const AUG_2026: PlDoc = {
  organisation: "Cook Brothers Bars Federal Street Limited",
  currency: "NZD",
  from: "2026-08-01",
  to: "2026-08-31",
  columns: ["31 Aug 26"],
  totals: {"income": [237425.63], "cost_of_sales": [117600.93], "gross_profit": [119824.7], "operating_expenses": [60513.11], "net_profit": [35532.66]},
  sections: [
    { title: "Income", total: [237425.63], rows: [
      { name: "Bar Sales", values: [158698.43] },
      { name: "Kitchen Sales", values: [78727.2] },
    ] },
    { title: "Cost of Goods", total: [47576.94], rows: [
      { name: "Bar Purchases", values: [31952.13] },
      { name: "Kitchen Purchases", values: [17073.36] },
      { name: "Supplier Rebates", values: [-1448.55] },
    ] },
    { title: "Salaries & Wages", total: [70023.99], rows: [
      { name: "Bar & Floor Team", values: [34377.78] },
      { name: "Kitchen Team", values: [29782.92] },
      { name: "Leave & Entitlements", values: [5863.29] },
    ] },
    { title: "Less Operating Expenses", total: null, rows: [
      { name: "Loss on Disposal of Fixed Assets", values: [3585.76] },
    ] },
    { title: "Administrative & General", total: [15168.74], rows: [
      { name: "Management Contract", values: [15000.0] },
      { name: "Phone & Fax", values: [124.68] },
      { name: "Printing & Stationery", values: [44.06] },
    ] },
    { title: "Advertising & Promotions", total: [6388.11], rows: [
      { name: "DJ's", values: [5040.0] },
      { name: "Marketing", values: [1348.11] },
    ] },
    { title: "Employment Related Expenses", total: [744.86], rows: [
      { name: "ACC", values: [744.86] },
    ] },
    { title: "Operating Costs", total: [7492.56], rows: [
      { name: "Bank Fees", values: [-1693.16] },
      { name: "Cleaning", values: [1361.26] },
      { name: "Glassware", values: [1166.44] },
      { name: "Licences", values: [408.7] },
      { name: "Operating Equipment - Bar", values: [815.63] },
      { name: "Operating Equipment - Kitchen", values: [690.04] },
      { name: "Repairs and Maintenance - Bar", values: [1855.07] },
      { name: "Repairs and Maintenance - Kitchen", values: [1430.0] },
      { name: "Rubbish Removal", values: [939.71] },
      { name: "Subscriptions", values: [201.38] },
      { name: "Till and Eftpos Rental", values: [317.49] },
    ] },
    { title: "Property Costs", total: [21843.56], rows: [
      { name: "Insurance", values: [553.59] },
      { name: "Rates", values: [4786.25] },
      { name: "Rent", values: [16503.72] },
    ] },
    { title: "Utility Costs", total: [5289.52], rows: [
      { name: "CO2", values: [487.96] },
      { name: "Electricity", values: [3533.53] },
      { name: "LPG Gas", values: [1268.03] },
    ] },
    { title: null, total: null, rows: [
      { name: "Operating Profit", values: [59311.59] },
    ] },
    { title: "Non-Operating Expenses", total: [23778.93], rows: [
      { name: "Depreciation", values: [7800.99] },
      { name: "Income Tax Expense", values: [15977.94] },
    ] },
  ],
};

export const SEP_2025: PlDoc = {
  organisation: "Cook Brothers Bars Federal Street Limited",
  currency: "NZD",
  from: "2025-09-01",
  to: "2025-09-30",
  columns: ["30 Sep 25"],
  totals: {"income": [155385.46], "cost_of_sales": [89458.05], "gross_profit": [65927.41], "operating_expenses": [60851.16], "net_profit": [-1969.46]},
  sections: [
    { title: "Income", total: [155385.46], rows: [
      { name: "Bar Sales", values: [110516.5] },
      { name: "Kitchen Sales", values: [44868.96] },
    ] },
    { title: "Cost of Goods", total: [37225.47], rows: [
      { name: "Bar Purchases", values: [24773.2] },
      { name: "Kitchen Purchases", values: [12562.38] },
      { name: "Supplier Rebates", values: [-110.11] },
    ] },
    { title: "Salaries & Wages", total: [52232.58], rows: [
      { name: "Bar & Floor Team", values: [27526.59] },
      { name: "Kitchen Team", values: [17338.29] },
      { name: "Leave & Entitlements", values: [7367.7] },
    ] },
    { title: "Administrative & General", total: [11646.5], rows: [
      { name: "Management Contract", values: [11500.0] },
      { name: "Phone & Fax", values: [122.7] },
      { name: "Printing & Stationery", values: [23.8] },
    ] },
    { title: "Advertising & Promotions", total: [6808.5], rows: [
      { name: "DJ's", values: [5267.5] },
      { name: "Marketing", values: [449.39] },
      { name: "Promotions", values: [1091.61] },
    ] },
    { title: "Employment Related Expenses", total: [1440.01], rows: [
      { name: "ACC", values: [540.92] },
      { name: "Training", values: [759.81] },
      { name: "Uniforms", values: [139.28] },
    ] },
    { title: "Operating Costs", total: [5216.73], rows: [
      { name: "Bank Fees", values: [-905.96] },
      { name: "Cleaning", values: [597.54] },
      { name: "Glassware", values: [160.65] },
      { name: "Operating Equipment - Bar", values: [680.43] },
      { name: "Operating Equipment - Kitchen", values: [314.09] },
      { name: "Repairs and Maintenance - Bar", values: [2675.82] },
      { name: "Rubbish Removal", values: [754.07] },
      { name: "Subscriptions", values: [184.59] },
      { name: "Till and Eftpos Rental", values: [755.5] },
    ] },
    { title: "Property Costs", total: [32869.85], rows: [
      { name: "Insurance", values: [822.93] },
      { name: "Rates", values: [4981.41] },
      { name: "Rent", values: [27065.51] },
    ] },
    { title: "Utility Costs", total: [2869.57], rows: [
      { name: "CO2", values: [335.77] },
      { name: "Electricity", values: [1708.43] },
      { name: "LPG Gas", values: [825.37] },
    ] },
    { title: null, total: null, rows: [
      { name: "Operating Profit", values: [5076.25] },
    ] },
    { title: "Non-Operating Expenses", total: [7045.71], rows: [
      { name: "Depreciation", values: [7850.14] },
      { name: "Income Tax Expense", values: [-804.43] },
    ] },
  ],
};
