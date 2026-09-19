// === C 档 · 整块 DOM 兜底(结构再乱也能落盘) ===========================
// 干啥:把关键区域的 HTML/文本原样抠下来。不理解内容,只保证"东西到手"。
// 用法:替换 CONFIG → 在目标页执行 → 返回 JSON 原样存盘 probe-out/<名字>.c.json。
// 体积控制(区域太大时的三板斧,按顺序试):
//   1) 缩小 regionSelector(F12 选中正文容器 → Copy selector);
//   2) mode 改 "text"(只要文本,调 SQL 抽取够用);
//   3) partSelector 分块(如 ".section",每块一条,分开存)。
// 超过 maxChars 会"拒载"并报实际尺寸——绝不静默截断,逼你换上面的三板斧。
(async () => {
  const CONFIG = {
    regionSelector: "body",   // 越小越好;body 是最后兜底
    mode: "both",             // "html" | "text" | "both"
    dropTags: ["script", "style", "noscript", "svg", "link", "meta"],  // 噪声标签,html 里剔除
    partSelector: "",         // 可选:区域太大时按子块切,如 ".section"
    maxChars: 120000,         // 拒载阈值(html+text 合计)
  };
  const env = { tier: "C", page: location.href, at: new Date().toISOString() };
  const el = document.querySelector(CONFIG.regionSelector);
  if (!el) return JSON.stringify({ ...env, error: "selector 未命中: " + CONFIG.regionSelector });
  const strip = (node) => {
    const c = node.cloneNode(true);
    for (const t of CONFIG.dropTags) c.querySelectorAll(t).forEach(n => n.remove());
    return c;
  };
  if (CONFIG.partSelector) {
    const parts = [...el.querySelectorAll(CONFIG.partSelector)].map((p, i) => ({
      idx: i, text: p.innerText, html: strip(p).outerHTML
    }));
    return JSON.stringify({ ...env, region: CONFIG.regionSelector, parts });
  }
  const text = el.innerText;
  const html = CONFIG.mode === "text" ? undefined : strip(el).outerHTML;
  const size = (text ? text.length : 0) + (html ? html.length : 0);
  if (size > CONFIG.maxChars) {
    return JSON.stringify({ ...env, too_big: true, size,
      hint: "缩小 regionSelector / mode 改 'text' / 用 partSelector 分块" });
  }
  return JSON.stringify({ ...env, region: CONFIG.regionSelector, html, text });
})()
