frappe.ui.form.on('MD To Docx', {
    refresh: function(frm) {
        frm.add_custom_button(__('Run'), function () {
            // 检查任务是否已完成
            if (frm.doc.is_done) {
                frappe.show_alert({
                    message: "任务已完成，不可重复运行。",
                    indicator: "orange"
                }, 5);
                return;
            }
            // 检查是否正在运行中
            if (frm.doc.is_running) {
                frappe.show_alert({
                    message: "任务正在运行中，请等待完成后再试。",
                    indicator: "yellow"
                }, 5);
                return;
            }
            // 强制刷新最新状态，避免重复点击无效
            frm.reload_doc().then(() => {
                // 再次检查状态（防止刷新后状态变化）
                if (frm.doc.is_done) {
                    frappe.show_alert({
                        message: "任务已完成，不可重复运行。",
                        indicator: "orange"
                    }, 5);
                    return;
                }
                if (frm.doc.is_running) {
                    frappe.show_alert({
                        message: "任务正在运行中，请等待完成后再试。",
                        indicator: "yellow"
                    }, 5);
                    return;
                }
                // 满足条件，启动任务
                frappe.call({
                    method: 'patent_hub.api.run_md_to_docx.run',
                    args: { docname: frm.doc.name },
                    callback: function(r) {
                        if (r.message && r.message.success) {
                            frappe.show_alert({
                                message: "任务已提交，请稍后刷新查看结果",
                                indicator: "blue"
                            }, 7);
                        } else {
                            frappe.show_alert({
                                message: r.message && r.message.error ? r.message.error : "任务提交失败",
                                indicator: "red"
                            }, 7);
                        }
                    },
                    error: function(r) {
                        frappe.show_alert({
                            message: "任务提交失败，请检查网络连接或联系管理员",
                            indicator: "red"
                        }, 7);
                    }
                });
            });
        });
        // 绑定实时事件监听
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
