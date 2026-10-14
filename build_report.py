"""Render the simulator's saved results as a self-contained Chinese HTML report.

Only reads results; never generates or changes simulation observations. All charts,
tables and the ledger player run locally without libraries or a network connection.
"""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path


def build_report(data: dict, output_path: str | Path) -> None:
    """Write an offline report using the experiment schema in results.json."""
    if not isinstance(data.get("scenarios"), list) or not data["scenarios"]:
        raise ValueError("results.json must contain at least one scenario")
    # Escape script delimiters as well as HTML metacharacters before embedding JSON.
    payload = json.dumps(data, ensure_ascii=False, allow_nan=False)
    payload = (payload.replace("&", "\\u0026").replace("<", "\\u003c")
               .replace(">", "\\u003e").replace("\u2028", "\\u2028")
               .replace("\u2029", "\\u2029"))
    created = html.escape(str(data.get("metadata", {}).get("created_at", "未记录")))
    page = TEMPLATE.replace("__CREATED__", created).replace("__DATA__", payload)
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(page, encoding="utf-8")


TEMPLATE = r'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light">
<title>做市模拟器 · 从账本到风险实验</title>
<style>
:root{--ink:#182c3c;--muted:#526575;--paper:#f4f3ed;--card:#fff;--line:#dce2e2;--a:#3a6f9d;--b:#b75527;--green:#236650;--warn:#fff1df}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font-family:system-ui,-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;font-size:16px;line-height:1.75}main{max-width:1180px;margin:auto;padding:48px 30px 72px}h1,h2,h3,p{margin-top:0}h1{font-size:clamp(32px,5vw,54px);line-height:1.22;letter-spacing:-1px;margin-bottom:18px}h2{font-size:26px;line-height:1.4;margin-bottom:14px}h3{font-size:18px;margin-bottom:10px}p{margin-bottom:14px}a{color:#285c84;text-underline-offset:3px}button,select{font:inherit}button{border:1px solid #8fa0ab;background:#fff;color:var(--ink);padding:8px 16px;border-radius:8px;cursor:pointer}button:hover:not(:disabled){background:#edf2f5}button:disabled{opacity:.4;cursor:default}button:focus-visible,select:focus-visible,input:focus-visible{outline:3px solid #c37a25;outline-offset:3px}select{padding:10px 14px;border:1px solid #98a9b1;border-radius:8px;max-width:100%;background:white;color:var(--ink)}section{margin-top:34px}.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:26px}.eyebrow{font-size:12px;font-weight:750;letter-spacing:2px;color:var(--muted);text-transform:uppercase;margin-bottom:12px}.intro{font-size:19px;max-width:850px}.notice{background:var(--warn);border-left:4px solid #b7762b;padding:14px 18px;border-radius:0 8px 8px 0}.muted{color:var(--muted)}.small{font-size:13px}.grid{display:grid;gap:18px;grid-template-columns:repeat(2,minmax(0,1fr))}.cards{display:grid;gap:14px;grid-template-columns:repeat(4,minmax(0,1fr));margin:20px 0}.metric{background:#f7f9fa;border:1px solid var(--line);padding:16px;border-radius:10px}.metric strong{display:block;font-size:24px;line-height:1.35;margin:8px 0}.metric .tag{font-size:12px;font-weight:600;color:var(--muted)}.metric span{font-size:13px}.a{color:var(--a)}.b{color:var(--b)}.legend{display:flex;flex-wrap:wrap;gap:18px;font-size:14px;margin:8px 0 12px}.legend span:before{content:"";display:inline-block;width:22px;height:3px;background:currentColor;vertical-align:middle;margin-right:7px}.legend .mid{color:#74858c}.table-wrap{overflow:auto}table{width:100%;border-collapse:collapse;font-size:14px;font-variant-numeric:tabular-nums}th,td{padding:12px 10px;border-bottom:1px solid var(--line);text-align:right;vertical-align:top}th{background:#f5f7f8;font-weight:650}th:first-child,td:first-child{text-align:left}thead{white-space:nowrap}.formula{background:#f2f5f7;padding:13px 16px;border-radius:8px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:14px;overflow:auto;margin:10px 0 14px}.flow{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:18px 0}.flow div{padding:15px;background:#f2f5f7;border-radius:9px}.flow b{display:block;font-size:14px;margin-bottom:5px}.flow span{font-size:13px}.chart{width:100%;display:block;height:auto;min-height:210px}.chart text{font-family:inherit;fill:#526575;font-size:11px}.chart .chart-title{fill:var(--ink);font-size:13px;font-weight:600}.chart-caption{font-size:13px;color:var(--muted);margin:4px 0 0}.charts{display:grid;gap:20px}.chart-box{padding:12px;border:1px solid var(--line);border-radius:10px;overflow:hidden}.controls{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin:16px 0}.controls input{flex:1;min-width:120px;accent-color:var(--a)}.ledger-summary{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}.ledger-summary div{border-top:3px solid var(--a);background:#f5f8fa;padding:14px}.ledger-summary strong{display:block;font-size:25px;line-height:1.4}.ledger-summary small{display:block;color:var(--muted)}#ledger-event{min-height:120px}.pill{display:inline-block;padding:2px 9px;border-radius:14px;background:#e9f1ee;color:var(--green);font-size:13px;margin-right:6px}.details{border:1px solid var(--line);padding:14px 18px;border-radius:8px;margin-top:16px}summary{cursor:pointer;font-weight:650}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px;line-height:1.6}ul,ol{padding-left:23px;margin-top:8px}li{margin:8px 0}footer{border-top:1px solid #c6d0d4;margin-top:40px;padding-top:22px;font-size:13px;color:var(--muted)}.note-list{padding-left:20px}.section-number{font-size:12px;font-weight:700;color:var(--muted);letter-spacing:1.5px;margin-bottom:8px}.conclusion{border-left:4px solid var(--green);background:#eff6f2;padding:16px 20px;margin-top:18px;border-radius:0 8px 8px 0}.zero-line{stroke:#9ba8ae;stroke-dasharray:3 3}.error{color:#9c332a;white-space:pre-wrap}@media(max-width:760px){main{padding:28px 16px 45px}.card{padding:19px}.grid{grid-template-columns:1fr}.cards,.ledger-summary,.flow{grid-template-columns:repeat(2,minmax(0,1fr))}.metric strong{font-size:21px}h2{font-size:23px}th,td{padding:10px 8px} .intro{font-size:17px}}@media(max-width:420px){.cards,.ledger-summary{grid-template-columns:1fr 1fr}.metric{padding:12px}.metric strong{font-size:19px}.flow{grid-template-columns:1fr}.controls button{padding:7px 11px}}@media print{body{background:white}main{max-width:none;padding:12px}.card{break-inside:avoid;box-shadow:none}button,input,select{display:none}.chart{max-height:270px}section{margin-top:22px}.notice{border:1px solid #b7762b}}
</style>
</head>
<body><main>
<header>
<div class="eyebrow">MARKET MAKING LAB · 可复现的教学研究</div>
<h1>做市模拟器<br>从一笔账，到一项风险实验。</h1>
<p class="intro">研究问题：<strong>根据库存调整报价，能否减少风险？为了减少库存，需要牺牲多少成交和收入？</strong></p>
<div class="notice"><strong>这是合成市场模拟，不是真实行情回测。</strong>结果只回答设定规则下的比较。它不证明策略可以在现实市场赚钱，也不是投资建议。所有图表读取已保存的实验结果。</div>
<p class="small muted" id="run-meta" style="margin-top:13px"></p>
</header>

<section class="card">
<div class="section-number">01 / 先看懂这个世界</div><h2>你是持续报买价和卖价的交易员</h2>
<div class="grid"><div><h3>策略 A · 固定价差</h3><p>围绕当前参考价，向下报买价 Bid，向上报卖价 Ask。每一步都会随参考价重新报价，价差保持固定。</p><div class="formula">Bid = mid − half_spread<br>Ask = mid + half_spread</div></div><div><h3>策略 B · 根据库存移动报价</h3><p>库存偏多时降低买卖报价，鼓励卖出；库存为负时提高报价，鼓励买回。调整力度由参数 k 决定。</p><div class="formula">centre = mid − k × inventory<br>Bid = centre − half_spread<br>Ask = centre + half_spread</div></div></div>
<p class="small muted">以上是未触及边界时的公式。代码会限制报价中心的偏移，并按最小报价单位取整，使买价和卖价始终包围参考价。这是透明的教学规则，并非实现了完整的 Avellaneda–Stoikov 最优做市模型。参数、库存限制和费用见本页末的实验配置。</p>
<div class="flow"><div><b>① 环境提供当前信息</b><span>当前参考价与自己的现金、库存已知；未来价格不可见。</span></div><div><b>② 策略先报价</b><span>策略不能偷看客户愿付价格或下一步参考价来修改本次报价。</span></div><div><b>③ 客户选择是否成交</b><span>客户买入要求 Ask ≤ 最高愿付价格；客户卖出要求 Bid ≥ 最低愿收价格。</span></div><div><b>④ 记账，再更新价格</b><span>成交更新现金和库存；下一参考价用于给库存重新估值。</span></div></div>
</section>

<section class="card" aria-labelledby="ledger-title">
<div class="section-number">02 / 可以逐笔播放的账本</div><h2 id="ledger-title">先理解每一元盈亏从哪里来</h2>
<p id="lesson-title" class="muted"></p><p id="lesson-config" class="small muted"></p>
<div class="controls"><button id="prev" type="button">← 上一笔</button><button id="next" type="button">下一笔 →</button><label for="ledger-range" class="small">步骤</label><input id="ledger-range" type="range" min="0" value="0"><span id="ledger-position" class="small"></span></div>
<div id="ledger-event"></div><div class="ledger-summary" id="ledger-values"></div>
<div class="formula">总资产 = 现金 + 库存 × 当前参考价<br>累计盈亏 = 当前总资产 − 初始总资产<br>本步盈亏 = 成交时的价格优势 + 持仓价格变化 − 本步手续费</div>
<p class="small muted" id="ledger-decomposition"></p>
<p class="small muted">本教学账本不自动执行期末清仓。研究实验的最终结果则计入期末清仓成本。账本里的“股票价格”只是模型参考价。</p>
</section>

<section class="card" aria-labelledby="compare-title">
<div class="section-number">03 / 用相同环境比较两种策略</div><h2 id="compare-title">库存更低，不代表利润一定更高</h2>
<label for="scenario" class="small">选择研究情景</label><br><select id="scenario"></select>
<p id="scenario-description" class="muted" style="margin-top:14px"></p>
<div class="legend"><span class="a">A 固定价差</span><span class="b">B 库存调整</span></div>
<div id="metrics" class="cards"></div>
<div class="table-wrap"><table><thead><tr><th>指标</th><th>A 固定价差</th><th>B 库存调整</th><th>怎样理解</th></tr></thead><tbody id="comparison-table"></tbody></table></div>
<div id="scenario-conclusion" class="conclusion"></div>
<p id="paired-note" class="small muted" style="margin-top:13px"></p><details class="details"><summary>查看本情景的市场参数</summary><pre id="scenario-config"></pre></details>
</section>

<section class="card"><div class="section-number">04 / 看全体情景，避免只挑好看的结果</div><h2>利润、成交和尾部损失一起比较</h2>
<div class="chart-box"><svg id="scenario-bars" class="chart" role="img" aria-label="各情景两种策略平均期末盈亏比较"></svg></div>
<p class="chart-caption">柱形为每个情景全部随机种子的平均清仓后盈亏。金额单位为模型货币单位，不是实际投资回报。</p>
<div class="table-wrap" style="margin-top:16px"><table><thead><tr><th>情景 / 策略</th><th>平均盈亏</th><th>平均成交笔数</th><th>平均绝对库存</th><th>最差 5% 均值</th></tr></thead><tbody id="all-scenarios"></tbody></table></div>
</section>

<section class="card"><div class="section-number">05 / 一条路径能解释机制，不能证明稳健性</div><h2>预先固定的一次实验</h2>
<p id="example-label" class="muted"></p><p class="small muted">下面三幅图只属于这一条固定示例。切换上方情景选择器不会改变它们；完整结论来自全部种子。</p>
<div class="legend"><span class="a">A 固定价差</span><span class="b">B 库存调整</span><span class="mid">参考价</span></div>
<div class="charts"><div class="chart-box"><svg id="price-chart" class="chart" role="img" aria-label="预先固定示例的参考价和两策略报价"></svg><p class="chart-caption">报价与参考价都取本步成交前的信息；实线为买价，虚线为卖价。</p></div><div class="chart-box"><svg id="inventory-chart" class="chart" role="img" aria-label="预先固定示例的两策略库存路径"></svg><p class="chart-caption">库存取每步成交后。正数是持有股票，负数是卖空后有待买回的数量。</p></div><div class="chart-box"><svg id="pnl-chart" class="chart" role="img" aria-label="预先固定示例的两策略累计盈亏路径"></svg><p class="chart-caption">每步盈亏按下一参考价估值，已扣成交手续费；这条过程曲线未扣期末清仓成本，汇总统计已扣。</p></div></div>
</section>

<section class="card"><div class="section-number">06 / 改变假设，结论还成立吗？</div><h2>参数敏感性</h2><p class="muted">每组只改变记录中的参数，其余条件固定。同组的两策略在同一随机环境下配对比较。B − A 为正表示库存策略平均盈亏更高。</p><p id="sensitivity-count" class="small muted"></p><div class="table-wrap"><table><thead><tr><th>改动</th><th>情景</th><th>A 平均盈亏</th><th>B 平均盈亏</th><th>B − A</th><th>差值 95% 区间</th></tr></thead><tbody id="sensitivity-table"></tbody></table></div><div id="sensitivity-configs" class="small muted" style="margin-top:14px"></div></section>

<section class="card"><div class="section-number">07 / 可信度来自哪些检查</div><h2>这个实验能说什么，还有什么说不了</h2><div class="grid"><div><h3>比较时保持一致</h3><ul class="note-list"><li>每对策略使用同一份预先生成的价格与客户事件，不能改变随机数消耗顺序来制造“相同种子”。</li><li>相同初始资金、库存约束、手续费和清仓方式。实际成交可以不同，因为报价不同。</li><li>报价不满足客户要求时不会成交。客户方向始终按客户视角命名。</li><li>收益分成成交价格优势、库存价格变化、成交手续费及期末清仓成本。</li></ul></div><div><h3>保留这些边界</h3><ul class="note-list"><li id="limit-description">研究模式允许有界的正负库存；负库存代表卖空。入门账本可以采用更简单的持仓范围。</li><li>模型不包含真实订单簿队列、交易所撮合延迟、竞争做市商，也没有根据实盘数据校准。</li><li>固定报价宽度和线性库存规则只是一组可解释的比较，不能称为最优策略。</li><li>95% 区间反映本模型的蒙特卡洛抽样误差，不能衡量模型距离现实有多远。</li></ul></div></div>
<div class="notice"><strong>为什么不能把模拟收入当成真实优势？</strong>本模型要求买卖报价包围参考价，因此成交当时的价格优势非负是规则带来的。普通客户的价格底线又与未来价格独立，本身提供了赚价差的机会。必须结合知情客户、费用提高和清仓成本等压力结果来看；这些假设没有经过真实行情校准。</div>
<details class="details"><summary>查看这次研究实验的完整配置与生成时间</summary><pre id="config"></pre></details>
</section>

<section class="card"><div class="section-number">08 / 读完后，你应当能解释</div><h2>把结果变成自己的理解</h2><ol><li>从现金、库存和参考价出发，手算一笔买入、一笔卖出和一次价格下跌。</li><li>说明为什么库存调整会改变成交机会，以及为什么客户“想交易”不等于一定成交。</li><li>从本页挑一个情景，用利润、库存、尾部结果三个数字解释取舍。</li><li>说出一个你改变过的假设，展示变化后的结果，并解释模型还遗漏了什么。</li></ol><p><strong>贡献说明：</strong>本项目使用 Codex 辅助实现。方法、模型假设与复现步骤见项目文档。</p></section>

<footer><p>理论与业务参考：<a href="https://math.nyu.edu/inmemoriam/avellaneda/HighFrequencyTrading.pdf" target="_blank" rel="noopener noreferrer">Avellaneda–Stoikov 做市论文</a> · <a href="https://prod-www.optiver.com/insights/explainers/market-making/" target="_blank" rel="noopener noreferrer">Optiver：做市解释</a>。公式和实现以本项目文件为准；这里只借鉴库存影响报价的基本思想。</p><p>结果生成时间：__CREATED__。离线 HTML，图表与账本数据嵌入本文件。刷新页面不会重新运行实验。</p></footer>
</main>
<script id="report-data" type="application/json">__DATA__</script>
<script>
'use strict';
const data = JSON.parse(document.getElementById('report-data').textContent);
const el = id => document.getElementById(id);
const A = '#3a6f9d', B = '#b75527', M = '#74858c';
const n = (v,d=2) => typeof v === 'number' && Number.isFinite(v) ? v.toLocaleString('zh-CN',{minimumFractionDigits:d,maximumFractionDigits:d}) : '—';
const pct = v => typeof v === 'number' && Number.isFinite(v) ? n(v*100,1)+'%' : '—';
const esc = s => String(s ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const signed = v => typeof v === 'number' && v>0 ? '+'+n(v) : n(v);
const finite = v => typeof v === 'number' && Number.isFinite(v);
const value = (object,key,fallback=0) => finite(object?.[key]) ? object[key] : fallback;
const metadata = data.metadata || {}, config = metadata.config || {};
el('run-meta').textContent = `每情景 ${n(metadata.seed_count,0)} 个种子 × ${n(metadata.steps,0)} 步 · 起始种子 ${metadata.seed_start ?? '未记录'} · 生成时间 ${metadata.created_at ?? '未记录'}`;
el('config').textContent = JSON.stringify(metadata,null,2);
const inventoryLimit = config.inventory_limit ?? config.max_inventory;
if(finite(inventoryLimit)) el('limit-description').textContent = `研究模式允许 −${inventoryLimit} 至 +${inventoryLimit} 股的有界库存。负库存代表卖空；借券可得性、保证金与融资成本等现实约束未在本模型中完整实现。`;

function metric(title,key,format=n){
  const s = data.scenarios[Number(el('scenario').value)], a=s.strategies.fixed, b=s.strategies.inventory;
  return `<div class="metric"><div class="tag">${title}</div><strong class="a">${format(a[key])}</strong><span>A 固定价差</span><strong class="b">${format(b[key])}</strong><span>B 库存调整</span></div>`;
}
function drawScenario(){
  const s = data.scenarios[Number(el('scenario').value)], a=s.strategies.fixed, b=s.strategies.inventory, p=s.paired || {};
  el('scenario-description').textContent = s.description || '';
  el('scenario-config').textContent = JSON.stringify(s.parameters||{},null,2);
  el('metrics').innerHTML = metric('清仓后平均盈亏','mean_pnl')+metric('亏损概率','loss_probability',pct)+metric('平均绝对库存 / 股','mean_abs_inventory')+metric('最差 5% 平均盈亏','tail_mean_pnl');
  const rows=[['平均盈亏','mean_pnl','每个种子的期末清仓后损益，再取均值'],['盈亏中位数','median_pnl','一半结果高于它，一半低于它'],['亏损概率','loss_probability','清仓后亏损的种子占比',pct],['最差 5% 均值','tail_mean_pnl','最差一组结果的平均值；越高越好'],['平均成交笔数','mean_trades','检查风险降低是否伴随较少交易'],['平均绝对库存','mean_abs_inventory','每条路径对 |库存| 取时间均值，再跨路径平均'],['平均最大绝对库存','mean_max_inventory','每条路径的最大 |库存|，再跨路径平均'],['平均成交价格优势','mean_spread_capture','成交相对当时参考价的累计优势'],['平均库存损益','mean_inventory_pnl','成交后库存承受后续价格变化的损益'],['平均成交手续费','mean_fees','成交发生时支付的费用'],['平均清仓成本','mean_liquidation_cost','期末剩余持仓平仓的额外成本']];
  el('comparison-table').innerHTML=rows.map(([title,key,meaning,formatter=n])=>`<tr><td>${title}</td><td class="a">${formatter(a[key])}</td><td class="b">${formatter(b[key])}</td><td class="muted">${meaning}</td></tr>`).join('');
  const inventoryChange=b.mean_abs_inventory-a.mean_abs_inventory;
  let conclusion=`在“${s.label || s.name}”中，B 的平均盈亏比 A ${p.mean_difference >= 0?'高':'低'} ${n(Math.abs(p.mean_difference))}；平均绝对库存${inventoryChange < 0?'降低':'增加'} ${n(Math.abs(inventoryChange))} 股。`;
  if(p.ci_low<=0 && p.ci_high>=0) conclusion+=' 配对差值区间包含 0，本次抽样不足以清楚分辨平均盈亏的高低。';
  else if(p.ci_low>0) conclusion+=' 本次配对模拟中，B 的平均盈亏较高；这个结果仍依赖此处的市场假设。';
  else if(p.ci_high<0) conclusion+=' 本次配对模拟中，B 的平均盈亏较低；需要结合库存和尾部指标评估这项代价。';
  conclusion+=` 亏损概率：A ${pct(a.loss_probability)}，B ${pct(b.loss_probability)}；最差 5% 平均盈亏：A ${n(a.tail_mean_pnl)}，B ${n(b.tail_mean_pnl)}。`;
  if((inventoryChange<0 || p.mean_difference>0) && b.loss_probability>a.loss_probability) conclusion+=' 注意：即使库存较低或平均收益较高，B 仍然更经常亏损；风险指标并不总朝同一个方向变化。';
  else if(b.tail_mean_pnl<a.tail_mean_pnl && (inventoryChange<0 || p.mean_difference>0)) conclusion+=' 注意：B 的最差一组结果更差；库存下降或平均收益上升不能单独代表风险改善。';
  el('scenario-conclusion').textContent=conclusion;
  el('paired-note').textContent=`对每个相同种子先计算 B − A，再统计平均差值 ${signed(p.mean_difference)}，标准误 ${n(p.standard_error)}，95% 区间 [${n(p.ci_low)}, ${n(p.ci_high)}]。区间只描述固定模型下的抽样误差。`;
}
data.scenarios.forEach((s,i)=>{const option=document.createElement('option');option.value=i;option.textContent=s.label||s.name;el('scenario').append(option)});
el('scenario').addEventListener('change',drawScenario);drawScenario();

function chart(svg,title,series,includeZero=false){
  const W=1000,H=260,L=65,R=22,T=34,D=37,w=W-L-R,h=H-T-D;
  const valid=series.flatMap(s=>s.points.filter(p=>finite(p.x)&&finite(p.y)));
  svg.setAttribute('viewBox',`0 0 ${W} ${H}`);
  if(!valid.length){svg.innerHTML='<text x="20" y="35">没有可用的路径数据。</text>';return}
  let x0=Math.min(...valid.map(p=>p.x)),x1=Math.max(...valid.map(p=>p.x));
  let y0=Math.min(...valid.map(p=>p.y)),y1=Math.max(...valid.map(p=>p.y));
  if(includeZero){y0=Math.min(0,y0);y1=Math.max(0,y1)}
  const pad=(y1-y0)*.1 || 1;y0-=pad;y1+=pad;if(x0===x1)x1=x0+1;
  const X=x=>L+(x-x0)/(x1-x0)*w,Y=y=>T+(y1-y)/(y1-y0)*h;
  let html=`<text class="chart-title" x="${L}" y="19">${esc(title)}</text>`;
  for(let i=0;i<=4;i++){const v=y0+(y1-y0)*i/4,y=Y(v);html+=`<line x1="${L}" y1="${y}" x2="${W-R}" y2="${y}" stroke="#e4e8ea"/><text text-anchor="end" x="${L-9}" y="${y+4}">${n(v,1)}</text>`}
  for(let i=0;i<=5;i++){const x=x0+(x1-x0)*i/5;html+=`<text text-anchor="middle" x="${X(x)}" y="${H-14}">${n(x,0)}</text>`}
  if(y0<0&&y1>0)html+=`<line x1="${L}" y1="${Y(0)}" x2="${W-R}" y2="${Y(0)}" class="zero-line"/>`;
  series.forEach(s=>{const points=s.points.filter(p=>finite(p.x)&&finite(p.y)).map(p=>`${X(p.x).toFixed(2)},${Y(p.y).toFixed(2)}`).join(' ');html+=`<polyline points="${points}" fill="none" stroke="${s.color}" stroke-width="${s.width||1.8}" ${s.dashed?'stroke-dasharray="5 4"':''}><title>${esc(s.label)}</title></polyline>`});
  html+=`<text text-anchor="end" x="${W-R}" y="${H-1}">步数</text>`;svg.innerHTML=html;
}

function bars(){
 const ss=data.scenarios,W=1000,H=Math.max(260,ss.length*90+65),L=190,R=100,T=25,D=25,w=W-L-R;
 const values=ss.flatMap(s=>[s.strategies.fixed.mean_pnl,s.strategies.inventory.mean_pnl]).filter(finite);let lo=Math.min(0,...values),hi=Math.max(0,...values);const pad=(hi-lo)*.08||1;lo-=pad;hi+=pad;const X=v=>L+(v-lo)/(hi-lo)*w;
 let html='';for(let i=0;i<=4;i++){const v=lo+(hi-lo)*i/4;html+=`<line x1="${X(v)}" y1="${T}" x2="${X(v)}" y2="${H-D}" stroke="#e4e8ea"/><text text-anchor="middle" x="${X(v)}" y="${H-7}">${n(v,0)}</text>`}html+=`<line x1="${X(0)}" y1="${T}" x2="${X(0)}" y2="${H-D}" stroke="#899ca8"/>`;
 ss.forEach((s,i)=>{const y=T+i*(H-T-D)/ss.length;html+=`<text text-anchor="end" x="${L-15}" y="${y+31}">${esc(s.label||s.name)}</text>`;['fixed','inventory'].forEach((key,j)=>{const v=s.strategies[key].mean_pnl;if(!finite(v))return;const xx=X(v),z=X(0),yy=y+j*25+7;html+=`<rect x="${Math.min(xx,z)}" y="${yy}" width="${Math.max(Math.abs(xx-z),.8)}" height="19" rx="2" fill="${j?B:A}"/><text x="${xx+(v>=0?7:-7)}" y="${yy+14}" text-anchor="${v>=0?'start':'end'}">${n(v)}</text>`})});
 const svg=el('scenario-bars');svg.setAttribute('viewBox',`0 0 ${W} ${H}`);svg.innerHTML=html;
}
bars();el('all-scenarios').innerHTML=data.scenarios.map(s=>['fixed','inventory'].map((key,i)=>{const x=s.strategies[key];return `<tr><td>${esc(s.label||s.name)} / <span class="${i?'b':'a'}">${i?'B':'A'}</span></td><td>${n(x.mean_pnl)}</td><td>${n(x.mean_trades)}</td><td>${n(x.mean_abs_inventory)}</td><td>${n(x.tail_mean_pnl)}</td></tr>`}).join('')).join('');
const example=data.example||{},traces=example.traces||{},at=traces.fixed||[],bt=traces.inventory||[];
el('example-label').textContent=`固定情景：${example.label||example.scenario||'未记录'}；预先指定种子：${example.seed??'未记录'}。未按照表现挑选种子。`;
const pts=(rows,key)=>rows.map((r,i)=>({x:r.step??i+1,y:r[key]}));
chart(el('price-chart'),'本步参考价与买卖报价',[{points:pts(at,'mid_price'),color:M,label:'参考价',width:2.3},{points:pts(at,'bid'),color:A,label:'A 买价'},{points:pts(at,'ask'),color:A,label:'A 卖价',dashed:true},{points:pts(bt,'bid'),color:B,label:'B 买价'},{points:pts(bt,'ask'),color:B,label:'B 卖价',dashed:true}]);
chart(el('inventory-chart'),'成交后库存 / 股',[{points:pts(at,'inventory'),color:A,label:'A 库存'},{points:pts(bt,'inventory'),color:B,label:'B 库存'}],true);
chart(el('pnl-chart'),'累计账面盈亏 / 模型货币单位',[{points:pts(at,'pnl'),color:A,label:'A 盈亏'},{points:pts(bt,'pnl'),color:B,label:'B 盈亏'}],true);

const sensitivity=data.sensitivity||[], scenarioLabels=Object.fromEntries(data.scenarios.map(s=>[s.name,s.label||s.name]));
el('sensitivity-table').innerHTML=sensitivity.length?sensitivity.map(s=>{const p=s.paired||{};return `<tr><td>${esc(s.label||s.name)}</td><td>${esc(scenarioLabels[s.scenario_name]||s.scenario_name||'')}</td><td>${n(s.strategies?.fixed?.mean_pnl)}</td><td>${n(s.strategies?.inventory?.mean_pnl)}</td><td>${signed(p.mean_difference)}</td><td>[${n(p.ci_low)}, ${n(p.ci_high)}]</td></tr>`}).join(''):'<tr><td colspan="6">这次结果中未包含参数敏感性实验。</td></tr>';
el('sensitivity-count').textContent=`每组敏感性检查 ${n(metadata.sensitivity_seed_count,0)} 个配对种子。`;
el('sensitivity-configs').innerHTML=sensitivity.map(s=>`<div>${esc(s.label||s.name)}：${esc(JSON.stringify({...s.config_changes,...s.scenario_changes}))}</div>`).join('');

const lesson=data.lesson||{}, ledger=lesson.trace||[];let position=0;
const initialCash=value(lesson,'initial_cash'),initialInventory=value(lesson,'initial_inventory'),initialMid=value(lesson,'initial_mid');
const initialEquity=initialCash+initialInventory*initialMid;
el('lesson-title').textContent=lesson.title||'每次查看一位客户，以及成交后的账户变化。';el('ledger-range').max=ledger.length;
el('lesson-config').textContent=`此处为人工固定教学事件，采用${lesson.strategy==='fixed'?'固定价差':'库存调整'}策略；每笔手续费 ${n(lesson.config?.fee_per_trade)}，${lesson.config?.allow_short===false?'不允许卖空':'允许有界卖空'}。它与下面研究实验的配置分开。`;
const reasons={filled:'成交',no_customer:'没有客户到来',price_rejected:'客户不接受报价',price_not_accepted:'客户不接受报价',price_not_acceptable:'客户不接受报价',inventory_limit:'达到库存限制',insufficient_cash:'现金不足',insufficient_inventory:'库存不足',no_inventory:'没有股票可卖',no_trade:'未成交'};
function drawLedger(){
 const r=position?ledger[position-1]:null;
 const cash=r?value(r,'cash'):initialCash,inventory=r?value(r,'inventory'):initialInventory,mid=r?value(r,'next_mid',value(r,'mid_price')):initialMid,equity=r?value(r,'equity',cash+inventory*mid):initialEquity,pnl=r?value(r,'pnl',equity-initialEquity):0;
 el('ledger-range').value=position;el('prev').disabled=position===0;el('next').disabled=position===ledger.length;el('ledger-position').textContent=position===0?'初始状态':`第 ${position} / ${ledger.length} 笔事件`;
 el('ledger-values').innerHTML=[['现金',n(cash),'成交时变化'],['库存 / 股',n(inventory,0),'负数表示卖空'],['总资产',n(equity),`按参考价 ${n(mid)} 估值`],['累计盈亏',signed(pnl),`初始总资产 ${n(initialEquity)}`]].map(([label,v,sub])=>`<div><span class="small">${label}</span><strong>${v}</strong><small>${sub}</small></div>`).join('');
 if(!r){el('ledger-event').innerHTML=`<p><span class="pill">初始状态</span>现金 ${n(initialCash)}，库存 ${n(initialInventory,0)} 股，参考价 ${n(initialMid)}。</p><p>初始总资产 = ${n(initialCash)} + ${n(initialInventory,0)} × ${n(initialMid)} = <strong>${n(initialEquity)}</strong>。点击“下一笔”观察交易。</p>`;el('ledger-decomposition').textContent='初始持仓本身也是资产。已有股票时，初始总资产不能只算现金。';return}
 const side=r.customer_side, sideLabel=side==='buy'?'客户买入，你卖出':side==='sell'?'客户卖出，你买入':'本步没有交易客户';
 const comparison=side==='buy'?`你的 Ask ${n(r.ask)} ${r.ask<=r.limit_price?'≤':'>'} 客户最高愿付 ${n(r.limit_price)}`:side==='sell'?`你的 Bid ${n(r.bid)} ${r.bid>=r.limit_price?'≥':'<'} 客户最低愿收 ${n(r.limit_price)}`:'没有可匹配的客户意愿';
 const reason=reasons[r.reason]||r.reason||'没有满足成交条件';
 const outcome=r.filled?`按你报出的 ${n(r.execution_price)} 成交 ${n(Math.abs(r.quantity??1),0)} 股，手续费 ${n(r.fee)}。`:`未成交：${reason}。`;
 el('ledger-event').innerHTML=`<p><span class="pill">${esc(sideLabel)}</span>本步报价：Bid <strong>${n(r.bid)}</strong> / Ask <strong>${n(r.ask)}</strong>。</p><p>${esc(comparison)}。${esc(outcome)}参考价随后从 ${n(r.mid_price)} 变为 ${n(mid)}。</p>`;
 const slice=ledger.slice(0,position),edge=slice.reduce((sum,x)=>sum+value(x,'edge'),0),inventoryPnl=slice.reduce((sum,x)=>sum+value(x,'inventory_pnl'),0),fees=slice.reduce((sum,x)=>sum+value(x,'fee'),0);
 el('ledger-decomposition').textContent=`本步：成交价格优势 ${signed(r.edge)} + 库存损益 ${signed(r.inventory_pnl)} − 手续费 ${n(r.fee)}。累计：${n(edge)} + (${n(inventoryPnl)}) − ${n(fees)} = ${n(edge+inventoryPnl-fees)}。价格下跌可能抵消成交时赚到的优势。`;
}
el('prev').addEventListener('click',()=>{position=Math.max(0,position-1);drawLedger()});el('next').addEventListener('click',()=>{position=Math.min(ledger.length,position+1);drawLedger()});el('ledger-range').addEventListener('input',e=>{position=Number(e.target.value);drawLedger()});drawLedger();
</script></body></html>'''


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the offline market-making report")
    parser.add_argument("input", nargs="?", type=Path, default=Path(__file__).parent / "results/results.json")
    parser.add_argument("output", nargs="?", type=Path, default=Path(__file__).parent / "results/report.html")
    args = parser.parse_args()
    with args.input.open(encoding="utf-8") as source:
        data = json.load(source)
    build_report(data, args.output)
    print(args.output.resolve())


if __name__ == "__main__":
    main()
