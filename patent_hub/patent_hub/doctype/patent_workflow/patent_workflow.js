frappe.ui.form.on('Patent Workflow', {
  refresh(frm) {
    update_step_buttons(frm);

    if (!frm._realtime_bound) {
      const steps = [
        ["title2scene", "Title2Scene"],
        ["info2tech", "Info2Tech"],
        ["scene2tech", "Scene2Tech"],
        ["tech2application", "Tech2Application"],
        ["align2tex2docx", "Align2Tex2Docx"],
        ["review2revise", "Review2Revise"]
      ];
      steps.forEach(([step, label]) => {
        bind_realtime_step_events(frm, step, label);
      });
      frm._realtime_bound = true;
    }

    bind_table_events_once(frm, 'table_upload_info2tech');
    bind_table_events_once(frm, 'table_upload_review2revise');
  },

  // 主要输入字段变更 => 刷新按钮状态
  patent_title: update_step_buttons,
  scene: update_step_buttons,
  tech: update_step_buttons,
  application: update_step_buttons,

  // ▶️ 正常运行按钮（首次）
  call_title2scene: async frm => await run_step_backend(frm, "patent_hub.api.call_title2scene.run", "Title2Scene"),
  call_info2tech: async frm => await run_step_backend(frm, "patent_hub.api.call_info2tech.run", "Info2Tech"),
  call_scene2tech: async frm => await run_step_backend(frm, "patent_hub.api.call_scene2tech.run", "Scene2Tech"),
  call_tech2application: async frm => await run_step_backend(frm, "patent_hub.api.call_tech2application.run", "Tech2Application"),
  call_align2tex2docx: async frm => await run_step_backend(frm, "patent_hub.api.call_align2tex2docx.run", "Align2Tex2Docx"),
  call_review2revise: async frm => await run_step_backend(frm, "patent_hub.api.call_review2revise.run", "Review2Revise"),

  // 🔁 强制重跑按钮（已执行过的任务才可用）
  rerun_title2scene: async frm => await run_step_backend(frm, "patent_hub.api.call_title2scene.run", "Title2Scene", { force: true }),
  rerun_info2tech: async frm => await run_step_backend(frm, "patent_hub.api.call_info2tech.run", "Info2Tech", { force: true }),
  rerun_scene2tech: async frm => await run_step_backend(frm, "patent_hub.api.call_scene2tech.run", "Scene2Tech", { force: true }),
  rerun_tech2application: async frm => await run_step_backend(frm, "patent_hub.api.call_tech2application.run", "Tech2Application", { force: true }),
  rerun_align2tex2docx: async frm => await run_step_backend(frm, "patent_hub.api.call_align2tex2docx.run", "Align2Tex2Docx", { force: true }),
  rerun_review2revise: async frm => await run_step_backend(frm, "patent_hub.api.call_review2revise.run", "Review2Revise", { force: true }),

  // ❌ 取消运行按钮
  cancel_title2scene: async frm => await cancel_step_backend(frm, "title2scene", "Title2Scene"),
  cancel_info2tech: async frm => await cancel_step_backend(frm, "info2tech", "Info2Tech"),
  cancel_scene2tech: async frm => await cancel_step_backend(frm, "scene2tech", "Scene2Tech"),
  cancel_tech2application: async frm => await cancel_step_backend(frm, "tech2application", "Tech2Application"),
  cancel_align2tex2docx: async frm => await cancel_step_backend(frm, "align2tex2docx", "Align2Tex2Docx"),
  cancel_review2revise: async frm => await cancel_step_backend(frm, "review2revise", "Review2Revise"),

  download_application_docx: function(frm) {
		if (!frm.doc.application_docx_link) {
			frappe.msgprint('申请书 DOCX 文件不存在，请先运行 Align2Tex2Docx 任务');
			return;
		}
		let file_url = `/api/method/frappe.utils.file_manager.download_file?file_url=${encodeURIComponent('/files/' + frm.doc.application_docx_link)}`;
		window.open(file_url, '_blank');
	},
  download_reply_review: function(frm) {
		if (!frm.doc.reply_review_docx_link) {
			frappe.msgprint('回复审查意见 DOCX 文件不存在，请先运行 Review2Revise 任务');
			return;
		}
		let download_url = `/api/method/frappe.utils.file_manager.download_file?file_url=${frm.doc.reply_review_docx_link}`;
		window.open(download_url, '_blank');
	},
	download_revised_application: function(frm) {
		if (!frm.doc.revised_application_docx_link) {
			frappe.msgprint('修改后申请书 DOCX 文件不存在，请先运行 Review2Revise 任务');
			return;
		}
		let download_url = `/api/method/frappe.utils.file_manager.download_file?file_url=${frm.doc.revised_application_docx_link}`;
		window.open(download_url, '_blank');
	}
});

/**
 * 🔄 主函数：根据字段和状态更新按钮启用状态和样式
 */
function update_step_buttons(frm) {
  const steps = [
    "title2scene",
    "info2tech",
    "scene2tech",
    "tech2application",
    "align2tex2docx",
    "review2revise"
  ];

  const field_map = {
    title2scene: "patent_title",
    info2tech: "table_upload_info2tech",
    scene2tech: "scene",
    tech2application: "tech",
    align2tex2docx: "application",
    review2revise: "table_upload_review2revise"
  };

  steps.forEach(step => {
    const is_running = frm.doc[`is_running_${step}`] === 1;
    const is_done = frm.doc[`is_done_${step}`] === 1;
    const field = field_map[step];
    const is_table = field.startsWith("table_");
    const has_value = is_table
      ? Array.isArray(frm.doc[field]) && frm.doc[field].length > 0
      : !!frm.doc[field]?.trim?.();

    // 控制按钮状态
    toggle_button_state(frm, `call_${step}`, has_value && !is_running && !is_done); // 首次执行
    toggle_button_state(frm, `rerun_${step}`, has_value && !is_running && is_done); // 重跑
    toggle_button_state(frm, `cancel_${step}`, is_running, true); // 正在运行可取消
  });
}

/**
 * ✅ 控制按钮样式和启用状态
 */
function toggle_button_state(frm, button_name, enabled, danger = false) {
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
 * 🧩 绑定表格监听器（首次绑定）
 */
function bind_table_events_once(frm, table_fieldname) {
  if (frm[`_${table_fieldname}_bound`]) return;
  const grid = frm.fields_dict[table_fieldname]?.grid;
  if (grid && typeof grid.on === 'function') {
    ['row_removed', 'row_added', 'data_changed'].forEach(event => {
      grid.on(event, () => update_step_buttons(frm));
    });
    frm[`_${table_fieldname}_bound`] = true;
  }
}

/**
 * ▶️ 通用运行任务：执行前自动保存表单，避免丢失字段
 */
async function run_step_backend(frm, method_path, label, extraArgs = {}) {
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
async function cancel_step_backend(frm, task_key, label) {
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
function bind_realtime_step_events(frm, step_name, label) {
  frappe.realtime.on(`${step_name}_done`, data => {
    if (data.docname === frm.doc.name) {
      frappe.show_alert({ message: `${label} 执行完成`, indicator: 'blue' }, 3);
      frm.reload_doc();
    }
  });

  frappe.realtime.on(`${step_name}_failed`, data => {
    if (data.docname === frm.doc.name) {
      frappe.show_alert({ message: `❌ ${label} 执行失败`, indicator: 'red' }, 7);
      frm.reload_doc();
    }
  });
}
