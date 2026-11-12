/**
 * 可切换按钮（筛选开关 / 胶囊入口）的「选中态」口径 —— 一份实现，多处引用。
 *
 * 为什么要有这个文件：选中态原先各写一套，而「只换个 variant」在这套主题里等于没换。
 * 亮色主题的 `--secondary` 是 218 73% 97%（近乎白的蓝），落在白底卡片上肉眼分不出
 * 选中与未选中（用户反馈：「仅收藏」看不出有没有选中）。
 *
 * 因此选中态必须**四层叠加**，任何一层单独失效都还看得出来：
 * ① 底色 tint（bg-primary/12）② 文字色（text-primary）③ 字重（font-semibold）
 * ④ 描边（ring-1 ring-primary/35）。颜色之外还有字重与描边，也是对色弱用户的兜底
 * ——不用单一色相传达状态（与 ui-resource-display / 无障碍口径一致）。
 *
 * hover 也必须留在同一色系（hover:bg-primary/20）：按钮挂在 `variant="ghost"` 上时
 * 变体自带 `hover:bg-accent`，鼠标一划就把选中态刷成灰底，等于"一碰就看不出选中"。
 */
export const TOGGLE_ACTIVE_CLASS =
  'bg-primary/12 font-semibold text-primary ring-1 ring-primary/35 hover:bg-primary/20 hover:text-primary';
