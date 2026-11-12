import ArticleTable from './ArticleTable';

/** 「内容管理」: 全部内容（草稿 / 待审 / 已发布 / 已下架），带发布与下架动作。 */
export default function ContentManage() {
  return <ArticleTable scope="all" />;
}
