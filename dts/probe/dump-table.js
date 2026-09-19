// === B 档 · 表格刮取(A 档不行时降这档) ================================
// 干啥:页面数据是服务端直出(没有 XHR)时,把结果表格整张刮成 JSON。
//      一跑一整页,列名(表头)就是字段映射的原料。
// 用法:替换 CONFIG → 在【结果列表页】执行 → 返回 JSON 原样存盘
//      probe-out/list-<版本>.b.json。翻页手动点下一页,每页跑一次。
// 失败:选择器命中不了表 → 降 C 档。
(async () => {
  const CONFIG = {
    tableSelector: "table",   // 结果表选择器;最简单通常就是 table,
                              // 有多个表时用更具体的(F12 选中表格抄选择器)
  };
  const env = { tier: "B", page: location.href, at: new Date().toISOString() };
  const t = document.querySelector(CONFIG.tableSelector);
  if (!t) return JSON.stringify({ ...env, error: "selector 未命中: " + CONFIG.tableSelector });
  const headers = [...t.querySelectorAll("thead th")].map(th => th.innerText.trim());
  const rows = [...t.querySelectorAll("tbody tr")].map(tr =>
    [...tr.querySelectorAll("td")].map(td => {
      const a = td.querySelector("a[href]");
      return { text: td.innerText.trim(), link: a ? a.getAttribute("href") : null };
    })
  );
  return JSON.stringify({ ...env, headers, rows });
})()
