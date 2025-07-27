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
    setTimeout(() => toggle_preinfo_sections(frm), 100);  // 延迟处理确保已渲染
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
  preinfo_options(frm) {
    toggle_preinfo_sections(frm);
  },
  validate(frm) {
  }
});


/**
 * 根据 preinfo_options 字段值，控制预处理相关 section 显示
 * 规则：
 * - 若为空：两个 section 都不显示
 * - 若为 "title2scene"：仅显示 title2scene_section
 * - 若为 "info2tech"：仅显示 info2tech_section
 * @param {frappe.ui.form.Form} frm - 当前表单对象
 */
function toggle_preinfo_sections(frm) {
  const opt = (frm.doc.preinfo_options || '').trim().toLowerCase();
  frm.toggle_display('title2scene_section', opt === 'title2scene');
  frm.toggle_display('info2tech_section', opt === 'info2tech');
}


/**
 * 根据步骤名映射状态字段（如 title2scene → is_running_preinfo）
 * @param {string} step_name
 * @returns {{is_running_field: string, is_done_field: string}}
 */
function get_status_field(step_name) {
  if (["title2scene", "info2tech"].includes(step_name)) {
    return {
      is_running_field: "is_running_preinfo",
      is_done_field: "is_done_preinfo"
    };
  }
  return {
    is_running_field: `is_running_${step_name}`,
    is_done_field: `is_done_${step_name}`
  };
}


/**
 * 通用步骤执行器：自动处理 running/done 状态
 * @param {Object} frm - 当前 Frappe form
 * @param {String} step_name - 步骤名（如 "title2scene"）
 * @param {String} method_path - 服务端调用路径
 * @param {String} label - UI 提示名
 */
async function run_step_with_status(frm, step_name, method_path, label) {
  const { is_running_field, is_done_field } = get_status_field(step_name);
  try {
    // 开始运行
    frm.set_value(is_running_field, 1);
    frm.set_value(is_done_field, 0);
    await frm.save();
    await frappe.call({
      method: method_path,
      args: { docname: frm.doc.name },
      freeze: true,
      freeze_message: `运行 ${label} 中，请稍候...`
    });
    await frm.reload_doc(); // 刷新获取运行结果
  } catch (e) {
    frappe.show_alert({
      message: e.message || `运行 ${label} 失败，请查看日志`,
      indicator: 'red'
    }, 7);
    // 保守处理异常状态
    await frappe.model.set_value(frm.doctype, frm.docname, is_running_field, 0);
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

