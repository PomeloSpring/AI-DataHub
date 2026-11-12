/**
 * 剪贴板写入的唯一入口。
 *
 * 口径：写失败必须抛错，由调用方把原因提示给用户 —— 不允许"点了复制但没写进去"
 * 还回一个成功（那等于把缺陷藏成"看起来能用"）。
 *
 * 两条路径：
 * 1. 异步 Clipboard API（需安全上下文：https 或 localhost）；
 * 2. 回落临时 textarea + execCommand('copy')（非安全上下文/无焦点时唯一可行的路子）。
 */

/** 纯文本写入。两条路都失败则抛错。 */
export async function copyPlainText(text: string): Promise<void> {
  const clipboard = navigator.clipboard;
  if (clipboard?.writeText) {
    try {
      await clipboard.writeText(text);
      return;
    } catch {
      // 继续走回落：权限被拒/文档失焦都可能是这里失败的原因
    }
  }
  legacyCopy(text);
}

/**
 * 富文本写入：同时给出 text/html 与 text/plain，粘贴方按目标格式取用。
 *
 * ClipboardItem（写 HTML 的唯一途径）在部分浏览器/非安全上下文不可用，
 * 此时降级为只写纯文本 —— 内容不丢，只是丢格式，属可用回落而非失败，
 * 但返回值要如实告知调用方（界面据此说明"已复制为纯文本"）。
 */
export async function copyRichText(html: string, text: string): Promise<'rich' | 'plain'> {
  const clipboard = navigator.clipboard;
  const Item = (globalThis as any).ClipboardItem;
  if (clipboard?.write && typeof Item === 'function') {
    try {
      await clipboard.write([new Item({
        'text/html': new Blob([html], { type: 'text/html' }),
        'text/plain': new Blob([text], { type: 'text/plain' }),
      })]);
      return 'rich';
    } catch {
      // 落纯文本路径
    }
  }
  await copyPlainText(text);
  return 'plain';
}

/**
 * textarea + execCommand('copy') 回落。
 *
 * 同步执行：必须在用户手势的同一轮里完成，异步（await 之后）调用会被浏览器拒。
 */
function legacyCopy(text: string): void {
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.setAttribute('readonly', '');
  ta.style.position = 'fixed';
  ta.style.top = '-9999px';
  document.body.appendChild(ta);
  ta.select();
  let ok = false;
  try {
    ok = document.execCommand('copy');
  } catch {
    ok = false;
  } finally {
    document.body.removeChild(ta);
  }
  if (!ok) throw new Error('当前浏览器不允许写入剪贴板');
}
