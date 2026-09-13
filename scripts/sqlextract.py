"""从 issue 正文里抽出可执行的 SQL 语句。只用标准库。

踩过的坑都有回归用例（tests/test_sqlextract.py），改这里之前先跑用例，
改完要拿具体 issue 做前后 diff，别只看聚合指标。
"""
import html
import re

# 绝不执行的语句：动实例级状态、读写宿主机文件、踢别人会话
DANGER = re.compile(
    r"\b(drop\s+database|alter\s+system|pg_terminate_backend|pg_cancel_backend|shutdown|gs_ctl|gs_om|"
    r"reset\s+all|drop\s+user|drop\s+role|drop\s+tablespace|copy\s+.*\bfrom\s+program|"
    r"pg_read_file|pg_ls_dir|lo_import|lo_export)\b", re.I)

SQL_KW = (r"create|insert|select|update|delete|alter|drop|declare|begin|call|explain|set|with|comment|"
          r"grant|revoke|prepare|execute|truncate|analyze|vacuum|do|show|reset|start|commit|rollback|"
          r"savepoint|merge|replace|values|cluster|reindex|lock|copy|end|exec|use|load")
SQLSTART = re.compile(r"^\s*(" + SQL_KW + r")\b", re.I)
META = re.compile(r"^\s*\\\w")  # gsql 元命令 \d \c 等

FENCE = re.compile(r"```[^\n]*\n(.*?)```", re.S)
INLINE = re.compile(r"`([^`\n]{12,})`")
PROMPT = re.compile(r"^\s*(?:[\w-]*=[#>]|openGauss[=\-][#>]|postgres=[#>]|gsql[=\-][#>]|\w+@\w+[:#>]?\s*\S*\s*[#$]|➜\s)\s*")
OUTPUT = re.compile(r"^\s*(\(\d+ rows?\)|Time: [\d.]+ ms|[-+]{4,}|-{3,}\+?.*|CREATE TABLE|INSERT \d+ \d+|"
                    r"SELECT \d+|UPDATE \d+|DELETE \d+|DROP TABLE|CREATE INDEX|ALTER TABLE|CALL|"
                    r"NOTICE:.*|INFO:.*|ERROR:.*|WARNING:.*|DETAIL:.*|HINT:.*|CONTEXT:.*|LINE \d+:.*|\^)\s*$")
CJK = re.compile(r"[一-鿿]")

PL_HEAD = re.compile(r"^\s*create\s+(or\s+replace\s+)?(definer\s*=\s*\S+\s+)?"
                     r"(procedure|function|package|trigger|type\s+body)\b", re.I)
PKG_BODY = re.compile(r"^\s*create\s+(or\s+replace\s+)?package\s+body\s+([\w.\"]+)", re.I)
PKG_SPEC = re.compile(r"^\s*create\s+(or\s+replace\s+)?package\s+(?!body\b)([\w.\"]+)", re.I)
ANON_HEAD = re.compile(r"^\s*(declare|begin)\b(?!\s*(transaction|work|isolation|;))", re.I)


def _strip_literals(line):
    line = re.sub(r"'(?:[^']|'')*'", "''", line)
    return re.sub(r"--.*$", "", line)


def _depth_delta(line):
    """一行对 PL 块深度的贡献。end if/end loop/end case 各算一次 -1。"""
    s = _strip_literals(line).lower()
    s = re.sub(r"\bif\s+(not\s+)?exists\b", " ", s)
    s = re.sub(r"\bend\s+(if|loop|case)\b", " end ", s)
    opens = len(re.findall(r"\b(begin|loop|case)\b", s))
    # PL 的 if 以 then 收尾才算开块；elsif/else if 不开新块
    opens += len(re.findall(r"(?<!els)(?<!else )\bif\b(?=.*\bthen\b)", s))
    closes = len(re.findall(r"\bend\b", s))
    return opens - closes


def _clean(text):
    """去掉 gsql 提示符、结果回显、markdown 表格、散文行。"""
    out = []
    for raw in text.splitlines():
        line = PROMPT.sub("", raw)
        st = line.strip()
        if not st:
            out.append("")
            continue
        if OUTPUT.match(st) or re.match(r"^\s*(\||[\s|:+=-]+$)", st) or st.startswith(("$ ", "# ", "[root@", "[omm@")):
            continue
        if re.match(r"^\s*(--|/\*|\*/|\*\s)", st):
            continue
        if st == "/":
            out.append(line)
            continue
        if CJK.search(st) and not SQLSTART.match(st):
            # 以 SQL 关键字开头的行即便带中文也留；否则只有在引号里的中文才算 SQL 一部分
            if not re.search(r"'[^']*[一-鿿][^']*'|\"[^\"]*[一-鿿]", st):
                if not re.search(r"[;(),=]", st):
                    out.append("")
                    continue
        if not CJK.search(st) and not SQLSTART.match(st) and not META.match(st) and re.match(r"^[A-Za-z ]+$", st) \
                and len(st.split()) > 6:
            continue  # 英文散文
        out.append(line)
    return "\n".join(out)


def split_statements(text):
    stmts, cur = [], []
    mode = None        # None | "pl" | "dollar"
    depth, opened, pkg = 0, False, None

    def flush(add_slash=False):
        nonlocal cur, mode, depth, opened, pkg
        s = "\n".join(cur).strip()
        if s:
            stmts.append(s + ("\n/" if add_slash and not s.endswith("/") else ""))
        cur, mode, depth, opened, pkg = [], None, 0, False, None

    for line in text.splitlines():
        st = line.strip()
        if mode == "dollar":
            cur.append(line)
            if _strip_literals(line).count("$$") % 2 == 1 and st.endswith(";"):
                flush()
            continue
        if st == "/":
            if cur:
                flush(add_slash=(mode == "pl"))
            continue  # 缓冲区为空时的孤立斜杠直接丢
        if mode == "pl":
            cur.append(line)
            if "$$" in line and _strip_literals(line).count("$$") % 2 == 1:
                mode = "dollar"
                continue
            d = _depth_delta(line)
            if d > 0:
                opened = True
            depth += d
            low = _strip_literals(st).lower()
            if pkg:
                if re.match(r"^end\s+" + re.escape(pkg) + r"\s*;$", low):
                    flush(add_slash=True)
            elif depth <= 0 and opened and re.search(r"\bend\b[^;]*;\s*$", low):
                flush(add_slash=True)
            continue
        if not st:
            if cur and not "\n".join(cur).strip():
                cur = []
            continue
        # 新语句头：前面若是没收尾的散文残片，丢掉
        if cur and SQLSTART.match(st) and not SQLSTART.match(cur[0]) and not META.match(cur[0]):
            cur = []
        if not cur:
            if not (SQLSTART.match(st) or META.match(st)):
                continue
            if PL_HEAD.match(st) or ANON_HEAD.match(st):
                mode = "pl"
                m = PKG_BODY.match(st) or PKG_SPEC.match(st)
                pkg = m.group(2).strip('"').split(".")[-1].lower() if m else None
                cur.append(line)
                if "$$" in line and _strip_literals(line).count("$$") % 2 == 1:
                    mode = "dollar"
                    continue
                d = _depth_delta(line)
                opened, depth = d > 0, d
                low = _strip_literals(st).lower()
                if (not pkg and opened and depth <= 0 and re.search(r"\bend\b[^;]*;\s*$", low)):
                    flush(add_slash=True)
                continue
            if re.match(r"^\s*do\b", st, re.I) and "$$" in st and st.count("$$") % 2 == 1:
                mode = "dollar"
                cur.append(line)
                continue
            if META.match(st):
                stmts.append(st)
                continue
        cur.append(line)
        if "$$" in line and _strip_literals(line).count("$$") % 2 == 1:
            mode = "dollar"
            continue
        if st.endswith(";"):
            flush()
    if cur and mode is None:
        flush()
    elif cur and mode == "pl" and opened:
        flush(add_slash=True)

    out = []
    for s in stmts:
        # 一行里写了多条（drop ...;create ...;）
        if "\n" not in s and s.count(";") > 1 and "'" not in s and "$$" not in s:
            out += [p.strip() + ";" for p in s.split(";") if p.strip()]
        else:
            out.append(s)
    return out


def extract(body):
    body = html.unescape(body or "").replace("\r\n", "\n").replace("；", ";")
    # 按正文顺序切成「围栏内 / 围栏外」段。不能「有围栏就只信围栏」：
    # OG-7097 的 SQL 写在模板操作步骤里，围栏块放的是 gdb 堆栈，只信围栏会一条不剩。
    # 同一段 SQL 在反引号和围栏里各写一遍的，靠下面的语句级去重收掉。
    blocks, pos = [], 0
    for m in FENCE.finditer(body):
        blocks.append(body[pos:m.start()])
        blocks.append(m.group(1))
        pos = m.end()
    blocks.append(body[pos:])

    def unquote_inline(seg):
        return INLINE.sub(lambda m: "\n" + m.group(1) + "\n" if SQLSTART.match(m.group(1)) else m.group(1), seg)

    blocks = [b if i % 2 else unquote_inline(b) for i, b in enumerate(blocks)]
    keep, seen = [], set()
    for b in blocks:
        for s in split_statements(_clean(b)):
            if DANGER.search(s):
                continue
            if not (SQLSTART.match(s) or META.match(s)):
                continue
            key = " ".join(s.split()).lower()
            if key in seen:
                continue
            seen.add(key)
            keep.append(s)
    return keep


B_FINGERPRINT = re.compile(
    r"`\w+`|(?<![\w'@.])@@?[a-z_]\w*(?![\w.]*\.\w)|\bauto_increment\b|\b(tinyint|mediumint|tinyblob|mediumblob|"
    r"longblob|tinytext|mediumtext|longtext)\b|\bunsigned\b|on\s+update\s+current_timestamp|\breplace\s+into\b|"
    r"\binsert\s+ignore\b|declare\s+\w+\s+handler\s+for|\b(datediff|str_to_date|date_format|yearweek|"
    r"group_concat|find_in_set|ifnull)\s*\(|\bengine\s*=|\bcharset\b.*\bcollate\b|alter\s+index\s+\w+\s+invisible",
    re.I)


def guess_db(sql, text):
    """兼容模式派库。大多数 issue 不写「B 兼容」，必须看语法指纹。"""
    t = text or ""
    if re.search(r"\bD\s*兼容|D库|D\s*模式|sql\s*server|\bshark\b", t, re.I):
        return "bench_d"
    if re.search(r"\bB\s*兼容|B库|B\s*模式|MySQL\s*(兼容|模式)|dolphin", t, re.I):
        return "bench_b"
    if re.search(r"\bPG\s*兼容|PG库|PG\s*模式", t, re.I):
        return "bench_pg"
    no_str = re.sub(r"'(?:[^']|'')*'", "''", sql or "")
    if B_FINGERPRINT.search(no_str):
        return "bench_b"
    return "bench"


CREATED = re.compile(r"create\s+(?:or\s+replace\s+)?(?:global\s+|local\s+|unlogged\s+|temp\w*\s+)?"
                     r"(?:table|view|materialized\s+view|index|function|procedure|package|sequence|type|synonym)\s+"
                     r"(?:if\s+not\s+exists\s+)?[\"`]?([\w.]+)", re.I)
REFED = re.compile(r"(?:insert\s+into|alter\s+table|update|delete\s+from|\bfrom|\bjoin)"
                   r"(?:\s+(?:concurrently|online|if\s+exists|only|lateral))*\s+[\"`]?([\w.]+)(?![\w.]*\s*\()", re.I)
SYSTEM = re.compile(r"^(pg_|gs_|dbe_|information_schema|sys_|pv_|gv_|dbms_|utl_|plan_table|dual$|generate_series$|unnest$)", re.I)
NOT_OBJ = {"select", "values", "only", "lateral", "public", "set", "where", "the", "table", "to", "on", "nothing",
           "skip", "wait", "nowait", "snapshot", "global", "temp", "tmp", "current_timestamp", "current_date",
           "current_user", "session_user", "sysdate", "localtimestamp", "tables", "columns", "a", "b", "x"}


def missing_objects(stmts):
    """引用了却从没建过的对象——生成阶段就挡掉，别浪费一次执行。"""
    sql = "\n".join(stmts)
    sql = re.sub(r"'(?:[^']|'')*'", "''", sql)
    created = {x.lower().split(".")[-1] for x in CREATED.findall(sql)}
    refed = {x.lower().split(".")[-1] for x in REFED.findall(sql)}
    refed = {x for x in refed if x and not SYSTEM.match(x) and x not in NOT_OBJ and not x.isdigit()}
    return refed - created


EXT_NOT_BUNDLED = ("bm25", "ogai", "diskann", "hnswpq", "postgis", "vector", "datavec")


def needs_extension(body):
    return {e for e in EXT_NOT_BUNDLED if re.search(r"\b" + e + r"\b", body or "", re.I)}


TOOL = re.compile(r"\b(gs_dump|gs_restore|gs_probackup|gs_basebackup|gs_dumpall|gs_guc|gs_initdb|pagehack|gs_check|"
                  r"gs_loader|gds|pg_rewind)\b", re.I)


def needs_shell(body):
    return bool(TOOL.search(body or ""))
