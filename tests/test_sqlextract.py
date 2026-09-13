"""sqlextract 的回归用例——每一条都是真实跑批里踩过的坑。

跑法：python3 -m unittest discover -s tests
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import sqlextract as sx  # noqa: E402


def norm(s):
    return " ".join(s.split()).lower()


class PlBlock(unittest.TestCase):
    def test_procedure_body_not_cut_at_inner_semicolon(self):
        # 按 ; 切会把过程腰斩，DB 回 subprogram body is not ended correctly
        body = """```sql
create or replace procedure p1() as
  x int;
begin
  x := 1;
  if x > 0 then
    raise info 'x=%', x;
  end if;
end;
/
call p1();
```"""
        st = sx.extract(body)
        self.assertEqual(len(st), 2, st)
        self.assertIn("end if;", st[0])
        self.assertTrue(st[0].rstrip().endswith("/"), st[0])
        self.assertEqual(norm(st[1]), "call p1();")

    def test_missing_slash_is_added(self):
        body = """```
create or replace function f1() return int as
begin
  return 1;
end;
select f1();
```"""
        st = sx.extract(body)
        self.assertEqual(len(st), 2, st)
        self.assertTrue(st[0].rstrip().endswith("/"))

    def test_blank_line_before_declare(self):
        # 「缓冲区为空才认块头」若不忽略空行，declare 块整体失效
        body = """```sql

declare
  v int;
begin
  v := 2;
  raise info '%', v;
end;
/
```"""
        st = sx.extract(body)
        self.assertEqual(len(st), 1, st)
        self.assertIn("v := 2;", st[0])

    def test_if_not_exists_does_not_open_block(self):
        body = """```sql
create table if not exists t1(a int);
select * from t1;
```"""
        st = sx.extract(body)
        self.assertEqual([norm(x) for x in st],
                         ["create table if not exists t1(a int);", "select * from t1;"])

    def test_case_and_loop_depth(self):
        body = """```sql
create or replace procedure p2() as
begin
  for i in 1..3 loop
    case when i = 1 then raise info 'one'; else null; end case;
  end loop;
  select case when 1=1 then 1 end into strict dummy;
end;
/
select 1;
```"""
        st = sx.extract(body)
        self.assertEqual(len(st), 2, st)
        self.assertEqual(norm(st[1]), "select 1;")

    def test_package_body_multiple_procs(self):
        body = """```sql
create or replace package pkg1 is
  procedure a1;
  procedure b1;
end pkg1;
/
create or replace package body pkg1 is
  procedure a1 as begin raise info 'a'; end;
  procedure b1 as begin raise info 'b'; end;
end pkg1;
/
call pkg1.a1();
```"""
        st = sx.extract(body)
        self.assertEqual(len(st), 3, st)
        self.assertIn("procedure b1 as begin", st[1])

    def test_dollar_quoted_do_then_stray_slash(self):
        # DO $$…$$; 之后多写一行 /，gsql 当独立语句报错并吞掉后面的判读语句
        body = """```sql
do $$
begin
  perform 1;
end
$$;
/
select 2;
```"""
        st = sx.extract(body)
        self.assertEqual(len(st), 2, st)
        self.assertFalse(any(x.strip() == "/" for x in st))
        self.assertFalse(st[0].rstrip().endswith("/"), "美元引号块不该补斜杠")


class Noise(unittest.TestCase):
    def test_fence_and_backtick_not_double_counted(self):
        body = "复现：`create table t2(a int, b text);`\n```sql\ncreate table t2(a int, b text);\ninsert into t2 values(1,'x');\n```"
        st = sx.extract(body)
        self.assertEqual([norm(x) for x in st],
                         ["create table t2(a int, b text);", "insert into t2 values(1,'x');"])

    def test_sql_outside_fence_kept_when_fence_is_stacktrace(self):
        # OG-7097：SQL 写在模板「操作步骤」里，围栏块放的是 gdb 堆栈
        body = ("### 操作步骤\n--step1:建表;expect:成功\ncreate table t5(a int);\n"
                "call p5();\n### 日志\n```\n#0  heap_deform_tuple_impl (isnull=0x1) at heaptuple.cpp:1085\n```")
        self.assertEqual([norm(x) for x in sx.extract(body)],
                         ["create table t5(a int);", "call p5();"])

    def test_document_order_preserved(self):
        body = "create table t6(a int);\n```sql\ninsert into t6 values(1);\n```\nselect * from t6;"
        self.assertEqual([norm(x) for x in sx.extract(body)],
                         ["create table t6(a int);", "insert into t6 values(1);", "select * from t6;"])

    def test_multi_statement_one_line(self):
        st = sx.extract("```\ndrop table if exists t3;create table t3(a int);\n```")
        self.assertEqual([norm(x) for x in st],
                         ["drop table if exists t3;", "create table t3(a int);"])

    def test_prose_not_glued_to_sql(self):
        body = "### 操作步骤\n先建一张表然后查询\ncreate table t4(a int);\nselect count(*) from t4;\n"
        st = sx.extract(body)
        self.assertEqual([norm(x) for x in st],
                         ["create table t4(a int);", "select count(*) from t4;"])

    def test_sql_line_with_chinese_kept(self):
        st = sx.extract("```\nselect '中文' as 名称 from dual;\n```")
        self.assertEqual(len(st), 1)
        self.assertIn("中文", st[0])

    def test_gsql_prompt_and_output_stripped(self):
        body = "```\nopenGauss=# select 1;\n ?column?\n----------\n        1\n(1 row)\n\nopenGauss=# select 2;\n```"
        st = sx.extract(body)
        self.assertEqual([norm(x) for x in st], ["select 1;", "select 2;"])

    def test_markdown_table_dropped(self):
        body = "| 列 | 值 |\n|---|---|\n| a | 1 |\nselect 3;"
        self.assertEqual([norm(x) for x in sx.extract(body)], ["select 3;"])

    def test_dangerous_statement_dropped(self):
        st = sx.extract("```\ndrop database bench;\nalter system set x=1;\nselect 4;\n```")
        self.assertEqual([norm(x) for x in st], ["select 4;"])

    def test_fullwidth_semicolon(self):
        st = sx.extract("```\nselect 5；\nselect 6;\n```")
        self.assertEqual([norm(x) for x in st], ["select 5;", "select 6;"])


class Compat(unittest.TestCase):
    def test_plain_is_a(self):
        self.assertEqual(sx.guess_db("select 1 from dual;", ""), "bench")

    def test_backtick_ident_is_b(self):
        self.assertEqual(sx.guess_db("create table `t`(`a` int);", ""), "bench_b")

    def test_user_variable_is_b(self):
        self.assertEqual(sx.guess_db("set @x = 1; select @x;", ""), "bench_b")

    def test_mysql_types_is_b(self):
        self.assertEqual(sx.guess_db("create table t(a tinyint unsigned auto_increment);", ""), "bench_b")

    def test_text_mention_b(self):
        self.assertEqual(sx.guess_db("select 1;", "【B模式】xxx"), "bench_b")

    def test_text_mention_pg(self):
        self.assertEqual(sx.guess_db("select 1;", "PG兼容模式下"), "bench_pg")

    def test_text_mention_d(self):
        self.assertEqual(sx.guess_db("select 1;", "D兼容库 shark"), "bench_d")

    def test_email_at_is_not_b(self):
        self.assertEqual(sx.guess_db("select 'a@b.com';", ""), "bench")


class Refs(unittest.TestCase):
    def test_missing_refs(self):
        miss = sx.missing_objects(["insert into t9 values(1);", "select * from t9 join pg_class on true;"])
        self.assertEqual(miss, {"t9"})

    def test_modifiers_are_not_objects(self):
        # 真实误判：alter table concurrently / delete from only / from (select ...)
        miss = sx.missing_objects(["create table t8(a int);", "alter table concurrently t8 add column b int;",
                                   "alter table if exists only t8 drop column b;", "delete from only t8;",
                                   "select * from (select 1) s;", "select * from lateral f(1);"])
        self.assertEqual(miss, set())

    def test_created_refs_ok(self):
        miss = sx.missing_objects(["create table t9(a int);", "select * from t9;"])
        self.assertEqual(miss, set())


if __name__ == "__main__":
    unittest.main()
