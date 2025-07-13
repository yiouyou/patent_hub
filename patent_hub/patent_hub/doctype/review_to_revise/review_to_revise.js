// Copyright (c) 2025, sz and contributors
// For license information, please see license.txt

// frappe.ui.form.on("Review To Revise", {
// 	refresh(frm) {

// 	},
// });
frappe.ui.form.on('Review To Revise', {
  refresh(frm) {
    frm.add_custom_button(__('→ Upload Final Docx'), () => {
      if (frm.doc.upload_final_docx_id) {
        frappe.set_route('Form', 'Upload Final Docx', frm.doc.upload_final_docx_id);
      } else {
        frappe.msgprint(__('No associated Upload Final Docx found.'));
      }
    });
    // ✅ 运行任务按钮
    frm.add_custom_button(__('▶️ Run'), async function () {
      try {
        // 🟡 先处理未保存的新文档（new-review-to-revise-xxx）
        if (frm.is_new()) {
          await frm.save();      // 保存
          await frm.reload_doc();  // 必须刷新获取新 name
        }
        // 🟡 再处理脏数据（已存在但有修改）
        if (frm.is_dirty()) {
          await frm.save();
          await frm.reload_doc();  // 保证最新状态
        }
        // 🟢 状态判断
        if (frm.doc.is_done) {
          frappe.show_alert({ message: '任务已完成，不可重复运行。', indicator: 'orange' }, 7);
          return;
        }
        if (frm.doc.is_running) {
          frappe.show_alert({ message: '任务正在运行中，请稍候完成。', indicator: 'orange' }, 7);
          return;
        }
        // 🟠 检查 review_pdf 字段
        if (!frm.doc.review_pdf || !frm.doc.review_pdf.toLowerCase().endsWith('.pdf')) {
          frappe.show_alert({ message: '❗请先上传 Review PDF 再运行任务。', indicator: 'red' }, 7);
          return;
        }
        // 🚀 提交任务
        const res = await frappe.call({
          method: 'patent_hub.api.run_review_to_revise.run',
          args: { docname: frm.doc.name },
          freeze: true,
          freeze_message: '任务提交中，请稍候...'
        });
        if (res.message?.success) {
          frappe.show_alert({ message: '✅ 任务已提交，稍后会自动刷新结果。', indicator: 'blue' }, 7);
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
    // 🔁 刷新链接按钮
    frm.add_custom_button(__('🔁 刷新链接'), async function () {
      if (frm.is_dirty()) {
        await frm.save();
      }
      await frappe.call({
        method: 'patent_hub.api.run_review_to_revise.generate_signed_urls',
        args: { docname: frm.doc.name },
        freeze: true,
        freeze_message: '生成预览链接中...'
      });
      await frm.reload_doc();
      frappe.show_alert({ message: '✅ 已刷新链接', indicator: 'blue' }, 7);
    });
    // 🔔 实时事件绑定
    if (!frm._realtime_bound) {
      frappe.realtime.on('review_to_revise_done', data => {
        if (data.docname === frm.doc.name) {
          frappe.show_alert({ message: '📄 文档已生成完成！', indicator: 'blue' }, 7);
          frm.reload_doc();
        }
      });
      frappe.realtime.on('review_to_revise_failed', data => {
        if (data.docname === frm.doc.name) {
          frappe.show_alert({ message: `❌ 生成失败：${data.error}`, indicator: 'red' }, 7);
          frm.reload_doc();
        }
      });
      frm._realtime_bound = true;
    }
    setup_clickable_column(frm);
  },
  validate(frm) {
    const fieldname = "review_pdf";
    const maxSizeMB = 10;
    const fileUrl = frm.doc[fieldname];
    if (!fileUrl) return;
    if (!fileUrl.toLowerCase().endsWith(".pdf")) {
      frappe.throw("请上传 PDF 格式的文件！");
    }
    const attachments = frm.get_docinfo()?.attachments || [];
    const matched = attachments.find(file => file.file_url === fileUrl);
    if (matched?.file_size && matched.file_size > maxSizeMB * 1024 * 1024) {
      frappe.throw(`❗上传文件不能超过 ${maxSizeMB}MB，请重新上传。`);
    }
  }
});


function setup_clickable_column(frm) {
  let grid_wrapper = frm.fields_dict['generated_files'].grid.wrapper;
  // 移除之前的样式和事件
  grid_wrapper.find('style.custom-clickable-style').remove();
  grid_wrapper.off('click.custom-clickable');
  // 添加 CSS 样式 - 只针对数据行，不包括表头
  grid_wrapper.append(`
    <style class="custom-clickable-style">
      .grid-body .grid-row [data-fieldname="signed_url"] {
        cursor: pointer !important;
        color: #007bff !important;
        text-decoration: underline !important;
      }
      .grid-body .grid-row [data-fieldname="signed_url"]:hover {
        color: #0056b3 !important;
        background-color: #f8f9fa !important;
      }
      /* 确保表头不受影响 */
      .grid-header [data-fieldname="signed_url"] {
        cursor: default !important;
        color: inherit !important;
        text-decoration: none !important;
      }
    </style>
  `);
  // 使用事件委托 - 只针对数据行
  grid_wrapper.on('click.custom-clickable', '.grid-body .grid-row [data-fieldname="signed_url"]', function(e) {
    e.preventDefault();
    e.stopPropagation();
    let $cell = $(this);
    let $row = $cell.closest('.grid-row');
    let row_index = $row.index();
    // 从文档数据获取URL（最可靠的方式）
    let url = frm.doc.generated_files[row_index].signed_url;
    // 验证URL是否有效
    if (!url || url === '' || url === 'undefined') {
      frappe.msgprint({
        title: '错误',
        message: '无效的URL',
        indicator: 'red'
      });
      return;
    }
    // 确保URL格式正确
    if (!url.startsWith('http://') && !url.startsWith('https://')) {
      url = 'https://' + url;
    }
    // 在新窗口打开链接
    try {
      window.open(url, '_blank', 'noopener,noreferrer');
    } catch (error) {
      frappe.msgprint({
        title: '错误',
        message: '无法打开链接: ' + error.message,
        indicator: 'red'
      });
    }
  });
  // 等待DOM渲染完成后应用样式
  setTimeout(() => {
    apply_clickable_styles(grid_wrapper);
  }, 100);
}

function apply_clickable_styles(grid_wrapper) {
  // 只为数据行的指定列添加样式，排除表头
  grid_wrapper.find('.grid-body .grid-row [data-fieldname="signed_url"]').each(function() {
    let $cell = $(this);
    let url = $cell.text().trim();
    // 只为有效URL添加样式
    if (url && url !== '' && url !== 'undefined') {
      $cell.addClass('clickable-url-cell');
    }
  });
}
