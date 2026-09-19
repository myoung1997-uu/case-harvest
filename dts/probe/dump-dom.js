// === C 档 · 整块 DOM 兜底(结构再乱也能落盘) ===========================
// 干啥:把关键区域的 HTML 原样抠下来。不理解内容,只保证"东西到手"。
// 用法:替换 CONFIG 的 regionSelector(F12 选中列表容器/详情正文区域抄选择器,
//      拿不准就先填 body)→ 在目标页执行 → 返回 JSON 原样存盘
//      probe-out/<单号或列表>.c.json。
(async () => {
  const CONFIG = {
    regionSelector: "body",   // 越小越好;body 是最后兜底
  };
  const env = { tier: "C", page: location.href, at: new Date().toISOString() };
  const el = document.querySelector(CONFIG.regionSelector);
  if (!el) return JSON.stringify({ ...env, error: "selector 未命中: " + CONFIG.regionSelector });
  return JSON.stringify({ ...env, html: el.outerHTML, text: el.innerText });
})()
