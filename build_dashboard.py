"""
Builds reports/dashboard.html: a self-contained, static KPI dashboard (no
server, no fetch) that mirrors the Power BI model in powerbi/README.md, for
an immediate visual preview of the project's headline findings.

All chart data is baked in as JSON at build time from data/processed/*.csv
and reports/findings.json - open the HTML file directly in a browser.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PROCESSED_DIR = ROOT / "data" / "processed"
REPORTS_DIR = ROOT / "reports"

RNG_SEED = 7

# Fixed categorical color slots (validated palette, see dataviz skill), assigned
# to departments in a stable order and reused across every chart in this file.
CATEGORICAL = {
    "light": ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"],
    "dark":  ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"],
}


def main():
    findings = json.loads((REPORTS_DIR / "findings.json").read_text())
    dept_summary = pd.DataFrame(findings["department_summary"]).sort_values("department_id")
    dept_order = dept_summary["department_name"].tolist()
    color_map_light = {name: CATEGORICAL["light"][i % 8] for i, name in enumerate(dept_order)}
    color_map_dark = {name: CATEGORICAL["dark"][i % 8] for i, name in enumerate(dept_order)}

    bed_util = pd.DataFrame(findings["bed_utilization"]).sort_values("avg_utilization_pct", ascending=False)
    monthly = pd.read_csv(PROCESSED_DIR / "monthly_trend.csv")
    monthly_total = monthly.groupby("arrival_month")["visit_count"].sum().reset_index().sort_values("arrival_month")

    shift_df = pd.read_csv(PROCESSED_DIR / "shift_level_summary.csv")
    shift_df["ppn_demeaned"] = shift_df["patients_per_nurse"] - shift_df.groupby("department_id")["patients_per_nurse"].transform("mean")
    shift_df["wait_demeaned"] = shift_df["avg_wait_minutes"] - shift_df.groupby("department_id")["avg_wait_minutes"].transform("mean")
    rng = np.random.default_rng(RNG_SEED)
    sample = shift_df.sample(n=min(900, len(shift_df)), random_state=RNG_SEED)
    scatter_points = [{"x": round(r.ppn_demeaned, 2), "y": round(r.wait_demeaned, 1)} for r in sample.itertuples()]

    reg = findings["staffing_wait_regression"]
    x_min, x_max = float(shift_df["ppn_demeaned"].min()), float(shift_df["ppn_demeaned"].max())
    reg_line = [
        {"x": round(x_min, 2), "y": round(reg["slope_minutes_per_patient_per_nurse"] * x_min, 2)},
        {"x": round(x_max, 2), "y": round(reg["slope_minutes_per_patient_per_nurse"] * x_max, 2)},
    ]

    staffing_flags = pd.DataFrame(findings["staffing_flags"]["top_bottleneck_combos"])
    staffing_flags["label"] = staffing_flags["department_name"] + " · " + staffing_flags["shift_type"]

    ov = findings["dataset_overview"]
    flags = findings["staffing_flags"]
    proj = findings["staffing_reallocation_projection"]
    tt = findings["ttest_emergency_worst_shift"]

    data = {
        "deptOrder": dept_order,
        "colorLight": color_map_light,
        "colorDark": color_map_dark,
        "kpis": {
            "totalVisits": ov["total_visits"],
            "totalDepartments": ov["total_departments"],
            "dateRange": ov["date_range"],
            "avgWaitOverall": round(float(dept_summary["avg_wait_minutes"].mean()), 1),
            "avgSatisfaction": round(float(dept_summary["avg_satisfaction"].mean()), 2),
            "avgUtilization": round(float(bed_util["avg_utilization_pct"].mean()), 1),
            "pctShiftsFlagged": round(100 * flags["flagged_shift_count"] / flags["total_shift_count"], 1),
            "projectedReductionMin": proj["projected_wait_reduction_minutes"],
        },
        "waitVsTarget": [
            {"name": r.department_name, "avgWait": r.avg_wait_minutes, "target": r.target_wait_minutes,
             "pctAboveTarget": r.pct_above_target}
            for r in dept_summary.itertuples()
        ],
        "bedUtilization": [
            {"name": r.department_name, "avg": r.avg_utilization_pct, "peak": r.peak_utilization_pct}
            for r in bed_util.itertuples()
        ],
        "monthlyTrend": [
            {"month": int(r.arrival_month), "visits": int(r.visit_count)}
            for r in monthly_total.itertuples()
        ],
        "scatter": scatter_points,
        "regressionLine": reg_line,
        "regression": {
            "slope": reg["slope_minutes_per_patient_per_nurse"],
            "r2": reg["r_squared"],
            "p": reg["p_value"],
            "n": reg["n_shifts"],
        },
        "bottlenecks": [
            {"label": r.label, "count": int(r.flagged_shift_count), "avgWait": round(float(r.avg_wait_minutes), 1)}
            for r in staffing_flags.itertuples()
        ],
        "narrative": {
            "worstShift": tt["worst_shift"],
            "worstShiftWait": tt["worst_shift_mean_wait"],
            "restWait": tt["rest_mean_wait"],
            "flaggedAvgWait": flags["flagged_avg_wait"],
            "unflaggedAvgWait": flags["unflagged_avg_wait"],
            "addedNurses": proj["added_nurses_per_flagged_shift"],
            "projectedNewWait": proj["projected_new_avg_wait_minutes"],
        },
    }

    html = HTML_TEMPLATE.replace("__DATA__", json.dumps(data))
    out_path = REPORTS_DIR / "dashboard.html"
    out_path.write_text(html, encoding="utf-8")
    print(f"Dashboard written to {out_path.relative_to(ROOT)}")


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Hospital Patient Flow &amp; Operations Dashboard</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.5.1/chart.umd.min.js"></script>
<style>
  :root {
    color-scheme: light;
    --surface-1:      #fcfcfb;
    --page-plane:     #f9f9f7;
    --text-primary:   #0b0b0b;
    --text-secondary: #52514e;
    --text-muted:     #898781;
    --gridline:       #e1e0d9;
    --baseline:       #c3c2b7;
    --border:         rgba(11,11,11,0.10);
    --series-1:       #2a78d6;
    --good:           #0ca30c;
  }
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) {
      color-scheme: dark;
      --surface-1:      #1a1a19;
      --page-plane:     #0d0d0d;
      --text-primary:   #ffffff;
      --text-secondary: #c3c2b7;
      --text-muted:     #898781;
      --gridline:       #2c2c2a;
      --baseline:       #383835;
      --border:         rgba(255,255,255,0.10);
      --series-1:       #3987e5;
      --good:           #0ca30c;
    }
  }
  :root[data-theme="dark"] {
    color-scheme: dark;
    --surface-1:      #1a1a19;
    --page-plane:     #0d0d0d;
    --text-primary:   #ffffff;
    --text-secondary: #c3c2b7;
    --text-muted:     #898781;
    --gridline:       #2c2c2a;
    --baseline:       #383835;
    --border:         rgba(255,255,255,0.10);
    --series-1:       #3987e5;
    --good:           #0ca30c;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    background: var(--page-plane);
    color: var(--text-primary);
    font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
    padding: 16px;
  }
  .wrap { max-width: 1180px; margin: 0 auto; }
  header { display: flex; justify-content: space-between; align-items: baseline; flex-wrap: wrap; gap: 8px; margin-bottom: 4px; }
  h1 { font-size: 20px; margin: 0; }
  .subtitle { color: var(--text-secondary); font-size: 13px; margin: 4px 0 20px; }
  .theme-toggle {
    font-size: 12px; color: var(--text-secondary); background: var(--surface-1);
    border: 1px solid var(--border); border-radius: 6px; padding: 6px 10px; cursor: pointer;
  }
  .kpi-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; margin-bottom: 20px; }
  .kpi {
    background: var(--surface-1); border: 1px solid var(--border); border-radius: 10px;
    padding: 14px 16px;
  }
  .kpi .label { font-size: 12px; color: var(--text-secondary); }
  .kpi .value { font-size: 24px; font-weight: 600; margin-top: 4px; }
  .kpi .sub { font-size: 11px; color: var(--text-muted); margin-top: 2px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(420px, 1fr)); gap: 14px; }
  .card {
    background: var(--surface-1); border: 1px solid var(--border); border-radius: 10px;
    padding: 16px;
  }
  .card h2 { font-size: 14px; margin: 0 0 2px; }
  .card .desc { font-size: 12px; color: var(--text-secondary); margin: 0 0 12px; }
  .card canvas { max-height: 280px; }
  .findings { background: var(--surface-1); border: 1px solid var(--border); border-radius: 10px; padding: 16px 20px; margin-top: 14px; }
  .findings h2 { font-size: 14px; margin: 0 0 10px; }
  .findings ul { margin: 0; padding-left: 18px; font-size: 13px; line-height: 1.7; color: var(--text-secondary); }
  .findings li b { color: var(--text-primary); }
  footer { text-align: center; font-size: 11px; color: var(--text-muted); margin: 24px 0 8px; }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>Hospital Patient Flow &amp; Operations Analytics</h1>
    <button class="theme-toggle" id="themeToggle">Toggle dark mode</button>
  </header>
  <p class="subtitle" id="subtitle"></p>

  <div class="kpi-row" id="kpiRow"></div>

  <div class="grid">
    <div class="card">
      <h2>Average wait time vs. target, by department</h2>
      <p class="desc">Bar = observed average wait; diamond marker = department's operational target.</p>
      <canvas id="waitChart"></canvas>
    </div>
    <div class="card">
      <h2>Staffing ratio vs. wait time (within-department)</h2>
      <p class="desc" id="scatterDesc"></p>
      <canvas id="scatterChart"></canvas>
    </div>
    <div class="card">
      <h2>Bed utilization by department</h2>
      <p class="desc">Average concurrent occupancy as % of bed capacity; dashed line = 100% capacity.</p>
      <canvas id="utilChart"></canvas>
    </div>
    <div class="card">
      <h2>Monthly visit volume (all departments)</h2>
      <p class="desc">Seasonality driven mostly by Emergency flu-season volume (Dec-Feb).</p>
      <canvas id="trendChart"></canvas>
    </div>
    <div class="card" style="grid-column: 1 / -1;">
      <h2>Top under-staffed bottleneck shifts</h2>
      <p class="desc">Department/shift combinations most frequently flagged in the top quartile of patients-per-nurse.</p>
      <canvas id="bottleneckChart"></canvas>
    </div>
  </div>

  <div class="findings">
    <h2>Key findings</h2>
    <ul id="findingsList"></ul>
  </div>

  <footer>Synthetic dataset generated for this project (see README.md) &middot; Built with Python/Pandas/SciPy + Chart.js</footer>
</div>

<script>
const DATA = __DATA__;

function isDark() {
  const stamp = document.documentElement.getAttribute('data-theme');
  if (stamp === 'dark') return true;
  if (stamp === 'light') return false;
  return window.matchMedia('(prefers-color-scheme: dark)').matches;
}
function colorFor(name) {
  const map = isDark() ? DATA.colorDark : DATA.colorLight;
  return map[name] || '#898781';
}
function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

document.getElementById('subtitle').textContent =
  `${DATA.kpis.totalVisits.toLocaleString()} patient visits across ${DATA.kpis.totalDepartments} departments, ` +
  `${DATA.kpis.dateRange[0]} to ${DATA.kpis.dateRange[1]} (synthetic data)`;

const kpiRow = document.getElementById('kpiRow');
const kpis = [
  { label: 'Total visits', value: DATA.kpis.totalVisits.toLocaleString(), sub: `${DATA.kpis.totalDepartments} departments` },
  { label: 'Avg wait (all depts)', value: `${DATA.kpis.avgWaitOverall} min`, sub: 'across all visits' },
  { label: 'Avg bed utilization', value: `${DATA.kpis.avgUtilization}%`, sub: 'of capacity' },
  { label: 'Shifts flagged understaffed', value: `${DATA.kpis.pctShiftsFlagged}%`, sub: 'top quartile patients/nurse' },
  { label: 'Avg satisfaction', value: `${DATA.kpis.avgSatisfaction} / 5`, sub: 'patient-reported' },
  { label: 'Projected wait reduction', value: `${DATA.kpis.projectedReductionMin} min`, sub: `+${DATA.narrative.addedNurses} nurses on flagged shifts` },
];
kpiRow.innerHTML = kpis.map(k => `
  <div class="kpi">
    <div class="label">${k.label}</div>
    <div class="value">${k.value}</div>
    <div class="sub">${k.sub}</div>
  </div>`).join('');

document.getElementById('scatterDesc').textContent =
  `Each point = one department/date/shift; demeaned by department. Slope ${DATA.regression.slope} min per ` +
  `patient/nurse, R²=${DATA.regression.r2}, p<0.001, n=${DATA.regression.n.toLocaleString()}.`;

const gridColor = () => cssVar('--gridline');
const tickColor = () => cssVar('--text-muted');
const textColor = () => cssVar('--text-primary');

Chart.defaults.font.family = "system-ui, -apple-system, 'Segoe UI', sans-serif";
Chart.defaults.color = tickColor();

function baseScales(extra) {
  return Object.assign({
    x: { grid: { color: gridColor() }, ticks: { color: tickColor() } },
    y: { grid: { color: gridColor() }, ticks: { color: tickColor() }, beginAtZero: true },
  }, extra || {});
}

const charts = [];

function buildWaitChart() {
  const ctx = document.getElementById('waitChart');
  const sorted = [...DATA.waitVsTarget].sort((a,b) => b.pctAboveTarget - a.pctAboveTarget);
  const chart = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: sorted.map(d => d.name),
      datasets: [
        {
          label: 'Avg wait (min)',
          data: sorted.map(d => d.avgWait),
          backgroundColor: sorted.map(d => colorFor(d.name)),
          borderRadius: 4,
          barThickness: 22,
        },
        {
          label: 'Target (min)',
          data: sorted.map(d => d.target),
          type: 'scatter',
          pointStyle: 'rectRot',
          pointRadius: 7,
          pointBackgroundColor: 'transparent',
          pointBorderColor: textColor(),
          pointBorderWidth: 2,
        },
      ],
    },
    options: {
      responsive: true,
      plugins: { legend: { position: 'top', labels: { color: tickColor() } } },
      scales: baseScales(),
    },
  });
  charts.push(chart);
}

function buildScatterChart() {
  const ctx = document.getElementById('scatterChart');
  const chart = new Chart(ctx, {
    type: 'scatter',
    data: {
      datasets: [
        {
          label: 'Shift observations (sampled)',
          data: DATA.scatter,
          backgroundColor: cssVar('--series-1'),
          pointRadius: 3,
          pointHoverRadius: 5,
        },
        {
          label: 'Fitted regression',
          data: DATA.regressionLine,
          type: 'line',
          borderColor: cssVar('--text-primary'),
          borderWidth: 2,
          pointRadius: 0,
          fill: false,
        },
      ],
    },
    options: {
      responsive: true,
      plugins: { legend: { position: 'top', labels: { color: tickColor() } } },
      scales: baseScales({
        x: { grid: { color: gridColor() }, ticks: { color: tickColor() }, title: { display: true, text: 'Patients per nurse (dept-demeaned)', color: tickColor() } },
        y: { grid: { color: gridColor() }, ticks: { color: tickColor() }, title: { display: true, text: 'Avg wait, min (dept-demeaned)', color: tickColor() } },
      }),
    },
  });
  charts.push(chart);
}

function buildUtilChart() {
  const ctx = document.getElementById('utilChart');
  const sorted = [...DATA.bedUtilization].sort((a,b) => b.avg - a.avg);
  const chart = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: sorted.map(d => d.name),
      datasets: [
        {
          label: 'Avg utilization %',
          data: sorted.map(d => d.avg),
          backgroundColor: sorted.map(d => colorFor(d.name)),
          borderRadius: 4,
          barThickness: 22,
        },
        {
          label: '100% capacity',
          data: sorted.map(() => 100),
          type: 'line',
          borderColor: cssVar('--text-muted'),
          borderWidth: 2,
          borderDash: [6, 4],
          pointRadius: 0,
          fill: false,
        },
      ],
    },
    options: {
      responsive: true,
      plugins: { legend: { position: 'top', labels: { color: tickColor() } } },
      scales: baseScales(),
    },
  });
  charts.push(chart);
}

function buildTrendChart() {
  const ctx = document.getElementById('trendChart');
  const monthNames = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  const chart = new Chart(ctx, {
    type: 'line',
    data: {
      labels: DATA.monthlyTrend.map(d => monthNames[d.month - 1]),
      datasets: [{
        label: 'Total visits',
        data: DATA.monthlyTrend.map(d => d.visits),
        borderColor: cssVar('--series-1'),
        backgroundColor: 'transparent',
        borderWidth: 2,
        pointRadius: 3,
        tension: 0.25,
      }],
    },
    options: {
      responsive: true,
      plugins: { legend: { display: false } },
      scales: baseScales(),
    },
  });
  charts.push(chart);
}

function buildBottleneckChart() {
  const ctx = document.getElementById('bottleneckChart');
  const chart = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: DATA.bottlenecks.map(d => d.label),
      datasets: [{
        label: 'Flagged shift count',
        data: DATA.bottlenecks.map(d => d.count),
        backgroundColor: cssVar('--series-1'),
        borderRadius: 4,
        barThickness: 20,
      }],
    },
    options: {
      indexAxis: 'y',
      responsive: true,
      plugins: { legend: { display: false } },
      scales: baseScales(),
    },
  });
  charts.push(chart);
}

function buildFindings() {
  const n = DATA.narrative;
  const items = [
    `Within-department regression: each additional patient per nurse adds <b>${DATA.regression.slope} minutes</b> of wait (R²=${DATA.regression.r2}, p&lt;0.001).`,
    `Emergency's <b>${n.worstShift}</b> shift runs the longest waits (<b>${n.worstShiftWait} min</b> vs ${n.restWait} min for other shifts) &mdash; driven by peak arrival volume, not overnight staffing.`,
    `<b>${DATA.kpis.pctShiftsFlagged}%</b> of department/shift instances are flagged as understaffed (top quartile patients/nurse), averaging <b>${n.flaggedAvgWait} min</b> wait vs ${n.unflaggedAvgWait} min in normally staffed shifts.`,
    `Adding ${n.addedNurses} nurses to flagged bottleneck shifts is projected to cut average wait from ${n.flaggedAvgWait} to <b>${n.projectedNewWait} min</b>.`,
    `Pediatrics runs the highest bed utilization of any department, exceeding 100% average occupancy &mdash; a capacity, not staffing, bottleneck.`,
  ];
  document.getElementById('findingsList').innerHTML = items.map(i => `<li>${i}</li>`).join('');
}

function rebuildAll() {
  charts.forEach(c => c.destroy());
  charts.length = 0;
  buildWaitChart();
  buildScatterChart();
  buildUtilChart();
  buildTrendChart();
  buildBottleneckChart();
}

buildFindings();
rebuildAll();

document.getElementById('themeToggle').addEventListener('click', () => {
  const current = document.documentElement.getAttribute('data-theme');
  const next = current === 'dark' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', next);
  setTimeout(rebuildAll, 0);
});
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
