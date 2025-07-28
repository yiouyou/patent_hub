frappe.ui.form.on('Patent Workflow', {
  call_title2scene: async function(frm) {
    await run_step_backend(frm, "patent_hub.api.call_title2scene.run", "Title2Scene");
  },
  call_info2tech: async function(frm) {
    await run_step_backend(frm, "patent_hub.api.call_info2tech.run", "Info2Tech");
  },
  call_scene2tech: async function(frm) {
    await run_step_backend(frm, "patent_hub.api.call_scene2tech.run", "Scene2Tech");
  },
  call_tech2application: async function(frm) {
    await run_step_backend(frm, "patent_hub.api.call_tech2application.run", "Tech2Application");
  },
  call_align2tex2docx: async function(frm) {
    await run_step_backend(frm, "patent_hub.api.call_align2tex2docx.run", "Align2Tex2Docx");
  },
  call_review2revise: async function(frm) {
    await run_step_backend(frm, "patent_hub.api.call_review2revise.run", "Review2Revise");
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
      frm._realtime_bound = true;
    }
  }
});


/**
 * 通用任务执行器（后端独立处理状态逻辑）
 * @param {frappe.ui.form.Form} frm
 * @param {string} method_path
 * @param {string} label
 */
async function run_step_backend(frm, method_path, label) {
  try {
    await frappe.call({
      method: method_path,
      args: { docname: frm.doc.name },
      freeze: true,
      freeze_message: `运行 ${label} 中，请稍候...`
    });

    await frm.reload_doc(); // 后端执行成功后刷新表单
  } catch (e) {
    frappe.show_alert({
      message: e.message || `运行 ${label} 失败，请查看日志`,
      indicator: 'red'
    }, 7);
  }
}


/**
 * 通用绑定实时事件函数
 * @param {frappe.ui.form.Form} frm
 * @param {string} step_name
 * @param {string} label
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
