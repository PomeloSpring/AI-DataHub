#!/usr/bin/env node
/**
 * 从 docs/ARCHITECTURE_OVERVIEW.md 生成自包含演示页 docs/ARCHITECTURE_OVERVIEW.html
 *
 * 口径（重要）：
 * - **markdown 是唯一事实源**，本脚本只做呈现转换，不新增也不删减结论；
 * - 4 张手绘矢量图（architecture/fig*.svg）**内联**进 HTML → 单文件可离线/邮件/打印；
 * - Mermaid 图不靠浏览器渲染（离线无文本度量，布局会退化成错乱直线，已验证不可靠），
 *   而是解析图定义后渲染为 HTML/CSS 原生结构：线性流程→箭头链，含分支→节点卡+出边标签，
 *   时序图→编号步骤表；每个图形下方保留原始图定义（<details>）供核对与再编辑。
 *
 * 用法：node docs/build-architecture-html.mjs   （无第三方依赖）
 */
import fs from 'node:fs';
import path from 'node:path';
import url from 'node:url';

const HERE = path.dirname(url.fileURLToPath(import.meta.url));
const MD = path.join(HERE, 'ARCHITECTURE_OVERVIEW.md');
const OUT = path.join(HERE, 'ARCHITECTURE_OVERVIEW.html');

/* ───────────────────────── 工具 ───────────────────────── */
const esc = s => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
const nl2br = s => s.replace(/&lt;br\s*\/?&gt;/g, '<br>').replace(/<br\/>/g, '<br>');

/** 行内 markdown → HTML（先转义，再按 `` / ** / []() / ![]() 顺序还原） */
function inline(text) {
  let s = esc(text);
  const imgs = [];
  s = s.replace(/!\[([^\]]*)\]\(([^)]+)\)/g, (_, alt, src) => {
    imgs.push({ alt, src });
    return `\u0000IMG${imgs.length - 1}\u0000`;
  });
  s = s.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2">$1</a>');
  s = s.replace(/`([^`]+)`/g, (_, c) => `<code>${c}</code>`);
  s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>');
  s = s.replace(/\u0000IMG(\d+)\u0000/g, (_, n) => figure(imgs[+n]));
  return s;
}

/** 矢量图：读 svg 源码内联，保证单文件离线可看 */
let figSeq = 0;
const figAnchor = alt => 'fig-' + alt.replace(/[^\w\u4e00-\u9fa5]+/g, '-').slice(0, 24);
function figure({ alt, src }) {
  if (!/^architecture\/.*\.svg$/.test(src)) return `<img alt="${esc(alt)}" src="${esc(src)}">`;
  // 索引节不重复内联同一张图（全文仅一份 SVG 实例，避免体积与 id 冲突）
  if (currentSection.includes('图索引'))
    return `<a class="figlink" href="#${figAnchor(alt)}">↓ ${esc(alt)}</a>`;
  const file = path.join(HERE, src);
  if (!fs.existsSync(file)) return `<div class="warn">缺图：${esc(src)}</div>`;
  // 内联多份 SVG 时必须隔离内部 id（marker 引用按文档解析，重名会互串）
  const n = ++figSeq;
  const ids = new Map();
  const svg = fs.readFileSync(file, 'utf8')
    .replace(/^<svg/, `<svg id="fs${n}" class="figsvg" preserveAspectRatio="xMidYMid meet"`)
    .replace(/\bid="([\w-]+)"/g, (m, i) => { const k = `f${n}-${i}`; ids.set(i, k); return `id="${k}"`; })
    .replace(/url\(#([\w-]+)\)/g, (m, i) => `url(#${ids.get(i) || i})`);
  return `<figure class="fig" id="${figAnchor(alt)}"><div class="figwrap">${svg}</div><figcaption>${esc(alt)}</figcaption></figure>`;
}

/* ─────────────────── 图定义解析（flowchart / sequence） ─────────────────── */
// 只收引号包起的标签（本仓图定义一律 `ID["..."]` / `ID{"..."}` / `ID[("...")]`）；
// 不收裸方括号是为了不把标签内的 `mask(full,partial,hash)` 误当成节点定义。
const NODE_RE = /([A-Za-z0-9_]+)\s*(?:\[\(\s*"([^"]*)"\s*\)\]|\[\s*"([^"]*)"\s*\]|\{\s*"([^"]*)"\s*\})/g;
const idOf = t => { const m = (t || '').match(/^\s*([A-Za-z0-9_]+)/); return m ? m[1] : null; };

/** 归一化箭头：`-.lbl.->` → `-->|lbl|`；`-.->`/`==>` → `-->` */
function parseFlow(src) {
  const nodes = new Map();   // id → label
  const edges = [];          // {from,to,label,dashed}
  const groups = new Map();  // subgraph id → title
  for (const raw of src.split('\n')) {
    const line = raw.trim();
    if (!line || /^(flowchart|graph|sequenceDiagram)/.test(line)) continue;
    const sg = line.match(/^subgraph\s+([A-Za-z0-9_]+)\s*\[\s*"?(.*?)"?\s*\]\s*$/);
    if (sg) { groups.set(sg[1], sg[2] || sg[1]); continue; }
    if (/^end$/.test(line)) continue;
    for (const m of line.matchAll(NODE_RE)) {
      const id = m[1], label = (m[2] ?? m[3] ?? m[4] ?? '').trim();
      if (label && !nodes.has(id)) nodes.set(id, nl2br(esc(label)));
    }
    const dashed = /-\./.test(line);
    const norm = line
      .replace(/-\.\s*([^.\n]*?)\s*\.->/g, '-->|$1|')
      .replace(/-\.->/g, '-->')
      .replace(/==>/g, '-->');
    const segs = norm.split('-->');
    if (segs.length < 2) continue;
    let prev = idOf(segs[0]);
    for (let k = 1; k < segs.length; k++) {
      let label = '', rest = segs[k];
      const lm = rest.match(/^\s*\|([^|]*)\|\s*([\s\S]*)$/);
      if (lm) { label = lm[1].trim(); rest = lm[2]; }
      const to = idOf(rest);
      if (prev && to && prev !== to) edges.push({ from: prev, to, label, dashed });
      if (to) prev = to;
    }
  }
  for (const e of edges) for (const id of [e.from, e.to])
    if (!nodes.has(id)) nodes.set(id, esc(groups.get(id) || id));
  return { nodes, edges, groups };
}

function isLinear({ nodes, edges }) {
  if (edges.length !== nodes.size - 1) return false;
  const out = new Map(), inn = new Map();
  for (const e of edges) { out.set(e.from, (out.get(e.from) || 0) + 1); inn.set(e.to, (inn.get(e.to) || 0) + 1); }
  return [...nodes.keys()].every(id => (out.get(id) || 0) <= 1 && (inn.get(id) || 0) <= 1);
}

function renderFlow(src) {
  const { nodes, edges, groups } = parseFlow(src);
  const g = { nodes, edges, groups };
  if (!nodes.size || !edges.length) return null;
  const label = id => nodes.get(id) || id;
  if (isLinear(g)) {
    const next = new Map(g.edges.map(e => [e.from, e]));
    const start = g.edges.find(e => ![...g.edges].some(x => x.to === e.from))?.from || g.edges[0].from;
    const seen = []; let cur = start;
    while (cur && !seen.includes(cur) && seen.length <= g.nodes.size) { seen.push(cur); cur = next.get(cur)?.to; }
    return `<div class="chain">${seen.map((id, k) => `<span class="chain-node">${label(id)}</span>` +
      (k < seen.length - 1 ? '<span class="chain-sep">→</span>' : '')).join('')}</div>`;
  }
  const byFrom = new Map();
  for (const e of g.edges) { if (!byFrom.has(e.from)) byFrom.set(e.from, []); byFrom.get(e.from).push(e); }
  const inEdge = new Set(g.edges.map(e => e.to));
  const order = [...nodes.keys()]
    .filter(id => byFrom.has(id) || groups.has(id))
    .sort((a, b) => (Number(inEdge.has(a)) - Number(inEdge.has(b))) ||                       // 入口优先
      [...groups.keys()].indexOf(a) * 100 - [...groups.keys()].indexOf(b) * 100);
  const cards = (order.length ? order : [...nodes.keys()]).map(id => {
    const outs = (byFrom.get(id) || []).map(e =>
      `<span class="edgechip ${e.dashed ? 'dashed' : ''}">${e.label ? `<b>${e.label}</b> ` : ''}→ ${label(e.to)}</span>`).join('');
    return `<div class="ncard"><div class="ncard-h">${label(id)}</div>${outs ? `<div class="ncard-b">${outs}</div>` : ''}</div>`;
  }).join('');
  return `<div class="ngrid">${cards}</div>`;
}

function renderSequence(src) {
  const parts = new Map();
  for (const m of src.matchAll(/^\s*participant\s+(\S+)\s+as\s+(.+)$/gm)) parts.set(m[1], esc(m[2].trim()));
  const steps = [];
  for (const m of src.matchAll(/^\s*([A-Za-z0-9_]+)(-->>|->>|--|-)([A-Za-z0-9_]+):\s*(.+)$/gm)) {
    steps.push({ from: m[1], to: m[3], msg: esc(m[4].trim()) });
  }
  if (!steps.length) return null;
  const name = id => parts.get(id) || id;
  return `<ol class="steps">${steps.map(s => `<li><span class="step-io">${esc(name(s.from))} <em>→</em> ${esc(name(s.to))}</span><span class="step-msg">${s.msg}</span></li>`).join('')}</ol>`;
}

/** mermaid 块 → 原生视觉 + 可折叠图定义 */
let diaSeq = 0;
function renderDiagram(src) {
  const id = `d${++diaSeq}`;
  const body = /^\s*sequenceDiagram/m.test(src) ? renderSequence(src) : renderFlow(src);
  const viz = body || '<div class="warn">该图未结构化成视觉（保留下方图定义）</div>';
  return `<div class="dia" id="${id}">${viz}
    <details class="dia-src"><summary>图定义（Mermaid，可复制回 markdown）</summary><pre><code>${esc(src)}</code></pre></details></div>`;
}

/* ───────────────────────── 块级解析 ───────────────────────── */
const md = fs.readFileSync(MD, 'utf8').replace(/\r/g, '');
const lines = md.split('\n');
const html = [];
const toc = [];
let h2n = 0, h3n = 0;
const slug = s => s.replace(/[^\w\u4e00-\u9fa5]+/g, '-').replace(/^-|-$/g, '').slice(0, 40);

function table(rows) {
  const head = rows[0], body = rows.slice(2);
  return `<div class="tbl-wrap"><table><thead><tr>${head.map(c => `<th>${inline(c)}</th>`).join('')}</tr></thead>` +
    `<tbody>${body.map(r => `<tr>${r.map(c => `<td>${inline(c)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
}
const splitRow = l => l.replace(/^\||\|$/g, '').split('|').map(s => s.trim());

let i = 0;
let currentSection = '';
while (i < lines.length) {
  const line = lines[i];

  if (/^```/.test(line)) {                                     // 围栏代码
    const lang = line.replace(/^```/, '').trim();
    const buf = []; i++;
    while (i < lines.length && !/^```/.test(lines[i])) buf.push(lines[i++]);
    i++;
    const code = buf.join('\n');
    html.push(lang === 'mermaid' ? renderDiagram(code)
      : `<pre class="code"><code>${esc(code)}</code></pre>`);
    continue;
  }
  if (/^#{1,4}\s/.test(line)) {                                // 标题
    const lv = line.match(/^#+/)[0].length, t = line.replace(/^#+\s*/, '');
    const id = slug(t);
    if (lv === 2) { h2n++; h3n = 0; currentSection = t; toc.push({ lv: 2, t, id }); html.push(`<h2 id="${id}">${inline(t)}</h2>`); }
    else if (lv === 3) { h3n++; toc.push({ lv: 3, t, id }); html.push(`<h3 id="${id}">${inline(t)}</h3>`); }
    else html.push(`<h${lv} id="${id}">${inline(t)}</h${lv}>`);
    i++; continue;
  }
  if (/^>\s?/.test(line)) {                                    // 引用块
    const buf = [];
    while (i < lines.length && /^>\s?/.test(lines[i])) buf.push(lines[i++].replace(/^>\s?/, ''));
    html.push(`<blockquote>${buf.map(b => b.trim() ? inline(b) : '<br>').join('<br>')}</blockquote>`);
    continue;
  }
  if (/^\|.*\|/.test(line) && i + 1 < lines.length && /^\|[\s:|-]+\|$/.test(lines[i + 1])) {  // 表格
    const rows = [];
    while (i < lines.length && /^\|.*\|/.test(lines[i])) rows.push(splitRow(lines[i++]));
    html.push(table(rows)); continue;
  }
  if (/^[-*]\s/.test(line) || /^\d+\.\s/.test(line)) {         // 列表（含两级缩进）
    const ordered = /^\d+\./.test(line);
    const items = []; let cur = null;
    while (i < lines.length && (/^(\s*)([-*]|\d+\.)\s/.test(lines[i]) || (/^\s{2,}\S/.test(lines[i]) && cur))) {
      const m = lines[i].match(/^(\s*)(?:[-*]|\d+\.)\s+(.*)$/);
      if (m) { if (m[1].length >= 2 && cur) cur.sub.push(m[2]); else { cur = { text: m[2], sub: [] }; items.push(cur); } }
      else cur.sub.push(lines[i].trim());
      i++;
    }
    const li = it => `<li>${inline(it.text)}${it.sub.length ? `<ul>${it.sub.map(s => `<li>${inline(s)}</li>`).join('')}</ul>` : ''}</li>`;
    html.push(`<${ordered ? 'ol' : 'ul'}>${items.map(li).join('')}</${ordered ? 'ol' : 'ul'}>`);
    continue;
  }
  if (/^---+\s*$/.test(line)) { html.push('<hr class="sep">'); i++; continue; }
  if (!line.trim()) { i++; continue; }

  const buf = [line]; i++;                                      // 段落
  while (i < lines.length && lines[i].trim() && !/^(#|>|```|\||[-*]\s|\d+\.\s|---)/.test(lines[i])) buf.push(lines[i++]);
  html.push(`<p>${inline(buf.join(' '))}</p>`);
}

/* ───────────────────────── 页面装配 ───────────────────────── */
const first = html.findIndex(h => h.startsWith('<h2'));
const hero = html.slice(0, first).join('\n');
const body = html.slice(first).join('\n');
const navText = s => esc(s.replace(/^>[\s]*/, '').replace(/\*+/g, '').replace(/`([^`]*)`/g, '$1').replace(/^\d+(\.\d+)*\s*/, ''));
const nav = toc.map(t => `<a class="nav-${t.lv}" href="#${t.id}">${navText(t.t)}</a>`).join('');
const generated = new Date().toISOString().slice(0, 16).replace('T', ' ');
const figCount = (body.match(/class="figsvg"/g) || []).length;

const page = `<!DOCTYPE html>
<html lang="zh-CN" data-theme="light">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI-DataHub 全貌架构设计</title>
<style>
:root{--bg:#f6f7fb;--card:#fff;--ink:#0f172a;--sub:#475569;--mut:#94a3b8;--line:#e2e8f0;
--pri:#4f46e5;--ok:#059669;--warn:#b45309;--bad:#dc2626;--chip:#eef2ff}
[data-theme=dark]{--bg:#0b1020;--card:#121a2e;--ink:#e6edf7;--sub:#a9b6cc;--mut:#7f8fa9;
--line:#22304d;--pri:#8b9cff;--ok:#34d399;--warn:#fbbf24;--bad:#f87171;--chip:#1b2540}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.65 -apple-system,"Noto Sans CJK SC","PingFang SC","Microsoft YaHei",Segoe UI,sans-serif}
a{color:var(--pri);text-decoration:none} a:hover{text-decoration:underline}
code{background:var(--chip);border:1px solid var(--line);border-radius:4px;padding:.08em .38em;font-size:.86em;
font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
pre.code{background:var(--card);border:1px solid var(--line);border-left:3px solid var(--pri);border-radius:8px;padding:14px 16px;overflow:auto;font-size:.86em}
pre.code code{background:none;border:none;padding:0}
header.hero{padding:34px 40px 26px;background:linear-gradient(135deg,var(--pri),#0ea5e9);color:#fff}
header.hero h1{margin:0 0 8px;font-size:30px;letter-spacing:.2px}
header.hero p{margin:0;opacity:.92;font-size:14.5px;max-width:1100px;line-height:1.7}
.layout{display:grid;grid-template-columns:288px minmax(0,1fr);gap:28px;padding:26px 32px 80px;align-items:start}
nav.side{position:sticky;top:16px;max-height:calc(100vh - 32px);overflow:auto;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 12px}
nav.side .navtitle{font-size:12.5px;color:var(--mut);letter-spacing:.08em;text-transform:uppercase;margin:2px 8px 10px}
a.nav-2{display:block;font-size:13.6px;padding:6px 9px;border-radius:7px;color:var(--ink)}
a.nav-3{display:block;font-size:12.6px;padding:4px 9px 4px 22px;border-radius:7px;color:var(--sub)}
a.nav-2:hover,a.nav-3:hover{background:var(--chip);text-decoration:none}
main{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:12px 38px 44px;min-width:0}
h2{font-size:24px;margin:38px 0 12px;padding-top:10px;border-top:2px solid var(--line)}
h2:first-of-type{border-top:none;margin-top:18px}
h3{font-size:18.5px;margin:28px 0 8px;color:var(--pri)}
h4{font-size:16px;margin:20px 0 6px}
p,li{color:var(--ink)} li{margin:4px 0} ul,ol{padding-left:22px}
.intro{margin:6px 0 18px;padding:12px 16px;background:var(--chip);border:1px solid var(--line);border-radius:10px;font-size:13.6px;color:var(--sub)}
.intro h1{display:none}
blockquote{margin:14px 0;padding:12px 16px;background:var(--chip);border-left:4px solid var(--pri);border-radius:0 10px 10px 0;font-size:14.2px;color:var(--sub)}
hr.sep{border:none;border-top:1px dashed var(--line);margin:30px 0}
strong{color:var(--ink)}
.tbl-wrap{overflow-x:auto;margin:14px 0;border:1px solid var(--line);border-radius:10px}
table{border-collapse:collapse;width:100%;font-size:14px;min-width:640px}
th,td{padding:9px 12px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{background:var(--chip);font-weight:650;font-size:13.2px;white-space:nowrap}
tbody tr:nth-child(even){background:color-mix(in srgb,var(--chip) 40%,transparent)}
tbody tr:hover{background:color-mix(in srgb,var(--pri) 8%,transparent)}
figure.fig{margin:18px 0;padding:14px;border:1px solid var(--line);border-radius:12px;background:#fff}
[data-theme=dark] figure.fig{background:#f1f5f9}
.figwrap{overflow-x:auto}
svg.figsvg{display:block;width:100%;min-width:1080px;height:auto}
figcaption{margin-top:8px;font-size:12.8px;color:var(--mut);text-align:center}
.dia{margin:18px 0;padding:16px;border:1px solid var(--line);border-radius:12px;background:color-mix(in srgb,var(--chip) 45%,transparent)}
.chain{display:flex;flex-wrap:wrap;align-items:center;gap:8px}
.chain-node{background:var(--card);border:1px solid var(--line);border-left:3px solid var(--pri);border-radius:8px;padding:7px 11px;font-size:13px;line-height:1.45;box-shadow:0 1px 2px rgba(15,23,42,.05)}
.chain-sep{color:var(--mut);font-weight:700}
.ngrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(290px,1fr));gap:10px}
.ncard{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 12px}
.ncard-h{font-size:13.4px;font-weight:650;color:var(--pri);margin-bottom:6px;line-height:1.45}
.ncard-b{display:flex;flex-direction:column;gap:4px}
.edgechip{font-size:12.4px;color:var(--sub);background:var(--chip);border:1px solid var(--line);border-radius:6px;padding:3px 7px;line-height:1.45}
.edgechip.dashed{border-style:dashed}
.edgechip b{color:var(--bad)}
.steps{list-style:none;padding:0;margin:0;counter-reset:s}
.steps li{counter-increment:s;display:grid;grid-template-columns:1fr 2fr;gap:10px;padding:7px 0;border-bottom:1px dashed var(--line);font-size:13.4px}
.steps li:last-child{border-bottom:none}
.steps li::before{content:counter(s)}
.step-io{color:var(--pri);font-weight:600}
.step-io em{color:var(--mut);font-style:normal;margin:0 2px}
.warn{color:var(--warn);font-size:13px}
.figlink{display:inline-block;font-size:12.8px;padding:3px 9px;border:1px solid var(--line);border-radius:999px;background:var(--chip)}
.figlink:hover{border-color:var(--pri);text-decoration:none}
:target>figcaption{color:var(--pri);font-weight:650}
figure.fig:target{outline:2px solid var(--pri);outline-offset:3px}
details.dia-src{margin-top:10px;font-size:12.6px;color:var(--mut)}
details.dia-src summary{cursor:pointer;padding:3px 0}
details.dia-src pre{margin:8px 0 0;padding:10px;background:var(--card);border:1px solid var(--line);border-radius:8px;overflow:auto;font-size:12px}
.tgl{position:fixed;right:22px;bottom:22px;z-index:9;background:var(--pri);color:#fff;border:none;border-radius:999px;padding:10px 15px;font-size:13px;cursor:pointer;box-shadow:0 6px 18px rgba(15,23,42,.22)}
.pill{display:inline-block;font-size:12px;padding:2px 9px;border-radius:999px;background:var(--chip);border:1px solid var(--line);color:var(--sub);margin-right:6px}
@media print{nav.side,.tgl{display:none}.layout{grid-template-columns:1fr;padding:0}main{border:none}figure.fig{break-inside:avoid}svg.figsvg{min-width:0}}
@media (max-width:1080px){.layout{grid-template-columns:1fr}nav.side{position:static;max-height:none}}
</style>
</head>
<body>
<header class="hero">
  <h1>AI-DataHub 全貌架构设计</h1>
  <p>分层结构 · 各层核心逻辑 · 安全围栏纵深 · 身份与角色权限模型 · FDE 演进契约　　<span class="pill" style="background:rgba(255,255,255,.18);border-color:rgba(255,255,255,.35);color:#fff">事实源：docs/ARCHITECTURE_OVERVIEW.md</span><span class="pill" style="background:rgba(255,255,255,.18);border-color:rgba(255,255,255,.35);color:#fff">${figCount} 张矢量大图已内联</span><span class="pill" style="background:rgba(255,255,255,.18);border-color:rgba(255,255,255,.35);color:#fff">生成于 ${generated}</span></p>
</header>
<div class="layout">
  <nav class="side"><div class="navtitle">目录</div>${nav}</nav>
  <main>${hero ? `<div class="intro">${hero}</div>` : ''}${body}</main>
</div>
<button class="tgl" onclick="const d=document.documentElement;d.dataset.theme=d.dataset.theme==='dark'?'light':'dark'">切换明暗</button>
<script>
  // 锚点定位时高亮当前章节
  const links=[...document.querySelectorAll('nav.side a')];
  const map=new Map(links.map(a=>[a.hash.slice(1),a]));
  const obs=new IntersectionObserver(es=>{for(const e of es){if(e.isIntersecting){links.forEach(l=>l.style.background='');const a=map.get(e.target.id);if(a)a.style.background='var(--chip)';}}},{rootMargin:'-10% 0px -70% 0px'});
  document.querySelectorAll('main h2, main h3').forEach(h=>h.id&&obs.observe(h));
</script>
</body></html>`;

fs.writeFileSync(OUT, page);
console.log(`✓ ${path.basename(OUT)}  ${(Buffer.byteLength(page) / 1024).toFixed(0)}KB
  章节 h2=${toc.filter(t => t.lv === 2).length} h3=${toc.filter(t => t.lv === 3).length}
  矢量图内联=${figCount}  图定义渲染=${diaSeq}  表格=${body.split('<table>').length - 1}`);
