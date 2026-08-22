"""Bundle metrics.visualize's per-chart PNGs into one self-contained HTML
dashboard, so viewing a batch's charts is one `open reports/dashboard.html`
instead of opening seven separate PNG files by hand.

Reuses metrics.visualize.render_all() unchanged (same charts, same tested
code path) and just relocates its PNG output into <img> data URIs on one
page, via a throwaway temp directory. Nothing here recomputes or re-derives
a chart; this module only packages what visualize.py already produced.

Requires matplotlib (see metrics/visualize.py's docstring). When it isn't
installed, render_dashboard_html() still writes a valid HTML page that says
so, rather than leaving the artifact missing -- main.py publishes
dashboard.html unconditionally on every batch, so reports/dashboard.html
always resolves to *something* explainable rather than a broken link.
"""

import base64
import html
import os
import shutil
import tempfile

PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  :root {{
    --bg: #fcfcfb;
    --ink: #0b0b0b;
    --ink-secondary: #52514e;
    --card-bg: #ffffff;
    --border: #e1e0d9;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg: #16161a;
      --ink: #f2f1ee;
      --ink-secondary: #b8b6b0;
      --card-bg: #1e1e23;
      --border: #34333a;
    }}
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    padding: 2rem clamp(1rem, 4vw, 3rem) 4rem;
    background: var(--bg);
    color: var(--ink);
    font-family: -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
  }}
  header {{ margin-bottom: 2rem; }}
  h1 {{ font-size: 1.5rem; margin: 0 0 0.25rem; }}
  .subtitle {{ color: var(--ink-secondary); font-size: 0.95rem; }}
  .grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(420px, 1fr));
    gap: 1.5rem;
  }}
  figure {{
    margin: 0;
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 1rem;
  }}
  figure h2 {{
    font-size: 0.95rem;
    font-weight: 600;
    margin: 0 0 0.75rem;
    color: var(--ink-secondary);
    text-transform: uppercase;
    letter-spacing: 0.03em;
  }}
  img {{ max-width: 100%; height: auto; display: block; border-radius: 6px; }}
</style>
</head>
<body>
<header>
  <h1>{title}</h1>
  <div class="subtitle">{subtitle}</div>
</header>
<div class="grid">
{figures}
</div>
</body>
</html>
"""

FIGURE_TEMPLATE = """  <figure>
    <h2>{title}</h2>
    <img src="data:image/png;base64,{data}" alt="{title}">
  </figure>"""

MISSING_MATPLOTLIB_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
  body {{
    font-family: -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
    max-width: 40rem;
    margin: 4rem auto;
    padding: 0 1.5rem;
    line-height: 1.5;
  }}
  code {{ background: #eee; padding: 0.15rem 0.35rem; border-radius: 4px; }}
</style>
</head>
<body>
<h1>Charts unavailable</h1>
<p>This batch's <code>{title}</code> ran without <code>matplotlib</code> installed,
so no charts were rendered.</p>
<p>Install it and re-run the batch (or <code>python3 -m metrics.visualize</code>
against this run's <code>run_results.csv</code>) to fill this page in:</p>
<pre>pip install -r requirements-viz.txt</pre>
</body>
</html>
"""

NO_CHARTS_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
  body {{
    font-family: -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
    max-width: 40rem;
    margin: 4rem auto;
    padding: 0 1.5rem;
    line-height: 1.5;
  }}
  code {{ background: #eee; padding: 0.15rem 0.35rem; border-radius: 4px; }}
</style>
</head>
<body>
<h1>Charts skipped</h1>
<p>This batch ran with <code>--no-charts</code>, so no dashboard was rendered.</p>
<p>Regenerate it with:</p>
<pre>python3 -m metrics.visualize --csv run_results.csv --out . </pre>
</body>
</html>
"""


def render_dashboard_html(
    csv_path: str,
    out_html_path: str,
    title: str = "Farm Economy Batch Report",
    subtitle: str = "",
    dpi: int = 130,
    convergence_path: str = None,
    distributions_path: str = None,
) -> str:
    """Render every chart from `csv_path` into one self-contained HTML file
    at `out_html_path`. Always writes something -- a real dashboard if
    matplotlib is available and there is data to chart, otherwise a short
    explanatory page -- so callers never have to special-case a missing
    artifact.
    """
    try:
        import matplotlib  # noqa: F401
    except ImportError:
        _write(out_html_path, MISSING_MATPLOTLIB_TEMPLATE.format(title=html.escape(title)))
        return out_html_path

    from metrics import visualize

    tmp_dir = tempfile.mkdtemp(prefix="farm-dashboard-")
    try:
        chart_paths = visualize.render_all(
            csv_path,
            tmp_dir,
            dpi,
            show=False,
            convergence_path=convergence_path,
            distributions_path=distributions_path,
        )
        figures = []
        for path in chart_paths:
            name = os.path.splitext(os.path.basename(path))[0]
            chart_title = html.escape(name.replace("_", " ").capitalize())
            with open(path, "rb") as f:
                data = base64.b64encode(f.read()).decode("ascii")
            figures.append(FIGURE_TEMPLATE.format(title=chart_title, data=data))
        page = PAGE_TEMPLATE.format(
            title=html.escape(title),
            subtitle=html.escape(subtitle),
            figures="\n".join(figures),
        )
        _write(out_html_path, page)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    return out_html_path


def write_no_charts_placeholder(
    out_html_path: str, title: str = "Farm Economy Batch Report"
) -> str:
    """Placeholder for a batch run with --no-charts, so dashboard.html still
    exists as an artifact and explains why it's empty rather than 404ing.
    """
    _write(out_html_path, NO_CHARTS_TEMPLATE.format(title=html.escape(title)))
    return out_html_path


def _write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write(content)
