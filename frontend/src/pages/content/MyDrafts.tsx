import ArticleTable from './ArticleTable';

/**
 * 「我的草稿」: 当前用户名下的工作稿。
 *
 * 与「内容管理」共用同一张表（同一份动作口径），只把范围收窄到自己名下 ——
 * 服务端按登录身份识别 `mine`，不是让前端自由传用户名去看别人的草稿。
 */
export default function MyDrafts() {
  return <ArticleTable scope="mine" />;
}
