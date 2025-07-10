frappe.ui.form.on('MD To Docx', {
    refresh: function(frm) {
        frm.add_custom_button(__('Run'), function () {
            if (frm.doc.is_done) {
                frappe.show_alert({
                    message: "该任务已完成，不可重复运行。",
                    indicator: "orange"
                }, 5);
                return;
            }
            // 强制刷新最新状态，避免重复点击无效
            frm.reload_doc().then(() => {
                frappe.call({
                    method: 'patent_hub.api.run_md_to_docx.run',
                    args: { docname: frm.doc.name },
                    callback: function() {
                        frappe.show_alert({
                            message: "任务已提交，请稍后刷新查看结果",
                            indicator: "blue"
                        }, 7);
                    }
                });
            });
        });
        if (!frm._realtime_bound) {
            frappe.realtime.on("md_to_docx_done", function(data) {
                if (data.docname === frm.doc.name) {
                    frappe.show_alert({
                        message: "📄 文档已生成！",
                        indicator: "green"
                    }, 7);
                    frm.reload_doc();
                }
            });
            frappe.realtime.on("md_to_docx_failed", function(data) {
                if (data.docname === frm.doc.name) {
                    frappe.show_alert({
                        message: `❌ 生成失败：${data.error}`,
                        indicator: "red"
                    }, 7);
                    frm.reload_doc();
                }
            });
            frm._realtime_bound = true;
        }
    }
});
