frappe.ui.form.on('Md2docx', {
  refresh(frm) {
    update_md2docx_buttons(frm);

    // 绑定实时事件（仅一次）
    if (!frm._realtime_bound) {
      bind_realtime_md2docx_events(frm);
      frm._realtime_bound = true;
    }
  },

  // 主要输入字段变更 => 刷新按钮状态
  md: update_md2docx_buttons,
  is_patent_md2docx: update_md2docx_buttons,

  // ▶️ 正常运行按钮（首次）
  call_md2docx: async frm => await run_md2docx_backend(frm, "patent_hub.api.call_md2docx.run", "Md2docx"),

  // 🔁 强制重跑按钮（已执行过的任务才可用）
  rerun_md2docx: async frm => await run_md2docx_backend(frm, "patent_hub.api.call_md2docx.run", "Md2docx", { force: true }),

  // ❌ 取消运行按钮
  cancel_md2docx: async frm => await cancel_md2docx_backend(frm, "md2docx", "Md2docx"),
});

/**
 * 🔄 主函数：根据字段和状态更新按钮启用状态和样式
 */
function update_md2docx_buttons(frm) {
  const is_running = frm.doc.is_running_md2docx === 1;
  const is_done = frm.doc.is_done_md2docx === 1;
  const success_count = frm.doc.success_count_md2docx || 0;
  
  // 检查输入字段是否有值
  const has_md = !!frm.doc.md?.trim?.();
  
  // 判断是否曾经成功执行过
  const has_ever_succeeded = success_count > 0;

  // 控制按钮状态
  if (has_ever_succeeded) {
    // 曾经成功过：只显示 rerun 和 cancel 按钮
    toggle_md2docx_button_state(frm, 'call_md2docx', false); // 隐藏首次执行按钮
    toggle_md2docx_button_state(frm, 'rerun_md2docx', has_md && !is_running); // 重跑按钮
    toggle_md2docx_button_state(frm, 'cancel_md2docx', is_running, true); // 取消按钮
  } else {
    // 从未成功过：只显示 call 和 cancel 按钮
    toggle_md2docx_button_state(frm, 'call_md2docx', has_md && !is_running && !is_done); // 首次执行
    toggle_md2docx_button_state(frm, 'rerun_md2docx', false); // 隐藏重跑按钮
    toggle_md2docx_button_state(frm, 'cancel_md2docx', is_running, true); // 取消按钮
  }
}

/**
 * ✅ 控制按钮样式和启用状态
 */
function toggle_md2docx_button_state(frm, button_name, enabled, danger = false) {
  const btn = frm.get_field(button_name);
  if (btn && btn.$wrapper) {
    const $btn = btn.$wrapper.find('button');
    $btn.toggleClass('btn-primary', enabled && !danger);
    $btn.toggleClass('btn-danger', enabled && danger);
    $btn.toggleClass('btn-default', !enabled);
    $btn.prop('disabled', !enabled);
  }
}

/**
 * ▶️ 通用运行任务：执行前自动保存表单，避免丢失字段
 */
async function run_md2docx_backend(frm, method_path, label, extraArgs = {}) {
  console.log(`[DEBUG] 开始执行 ${label}`);
  try {
    // 只在表单有未保存更改时才保存
    if (frm.is_dirty()) {
      console.log(`[DEBUG] 检测到未保存更改，正在保存...`);
      await frm.save();
      console.log(`[DEBUG] 表单保存完成`);
    } else {
      console.log(`[DEBUG] 表单无更改，跳过保存`);
    }
    console.log(`[DEBUG] 调用后端方法...`);
    const response = await frappe.call({
      method: method_path,
      args: {
        docname: frm.doc.name,
        ...extraArgs
      },
      freeze: true,
      freeze_message: `运行 ${label} 中，请稍候...`
    });
    console.log(`[DEBUG] 后端响应:`, response);
    await frm.reload_doc();
  } catch (e) {
    console.error(`[DEBUG] 执行失败:`, e);
    frappe.show_alert({
      message: e.message || `运行 ${label} 失败，请查看日志`,
      indicator: 'red'
    }, 7);
  }
}

/**
 * ❌ 通用取消任务
 */
async function cancel_md2docx_backend(frm, task_key, label) {
  try {
    const r = await frappe.call({
      method: "patent_hub.api._utils.cancel_task",
      args: {
        docname: frm.doc.name,
        task_key: task_key
      },
      freeze: true,
      freeze_message: `正在终止 ${label} ...`
    });

    if (r.message) {
      frappe.show_alert({ message: r.message, indicator: "red" }, 5);
    }

    await frm.reload_doc();
  } catch (e) {
    frappe.show_alert({
      message: e.message || `终止 ${label} 失败`,
      indicator: "red"
    }, 7);
  }
}

/**
 * 📡 实时事件监听：done / failed
 */
function bind_realtime_md2docx_events(frm) {
  frappe.realtime.on('md2docx_done', data => {
    if (data.docname === frm.doc.name) {
      frappe.show_alert({ message: 'Md2docx 执行完成', indicator: 'blue' }, 3);
      frm.reload_doc();
    }
  });

  frappe.realtime.on('md2docx_failed', data => {
    if (data.docname === frm.doc.name) {
      frappe.show_alert({ message: '❌ Md2docx 执行失败', indicator: 'red' }, 7);
      frm.reload_doc();
    }
  });
}
