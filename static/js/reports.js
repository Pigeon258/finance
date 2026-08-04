(function () {
  "use strict";

  const dataElement = document.getElementById("report-chart-data");
  if (!dataElement || typeof echarts === "undefined") return;
  const data = JSON.parse(dataElement.textContent);
  const themeElement = document.getElementById("report-chart-theme");
  if (themeElement) {
    echarts.registerTheme("pf-active", JSON.parse(themeElement.textContent));
  }
  const charts = [];

  function chart(id, option) {
    const element = document.getElementById(id);
    if (!element) return;
    const instance = echarts.init(element, themeElement ? "pf-active" : null);
    instance.setOption(option);
    charts.push(instance);
  }

  function axes(labels) {
    return {
      tooltip: { trigger: "axis" },
      legend: {},
      grid: { left: 72, right: 24, top: 48, bottom: 64 },
      xAxis: { type: "category", data: labels, axisLabel: { rotate: labels.length > 12 ? 45 : 0 } },
      yAxis: { type: "value" },
    };
  }

  chart("chart-monthly", {
    ...axes(data.monthly.labels),
    series: [
      { name: "收入", type: "bar", data: data.monthly.income },
      { name: "支出", type: "bar", data: data.monthly.expense },
    ],
  });
  chart("chart-categories", {
    ...axes(data.categories.labels),
    series: [{ name: "分类净支出", type: "bar", data: data.categories.values }],
  });
  chart("chart-daily", { ...axes(data.daily.labels), series: [{ name: "净支出", type: "line", showSymbol: false, data: data.daily.values }] });
  chart("chart-net-funds", { ...axes(data.netFunds.labels), series: [{ name: "净资金", type: "line", showSymbol: false, data: data.netFunds.values }] });
  chart("chart-credit", { ...axes(data.credit.labels), series: [{ name: "信用卡消费", type: "bar", data: data.credit.values }] });
  chart("chart-installments", {
    ...axes(data.installments.labels),
    series: [
      { name: "已入账净额", type: "bar", stack: "burden", data: data.installments.actual },
      { name: "未来承诺", type: "bar", stack: "burden", data: data.installments.planned },
    ],
  });
  chart("chart-savings", {
    ...axes(data.savings.labels),
    series: [
      { name: "储蓄目标", type: "bar", data: data.savings.target },
      { name: "月度收支结余", type: "line", data: data.savings.surplus },
    ],
  });

  window.addEventListener("resize", function () {
    charts.forEach((instance) => instance.resize());
  });
})();
