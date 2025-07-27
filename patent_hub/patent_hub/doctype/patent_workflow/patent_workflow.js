frappe.ui.form.on('Patent Workflow', {
  call_title2scene: async function(frm) {
    await run_step_with_status(frm, "title2scene", "patent_hub.api.call_title2scene.run", "Title2Scene");
  },
  call_info2tech: async function(frm) {
    await run_step_with_status(frm, "info2tech", "patent_hub.api.call_info2tech.run", "Info2Tech");
  },
  call_scene2tech: async function(frm) {
    await run_step_with_status(frm, "scene2tech", "patent_hub.api.call_scene2tech.run", "Scene2Tech");
  },
  call_tech2application: async function(frm) {
    await run_step_with_status(frm, "tech2application", "patent_hub.api.call_tech2application.run", "Tech2Application");
  },
  call_align2tex2docx: async function(frm) {
    await run_step_with_status(frm, "align2tex2docx", "patent_hub.api.call_align2tex2docx.run", "Align2Tex2Docx");
  },
  call_review2revise: async function(frm) {
    await run_step_with_status(frm, "review2revise", "patent_hub.api.call_review2revise.run", "Review2Revise");
  },
  refresh(frm) {
    // 🔔 实时事件绑定
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
      frm._realtime_bound = true; // 防止重复绑定
    }
  },
  validate(frm) {
  }
});


/**
 * 获取与指定步骤相关的所有状态字段名（适用于单独字段）
 *
 * 返回字段：
 * - is_running_field：运行中标志字段
 * - is_done_field：已完成标志字段
 * - status_field：状态文字描述字段（如 "Running" / "Done" / "Failed"）
 * - started_at_field：任务启动时间字段（建议后台也使用）
 *
 * @param {string} step_name
 * @returns {{
 *   is_running_field: string,
 *   is_done_field: string,
 *   status_field: string,
 *   started_at_field: string
 * }}
 */
function get_status_field(step_name) {
  return {
    is_running_field: `is_running_${step_name}`,
    is_done_field: `is_done_${step_name}`,
    status_field: `status_${step_name}`,
    started_at_field: `${step_name}_started_at`
  };
}


/**
 * 通用任务执行器（自动处理状态字段）
 * @param {frappe.ui.form.Form} frm - 当前 Frappe 表单对象
 * @param {string} step_name - 步骤名，如 "info2tech"
 * @param {string} method_path - 后端方法路径
 * @param {string} label - 前端用户提示名称
 */
async function run_step_with_status(frm, step_name, method_path, label) {
  const {
    is_running_field,
    is_done_field,
    status_field,
    started_at_field
  } = get_status_field(step_name);

  try {
    // 启动状态设置
    frm.set_value(is_running_field, 1);
    frm.set_value(is_done_field, 0);
    frm.set_value(status_field, "Running");
    frm.set_value(started_at_field, frappe.datetime.now_datetime());
    await frm.save();

    // 调用后端方法
    await frappe.call({
      method: method_path,
      args: { docname: frm.doc.name },
      freeze: true,
      freeze_message: `运行 ${label} 中，请稍候...`
    });

    // 后端完成后刷新文档
    await frm.reload_doc();
  } catch (e) {
    frappe.show_alert({
      message: e.message || `运行 ${label} 失败，请查看日志`,
      indicator: 'red'
    }, 7);

    // 如果请求失败，回滚运行中标志
    await frappe.model.set_value(frm.doctype, frm.docname, is_running_field, 0);
    await frappe.model.set_value(frm.doctype, frm.docname, status_field, "Failed");
  }
}


/**
 * 通用绑定实时事件函数
 * @param {frappe.ui.form.Form} frm - 当前表单
 * @param {string} step_name - 步骤名（如 "title2scene"）
 * @param {string} label - 显示用标签（如 "Title2Scene"）
 */
function bind_realtime_step_events(frm, step_name, label) {
  const done_event = `${step_name}_done`;
  const fail_event = `${step_name}_failed`;
  frappe.realtime.on(done_event, data => {
    if (data.docname === frm.doc.name) {
      frappe.show_alert({
        message: `${label} 执行完成`,
        indicator: 'blue'
      }, 3);
      frm.reload_doc();
    }
  });
  frappe.realtime.on(fail_event, data => {
    if (data.docname === frm.doc.name) {
      frappe.show_alert({
        message: `❌ ${label} 执行失败`,
        indicator: 'red'
      }, 7);
      frm.reload_doc();
    }
  });
}

