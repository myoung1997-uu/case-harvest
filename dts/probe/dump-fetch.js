// === A 档 · 接口重放(首选) ============================================
// 干啥:在 DTS 页面里执行(用 chrome-devtools 的 evaluate 类工具),用页面自身
//      登录态(credentials:'include')重放数据接口,把整页 JSON 原样搬回来。
// 用法:替换 CONFIG → 在【已登录的 DTS 页面】里执行 → 返回的 JSON 字符串原样
//      存盘 probe-out/list-<版本>.a.json。每页一个返回,量大就把 startPage
//      往后挪、分次跑——一次别贪多。
// 失败:接口 404/字段对不上 → 别调了,降 B 档(PROBE.md 规矩:每档只试一次)。
(async () => {
  const CONFIG = {
    // 列表接口模板。去哪找:F12/Network 面板里翻页时那条 XHR,原样抄 URL,
    // 把版本和页码换成 {VER} {PAGE} 占位符。
    listUrl: "https://CHANGE-ME/api/issues?version={VER}&page={PAGE}&pageSize=50",
    version: "CHANGE-ME",   // DTS 的 B版本 字段值
    startPage: 1,
    maxPages: 3,            // 一次最多翻几页
    // 可选:详情接口(模板含 {ID})+ 样本单号。probe 阶段抓 2 条样本用。
    detailUrl: "",
    detailIds: [],
  };
  const fill = (s, m) => s.replace(/\{VER\}/g, m.ver ?? "").replace(/\{PAGE\}/g, String(m.page ?? "")).replace(/\{ID\}/g, String(m.id ?? ""));
  const out = { tier: "A", page: location.href, at: new Date().toISOString(), pages: [], details: [] };
  for (let p = CONFIG.startPage; p < CONFIG.startPage + CONFIG.maxPages; p++) {
    try {
      const r = await fetch(fill(CONFIG.listUrl, { ver: CONFIG.version, page: p }), { credentials: "include", headers: { Accept: "application/json" } });
      if (!r.ok) { out.pages.push({ page: p, error: r.status }); break; }
      out.pages.push({ page: p, body: await r.json() });
    } catch (e) { out.pages.push({ page: p, error: String(e) }); break; }
  }
  for (const id of CONFIG.detailIds) {
    try {
      const r = await fetch(fill(CONFIG.detailUrl, { id }), { credentials: "include", headers: { Accept: "application/json" } });
      out.details.push(r.ok ? { id, body: await r.json() } : { id, error: r.status });
    } catch (e) { out.details.push({ id, error: String(e) });
    }
  }
  return JSON.stringify(out);
})()
