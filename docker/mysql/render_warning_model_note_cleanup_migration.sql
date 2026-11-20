-- 2026-11-20 图表卡“未经服务端取数对账”徽标清理（一次性数据迁移，幂等可重跑）
--
-- 由来：render_chart 的 columns+rows 兜底分支把一句**给模型的作业约束**
-- （“本图数据由对话侧提供（未经服务端取数对账）：数字必须与本轮已展示的查询结果一致，不得凭记忆补数”）
-- 写进了 doc 的 warnings（列注释即“对用户可见的非阻断提示”），前端 ChartRefCard 把它渲染成
-- 图表卡顶部一条命令式徽标 —— 用户看到的是给自己下的指令，既看不懂也不知道该做什么。
-- 代码侧已改为只回模型（render_tools 的 model_notes，不落 doc.warnings）。
-- 这里清掉存量图表文档里的这一条：其余提示（图型由数据推断、结果被截断）保留，不做整体清空。
--
-- 幂等：JSON_SEARCH 检索不到即 0 行受影响；重复执行 0 处变更。
-- 边界：只处理 warnings 数组里以该前缀开头的元素，不触碰其它提示与文档正文。

UPDATE adh_render_documents
SET warnings = JSON_REMOVE(
        warnings,
        JSON_UNQUOTE(JSON_SEARCH(warnings, 'one', '本图数据由对话侧提供（未经服务端取数对账）%')))
WHERE JSON_VALID(warnings)
  AND JSON_SEARCH(warnings, 'one', '本图数据由对话侧提供（未经服务端取数对账）%') IS NOT NULL;
