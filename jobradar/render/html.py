"""HTML discovery renderer — a sortable, ranked table (DESIGN §4.6)."""
import html
from datetime import datetime
from pathlib import Path

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>jobradar — discovery</title>
<style>
  :root {{ --bg:#0f1115; --card:#181b22; --line:#272b34; --txt:#e6e8ec; --muted:#9aa1ab; --accent:#5b9dff; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
         background:var(--bg); color:var(--txt); padding:24px; }}
  h1 {{ font-size:20px; margin:0 0 2px; }}
  .meta {{ color:var(--muted); margin-bottom:16px; }}
  .controls {{ margin-bottom:12px; }}
  input[type=search] {{ background:var(--card); border:1px solid var(--line); color:var(--txt);
         padding:8px 10px; border-radius:8px; width:280px; max-width:100%; }}
  table {{ border-collapse:collapse; width:100%; background:var(--card);
         border:1px solid var(--line); border-radius:10px; overflow:hidden; }}
  th, td {{ text-align:left; padding:10px 12px; border-bottom:1px solid var(--line); vertical-align:top; }}
  th {{ background:#1d2129; cursor:pointer; user-select:none; white-space:nowrap; position:sticky; top:0; }}
  th:hover {{ color:var(--accent); }}
  th .arrow {{ color:var(--muted); font-size:11px; }}
  tr:last-child td {{ border-bottom:none; }}
  tr:hover td {{ background:#1b1f27; }}
  .score {{ font-variant-numeric:tabular-nums; font-weight:600; }}
  .reason {{ color:var(--muted); max-width:380px; }}
  a {{ color:var(--accent); text-decoration:none; }}
  a:hover {{ text-decoration:underline; }}
  .pill {{ display:inline-block; min-width:28px; text-align:center; padding:2px 8px; border-radius:999px; }}
  .s-hi {{ background:#16331f; color:#7ee0a0; }}
  .s-mid {{ background:#33301a; color:#e8d27e; }}
  .s-lo {{ background:#2a2d34; color:#aeb4bf; }}
</style>
</head>
<body>
  <h1>jobradar — open matching roles</h1>
  <div class="meta">{count} roles · generated {generated} · click a column to sort</div>
  <div class="controls"><input type="search" id="q" placeholder="Filter by company, title, location…"></div>
  <table id="t">
    <thead><tr>
      <th data-k="score" data-num="1">Score <span class="arrow">▼</span></th>
      <th data-k="company">Company <span class="arrow"></span></th>
      <th data-k="title">Title <span class="arrow"></span></th>
      <th data-k="location">Location <span class="arrow"></span></th>
      <th data-k="reason">Reason <span class="arrow"></span></th>
      <th>Apply</th>
    </tr></thead>
    <tbody>
{rows}
    </tbody>
  </table>
<script>
  const table = document.getElementById('t');
  const tbody = table.querySelector('tbody');
  let sortKey = 'score', asc = false;
  function cell(tr, k) {{
    const idx = {{score:0, company:1, title:2, location:3, reason:4}}[k];
    return tr.children[idx].getAttribute('data-v') ?? tr.children[idx].textContent;
  }}
  function sortBy(k, numeric) {{
    asc = (sortKey === k) ? !asc : (k === 'score' ? false : true);
    sortKey = k;
    const rows = [...tbody.querySelectorAll('tr')];
    rows.sort((a,b) => {{
      let x = cell(a,k), y = cell(b,k);
      if (numeric) {{ x = parseFloat(x)||0; y = parseFloat(y)||0; return asc ? x-y : y-x; }}
      return asc ? String(x).localeCompare(y) : String(y).localeCompare(x);
    }});
    rows.forEach(r => tbody.appendChild(r));
    table.querySelectorAll('th .arrow').forEach(a => a.textContent='');
    const th = table.querySelector(`th[data-k="${{k}}"] .arrow`);
    if (th) th.textContent = asc ? '▲' : '▼';
  }}
  table.querySelectorAll('th[data-k]').forEach(th =>
    th.addEventListener('click', () => sortBy(th.dataset.k, th.dataset.num === '1')));
  document.getElementById('q').addEventListener('input', e => {{
    const term = e.target.value.toLowerCase();
    tbody.querySelectorAll('tr').forEach(tr =>
      tr.style.display = tr.textContent.toLowerCase().includes(term) ? '' : 'none');
  }});
</script>
</body>
</html>
"""


def _score_class(score: float) -> str:
    if score >= 5:
        return "s-hi"
    if score >= 3:
        return "s-mid"
    return "s-lo"


def write_html(rows, output_path: Path) -> int:
    """Write ranked job rows to a self-contained sortable HTML page. Returns row count."""
    trs = []
    count = 0
    for r in rows:
        score = r["match_score"] or 0.0
        company = html.escape(r["company"] or "")
        title = html.escape(r["title"] or "")
        location = html.escape(r["location"] or "")
        reason = html.escape(r["match_reason"] or "")
        url = html.escape(r["url"] or "", quote=True)
        apply = f'<a href="{url}" target="_blank" rel="noopener">open ↗</a>' if url else ""
        trs.append(
            "      <tr>"
            f'<td class="score" data-v="{score}"><span class="pill {_score_class(score)}">{score:g}</span></td>'
            f"<td>{company}</td>"
            f"<td>{title}</td>"
            f"<td>{location}</td>"
            f'<td class="reason">{reason}</td>'
            f"<td>{apply}</td>"
            "</tr>"
        )
        count += 1

    page = _PAGE.format(
        count=count,
        generated=datetime.now().strftime("%Y-%m-%d %H:%M"),
        rows="\n".join(trs),
    )
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(page)
    return count
