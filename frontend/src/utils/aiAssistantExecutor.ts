/**
 * AI Assistant Executor — executes actions from AI assistant
 *
 * Handles navigation, form filling, and other UI actions.
 */

import client from '../api/client';

interface Action {
  type: string;
  page?: string;
  params?: Record<string, any>;
  form_type?: string;
  field_name?: string;
  value?: string;
  button_text?: string;
  button_selector?: string;
  form_state?: Record<string, any>;
}

interface ActionResult {
  success: boolean;
  message?: string;
  error?: string;
}

class AIAssistantExecutor {
  private navigate: ((path: string) => void) | null = null;
  private pendingActions: Action[] = [];
  private isExecuting = false;

  /**
   * 设置导航函数
   */
  setNavigate(navigate: (path: string) => void) {
    this.navigate = navigate;
  }

  /**
   * 获取待执行的操作
   */
  async fetchPendingActions(): Promise<Action[]> {
    try {
      const response = await client.get('/ai-assistant/tools/actions');
      this.pendingActions = response.data.actions || [];
      return this.pendingActions;
    } catch (error) {
      console.error('Fetch pending actions error:', error);
      return [];
    }
  }

  /**
   * 执行下一个操作
   */
  async executeNextAction(): Promise<ActionResult> {
    if (this.pendingActions.length === 0) {
      return { success: true, message: '没有待执行的操作' };
    }

    const action = this.pendingActions.shift();
    if (!action) {
      return { success: true, message: '没有待执行的操作' };
    }

    return this.executeAction(action);
  }

  /**
   * 执行所有待执行的操作
   */
  async executeAllActions(): Promise<ActionResult[]> {
    const results: ActionResult[] = [];

    while (this.pendingActions.length > 0) {
      const result = await this.executeNextAction();
      results.push(result);

      // 如果执行失败，停止执行
      if (!result.success) {
        console.warn('Action failed, stopping:', result.error);
        break;
      }

      // 等待页面响应，导航操作需要更长时间
      const lastAction = results.length > 0 ? this.pendingActions.length : 0;
      await this.delay(800);
    }

    return results;
  }

  /**
   * 执行单个操作
   */
  async executeAction(action: Action): Promise<ActionResult> {
    console.log('Executing action:', action);

    try {
      switch (action.type) {
        case 'navigate':
          return await this.executeNavigate(action);
        case 'open_form':
          return await this.executeOpenForm(action);
        case 'fill_field':
          return this.executeFillField(action);
        case 'submit_form':
          return this.executeSubmitForm(action);
        case 'cancel_form':
          return this.executeCancelForm(action);
        case 'click_button':
          return await this.executeClickButton(action);
        case 'show_confirmation':
          return this.executeShowConfirmation(action);
        default:
          return { success: false, error: `Unknown action type: ${action.type}` };
      }
    } catch (error: any) {
      console.error('Execute action error:', error);
      return { success: false, error: error.message || '执行操作时出错' };
    }
  }

  /**
   * 执行导航操作
   */
  private async executeNavigate(action: Action): Promise<ActionResult> {
    if (!action.page) {
      return { success: false, error: '目标页面不能为空' };
    }

    if (!this.navigate) {
      return { success: false, error: '导航函数未设置' };
    }

    // 构建URL
    let url = action.page;
    if (action.params) {
      const searchParams = new URLSearchParams();
      Object.entries(action.params).forEach(([key, value]) => {
        if (value !== undefined && value !== null) {
          searchParams.set(key, String(value));
        }
      });
      const queryString = searchParams.toString();
      if (queryString) {
        url += `?${queryString}`;
      }
    }

    this.navigate(url);

    // 等待页面加载
    await this.delay(1000);

    return { success: true, message: `已导航到: ${url}` };
  }

  /**
   * 执行打开表单操作
   */
  private async executeOpenForm(action: Action): Promise<ActionResult> {
    if (!action.page) {
      return { success: false, error: '目标页面不能为空' };
    }

    if (!this.navigate) {
      return { success: false, error: '导航函数未设置' };
    }

    // 构建URL，添加模式参数
    const params = new URLSearchParams();
    params.set('mode', action.params?.mode || 'create');
    if (action.params?.id) {
      params.set('id', action.params.id);
    }

    const url = `${action.page}?${params.toString()}`;
    this.navigate(url);

    // 等待页面加载
    await this.delay(1000);

    return { success: true, message: `已打开表单: ${action.form_type}` };
  }

  /**
   * 执行填写字段操作
   */
  private executeFillField(action: Action): ActionResult {
    if (!action.field_name) {
      return { success: false, error: '字段名不能为空' };
    }

    const value = action.value || '';

    // 查找字段元素
    const field = this.findField(action.field_name);
    if (!field) {
      return { success: false, error: `未找到字段: ${action.field_name}` };
    }

    // 填写字段
    this.setFieldValue(field, value);

    return { success: true, message: `已填写字段 ${action.field_name} = ${value}` };
  }

  /**
   * 执行提交表单操作
   */
  private executeSubmitForm(action: Action): ActionResult {
    // 查找提交按钮
    const submitButton = this.findButton('保存') || this.findButton('提交') || this.findButton('确定');
    if (!submitButton) {
      return { success: false, error: '未找到提交按钮' };
    }

    // 点击提交按钮
    submitButton.click();

    return { success: true, message: '已提交表单' };
  }

  /**
   * 执行取消表单操作
   */
  private executeCancelForm(action: Action): ActionResult {
    // 查找取消按钮
    const cancelButton = this.findButton('取消');
    if (!cancelButton) {
      return { success: false, error: '未找到取消按钮' };
    }

    // 点击取消按钮
    cancelButton.click();

    return { success: true, message: '已取消表单' };
  }

  /**
   * 执行点击按钮操作
   */
  private async executeClickButton(action: Action): Promise<ActionResult> {
    // 等待页面加载
    await this.delay(500);

    let button: HTMLElement | null = null;

    // 尝试多次查找按钮
    for (let attempt = 0; attempt < 3; attempt++) {
      if (action.button_selector) {
        button = document.querySelector(action.button_selector);
      } else if (action.button_text) {
        // 尝试多种方式查找按钮
        button = this.findButton(action.button_text);

        // 如果没找到，尝试模糊匹配
        if (!button) {
          button = this.findButtonFuzzy(action.button_text);
        }
      }

      if (button) {
        break;
      }

      // 等待一下再重试
      console.log(`Button not found, retrying (${attempt + 1}/3)...`);
      await this.delay(500);
    }

    if (!button) {
      console.error(`未找到按钮: ${action.button_text || action.button_selector}`);
      return { success: false, error: `未找到按钮: ${action.button_text || action.button_selector}` };
    }

    console.log(`Clicking button: ${action.button_text}`);
    button.click();

    // 等待点击后的响应
    await this.delay(300);

    return { success: true, message: `已点击按钮: ${action.button_text}` };
  }

  /**
   * 模糊查找按钮
   */
  private findButtonFuzzy(buttonText: string): HTMLElement | null {
    console.log(`Searching for button: ${buttonText}`);

    // 1. 查找所有按钮
    const buttons = document.querySelectorAll('button, [role="button"], a.btn, a.button');
    console.log(`Found ${buttons.length} buttons`);

    for (const button of buttons) {
      const text = button.textContent?.trim() || '';
      // 模糊匹配：包含关键词即可
      if (text.includes(buttonText) || buttonText.includes(text)) {
        console.log(`Found button by text: ${text}`);
        return button as HTMLElement;
      }
    }

    // 2. 查找包含特定图标的按钮
    const iconButtons = document.querySelectorAll('button svg, button .icon');
    for (const icon of iconButtons) {
      const parent = icon.parentElement;
      if (parent) {
        const parentText = parent.textContent?.trim() || '';
        if (parentText.includes(buttonText)) {
          console.log(`Found button by icon parent: ${parentText}`);
          return parent as HTMLElement;
        }
      }
    }

    // 3. 查找新建/添加按钮（常见模式）
    const createButtons = document.querySelectorAll('[class*="create"], [class*="add"], [class*="new"], [class*="plus"]');
    for (const btn of createButtons) {
      const text = btn.textContent?.trim() || '';
      if (text.includes(buttonText) || buttonText.includes(text)) {
        console.log(`Found button by class: ${text}`);
        return btn as HTMLElement;
      }
    }

    // 4. 列出所有按钮文本供调试
    const allButtonTexts = Array.from(buttons).map(b => b.textContent?.trim()).filter(Boolean);
    console.log('Available button texts:', allButtonTexts);

    return null;
  }

  /**
   * 执行显示确认对话框操作
   */
  private executeShowConfirmation(action: Action): ActionResult {
    const title = action.params?.title || '确认';
    const message = action.params?.message || '确定要执行此操作吗？';

    const confirmed = window.confirm(`${title}\n\n${message}`);

    return {
      success: true,
      message: confirmed ? '用户确认了操作' : '用户取消了操作'
    };
  }

  /**
   * 查找表单字段
   */
  private findField(fieldName: string): HTMLElement | null {
    // 尝试多种选择器
    const selectors = [
      `[name="${fieldName}"]`,
      `[id="${fieldName}"]`,
      `[data-field="${fieldName}"]`,
      `[placeholder*="${fieldName}"]`,
      `label:contains("${fieldName}") + input`,
      `label:contains("${fieldName}") + select`,
      `label:contains("${fieldName}") + textarea`,
    ];

    for (const selector of selectors) {
      try {
        const element = document.querySelector(selector);
        if (element) {
          return element as HTMLElement;
        }
      } catch {
        // 选择器语法错误，跳过
      }
    }

    // 尝试通过label文本查找
    const labels = document.querySelectorAll('label');
    for (const label of labels) {
      if (label.textContent?.includes(fieldName)) {
        const forId = label.getAttribute('for');
        if (forId) {
          const element = document.getElementById(forId);
          if (element) {
            return element;
          }
        }

        // 查找相邻的输入元素
        const parent = label.parentElement;
        if (parent) {
          const input = parent.querySelector('input, select, textarea');
          if (input) {
            return input as HTMLElement;
          }
        }
      }
    }

    return null;
  }

  /**
   * 查找按钮
   */
  private findButton(buttonText: string): HTMLElement | null {
    // 查找所有按钮
    const buttons = document.querySelectorAll('button, [role="button"], input[type="submit"], input[type="button"]');

    for (const button of buttons) {
      const text = button.textContent?.trim() || button.getAttribute('value') || '';
      if (text.includes(buttonText)) {
        return button as HTMLElement;
      }
    }

    return null;
  }

  /**
   * 设置字段值
   */
  private setFieldValue(field: HTMLElement, value: string) {
    const tagName = field.tagName.toLowerCase();
    const inputType = field.getAttribute('type')?.toLowerCase();

    if (tagName === 'input') {
      if (inputType === 'checkbox' || inputType === 'radio') {
        const isChecked = value === 'true' || value === '1' || value === 'yes';
        (field as HTMLInputElement).checked = isChecked;
      } else {
        // 使用React的值设置方式
        const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
          window.HTMLInputElement.prototype, 'value'
        )?.set;

        if (nativeInputValueSetter) {
          nativeInputValueSetter.call(field, value);
        } else {
          (field as HTMLInputElement).value = value;
        }
      }
    } else if (tagName === 'select') {
      const select = field as HTMLSelectElement;
      select.value = value;

      // 如果直接设置不生效，尝试查找选项
      if (select.value !== value) {
        const options = select.querySelectorAll('option');
        for (const option of options) {
          if (option.textContent?.includes(value) || option.value === value) {
            select.value = option.value;
            break;
          }
        }
      }
    } else if (tagName === 'textarea') {
      const nativeTextAreaValueSetter = Object.getOwnPropertyDescriptor(
        window.HTMLTextAreaElement.prototype, 'value'
      )?.set;

      if (nativeTextAreaValueSetter) {
        nativeTextAreaValueSetter.call(field, value);
      } else {
        (field as HTMLTextAreaElement).value = value;
      }
    }

    // 触发变更事件
    field.dispatchEvent(new Event('input', { bubbles: true }));
    field.dispatchEvent(new Event('change', { bubbles: true }));

    // 触发React的合成事件
    const event = new Event('input', { bubbles: true });
    Object.defineProperty(event, 'target', { value: field, enumerable: true });
    field.dispatchEvent(event);
  }

  /**
   * 清空待执行的操作
   */
  async clearPendingActions(): Promise<void> {
    try {
      await client.post('/ai-assistant/tools/clear');
      this.pendingActions = [];
    } catch (error) {
      console.error('Clear pending actions error:', error);
    }
  }

  /**
   * 延迟执行
   */
  private delay(ms: number): Promise<void> {
    return new Promise(resolve => setTimeout(resolve, ms));
  }
}

// 导出单例
export const aiAssistantExecutor = new AIAssistantExecutor();
export default aiAssistantExecutor;
