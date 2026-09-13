"""各脚本共用的小工具：数据目录定位、实例清单解析。只用标准库。"""
import os


def home():
    """数据根目录。优先 $HARVEST_HOME，否则当前目录下的 harvest/。"""
    h = os.environ.get("HARVEST_HOME") or os.path.join(os.getcwd(), "harvest")
    return os.path.abspath(os.path.expanduser(h))


INSTANCE_COLS = ("label", "port", "osuser", "unit", "revive", "caps", "build", "core_glob")


def read_instances(path):
    """解析 instances.conf：每行 8 列，空白分隔，# 开头是注释，缺省值写 -。

    label port osuser unit revive caps build core_glob
    """
    out = []
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) < 5:
                raise ValueError(f"{path}:{n} 至少要 label port osuser unit revive 五列")
            parts += ["-"] * (len(INSTANCE_COLS) - len(parts))
            row = dict(zip(INSTANCE_COLS, parts))
            row = {k: (None if v == "-" else v) for k, v in row.items()}
            row["caps"] = set((row["caps"] or "").split(",")) - {""}
            if row["revive"] not in ("yes", "wait"):
                raise ValueError(f"{path}:{n} revive 只能是 yes 或 wait")
            out.append(row)
    return out


# 库名 → 兼容模式 → 需要的插件能力
DB_COMPAT = {"bench": ("A", None), "bench_b": ("B", "dolphin"), "bench_pg": ("PG", None), "bench_d": ("D", "shark")}
