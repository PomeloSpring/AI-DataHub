/**
 * ontologyObjects — canonical 本体 doc「对象清单」的读取适配层（纯函数、无副作用）。
 *
 * 背景：canonical doc 的对象身份字段是 `key` / `display_name`
 * （事实源：backend/modules/catalog/services/ontology_service.py 的 ONTOLOGY_SCHEMA_SPEC），
 * 但存量「源本体」（kind='source'，B6 退役中）落库的 doc 用的是旧字段名
 * `object_key` / `name`。前端直接读 `o.key` 会拿到 undefined 并在渲染期崩溃。
 *
 * 本模块只在**读取边界**做字段别名适配（不改写 doc、不影响保存内容），并把
 * 无法定位身份的元素作为 issue 显式返回给 UI 呈现——不静默丢弃、不假装有数据
 * （见 .qoder/rules/no-silent-degradation.md）。
 */

/** 元素无法当作对象展示的原因 */
export interface DocObjectIssue {
  /** 在 doc.objects 中的下标（0 基） */
  index: number;
  /** 可读定位描述（供 UI 提示，不暴露裸 id） */
  label: string;
  /** not_object=元素不是对象；no_identity=缺 key/object_key/primary_table */
  reason: 'not_object' | 'no_identity';
  /** 对用户可见的原因文案 */
  hint: string;
}

export interface NormalizeDocObjectsResult {
  /** 已补齐 key / display_name 的对象（顺序与 doc 一致，仅剔除无法展示的元素） */
  objects: Record<string, any>[];
  /** 被剔除的元素及原因，必须由调用方显式呈现 */
  issues: DocObjectIssue[];
}

const firstStr = (...vals: unknown[]): string => {
  for (const v of vals) {
    const s = String(v ?? '').trim();
    if (s) return s;
  }
  return '';
};

/**
 * 对象身份 key：canonical `key` → 旧字段 `object_key`/`code` → `primary_table` 回落。
 * 与服务端 save_draft 的 `normalize_object_key(obj.key) or primary_table` 同口径。
 */
export function resolveObjectKey(obj: unknown): string {
  if (!obj || typeof obj !== 'object' || Array.isArray(obj)) return '';
  const o = obj as Record<string, any>;
  return firstStr(o.key, o.object_key, o.code, o.primary_table);
}

/** 对象可读名：canonical `display_name` → 旧字段 `name`/`label` → key */
export function resolveDisplayName(obj: unknown, key: string): string {
  if (!obj || typeof obj !== 'object' || Array.isArray(obj)) return key;
  const o = obj as Record<string, any>;
  return firstStr(o.display_name, o.name, o.label, key);
}

/**
 * M2/M3/M4（行为 / 规则 / 场景）建模位的可见域：只对系统能力本体开放。
 * 业务本体是路由索引层（对象 + route），源本体是某数据源物理表的语义投影——
 * 两者都不承载行为/规则/场景（写动作权限由功能权限码把关，口径规则入指标字典）。
 * 空 kind 的存量模型维持可见，不因分域判别而误伤。
 */
export function showsM234(kind?: string): boolean {
  return kind !== 'business' && kind !== 'source';
}

/**
 * 「适用站点」页签的可见域：源本体不展示。
 * 站点声明是**模型级**的跨站路由配置（一份契约 × N 站部署），而源本体本身就是**单个数据源**
 * 物理表的语义投影——对它谈“部署到哪几站”没有意义（v0.5 拍板：绑定层不属于本体）。
 * 空 kind 的存量模型维持可见，不因分域判别而误伤。
 */
export function showsSiteScope(kind?: string): boolean {
  return kind !== 'source';
}

/**
 * 「取数路径（承载）」页签的可见域：仅业务本体。
 *
 * 承载是**跨源拓扑**（一个概念有哪几条可取数路径：各站明细 / 数仓全局汇总 / 日志源）。
 * 源本体本身就是单源物理投影——它就是被别人指向的那一方，对它谈“还有哪几条路径”无意义；
 * 系统本体只描述系统能力面，同样不适用。空 kind 的存量模型不展示（无承载可配）。
 */
export function showsCarriers(kind?: string): boolean {
  return kind === 'business';
}

/**
 * doc 里已声明的行为/规则/场景条数。
 * 页签隐藏后用于判断是否存在“存量声明被隐藏”的情况——那必须显式提示，
 * 不得静默把仍在 doc 里的建模声明藏起来（no-silent-degradation）。
 */
export function countM234Declarations(raw: unknown): number {
  if (!raw || typeof raw !== 'object') return 0;
  const doc = raw as Record<string, any>;
  const objects = Array.isArray(doc.objects) ? doc.objects : [];
  const actions = objects.reduce(
    (acc: number, o: any) => acc + (Array.isArray(o?.actions) ? o.actions.length : 0), 0);
  const rules = Array.isArray(doc.rules) ? doc.rules.length : 0;
  const scenarios = Array.isArray(doc.scenarios) ? doc.scenarios.length : 0;
  return actions + rules + scenarios;
}

/**
 * 把 doc.objects 归一为前端可直接渲染的对象清单。
 * 返回值同时给出 issues：调用方必须把 issues 显示出来（而非当作空清单）。
 */
export function normalizeDocObjects(raw: unknown): NormalizeDocObjectsResult {
  const items = Array.isArray(raw) ? raw : [];
  const objects: Record<string, any>[] = [];
  const issues: DocObjectIssue[] = [];
  items.forEach((it, index) => {
    if (!it || typeof it !== 'object' || Array.isArray(it)) {
      issues.push({
        index,
        label: `第 ${index + 1} 项`,
        reason: 'not_object',
        hint: 'objects 元素必须是对象',
      });
      return;
    }
    const key = resolveObjectKey(it);
    if (!key) {
      issues.push({
        index,
        label: `第 ${index + 1} 项`,
        reason: 'no_identity',
        hint: '缺少 key（或旧字段 object_key / primary_table），无法定位对象身份',
      });
      return;
    }
    const o = it as Record<string, any>;
    objects.push({ ...o, key, display_name: resolveDisplayName(o, key) });
  });
  return { objects, issues };
}
