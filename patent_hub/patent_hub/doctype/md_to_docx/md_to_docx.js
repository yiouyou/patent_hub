frappe.ui.form.on('MD To Docx', {
    refresh: function (frm) {
        frm.add_custom_button(__('Run'), async function () {
            try {
                // 🟡 先处理未保存的新文档（new-md-to-docx-xxx）
                if (frm.is_new()) {
                    await frm.save();          // 保存
                    await frm.reload_doc();    // 必须刷新获取新 name
                }
                // 🟡 再处理脏数据（已存在但有修改）
                if (frm.is_dirty()) {
                    await frm.save();
                    await frm.reload_doc();    // 保证最新状态
                }
                // 🟢 状态判断
                if (frm.doc.is_done) {
                    frappe.show_alert({ message: '任务已完成，不可重复运行。', indicator: 'orange' }, 5);
                    return;
                }
                if (frm.doc.is_running) {
                    frappe.show_alert({ message: '任务正在运行中，请稍候完成。', indicator: 'yellow' }, 5);
                    return;
                }
                // 🚀 提交任务
                const res = await frappe.call({
                    method: 'patent_hub.api.run_md_to_docx.run',
                    args: { docname: frm.doc.name },
                    freeze: true,
                    freeze_message: '任务提交中，请稍候...'
                });
                if (res.message?.success) {
                    frappe.show_alert({ message: '✅ 任务已提交，稍后会自动刷新结果。', indicator: 'blue' }, 6);
                } else {
                    throw new Error(res.message?.error || '未知错误');
                }
            } catch (err) {
                frappe.show_alert({
                    message: `❌ 提交失败：${err.message}`,
                    indicator: 'red'
                }, 6);
            }
        });
        // 🔔 实时事件绑定
        if (!frm._realtime_bound) {
            frappe.realtime.on('md_to_docx_done', data => {
                if (data.docname === frm.doc.name) {
                    frappe.show_alert({ message: '📄 文档已生成完成！', indicator: 'green' }, 7);
                    frm.reload_doc();
                }
            });
            frappe.realtime.on('md_to_docx_failed', data => {
                if (data.docname === frm.doc.name) {
                    frappe.show_alert({ message: `❌ 生成失败：${data.error}`, indicator: 'red' }, 7);
                    frm.reload_doc();
                }
            });
            frm._realtime_bound = true;
        }
    }
});
