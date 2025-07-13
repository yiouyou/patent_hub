frappe.ui.form.on('Upload Final Docx', {
  refresh(frm) {
    frm.add_custom_button(__('→ Docx Proofreading'), () => {
      if (frm.doc.docx_proofreading_id) {
        frappe.set_route('Form', 'Docx Proofreading', frm.doc.docx_proofreading_id);
      } else {
        frappe.msgprint(__('No associated Docx Proofreading found.'));
      }
    });
    frm.add_custom_button(__('+ Review To Revise'), () => {
      if (!frm.doc.is_done) {
        frappe.show_alert({ message: '任务未完成，不能下一步。', indicator: 'red' }, 7);
        return;
      }
      frappe.new_doc('Review To Revise', {}, (doc) => {
        doc.patent_title = frm.doc.patent_title
        doc.writer_id = frm.doc.writer_id
        doc.patent_id = frm.doc.patent_id
        doc.scene_to_tech_id = frm.doc.scene_to_tech_id
        doc.tech_to_claims_id = frm.doc.tech_to_claims_id
        doc.claims_to_docx_id = frm.doc.claims_to_docx_id
        doc.docx_proofreading_id = frm.doc.docx_proofreading_id
        doc.upload_final_docx_id = frm.doc.upload_final_docx_id
        doc.save();
      });
    });
    // ⬆️ 上传按钮
    frm.add_custom_button(__('⬆️ 上传'), async function () {
      try {
        // 🟡 先处理未保存的新文档
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
          frappe.show_alert({ message: '上传已完成，不可重复运行。', indicator: 'orange' }, 7);
          return;
        }
        // 检查是否有文件需要上传
        const has_markdown = frm.doc.final_markdown;
        const has_docx = frm.doc.final_docx;
        if (!has_markdown || !has_docx) {
          frappe.show_alert({ 
            message: '❗请先上传 Final Markdown 和 Final Docx 文件', 
            indicator: 'red' 
          }, 7);
          return;
        }
        // 提交上传任务
        const res = await frappe.call({
          method: 'patent_hub.api.upload_final_docx.upload_files',
          args: { docname: frm.doc.name },
          freeze: true,
          freeze_message: '正在上传文件到S3...'
        });
        if (res.message?.success) {
          frappe.show_alert({ 
            message: '✅ 文件上传成功！', 
            indicator: 'green' 
          }, 5);
          await frm.reload_doc();
          update_download_buttons(frm);
        } else {
          throw new Error(res.message?.error || '上传失败');
        }
      } catch (err) {
        frappe.show_alert({
          message: `❌ 上传失败：${err.message}`,
          indicator: 'red'
        }, 6);
      }
    });
    // 🔁 刷新链接按钮
    frm.add_custom_button(__('🔁 刷新链接'), async function () {
      if (frm.is_dirty()) {
        await frm.save();
      }
      // 检查是否有 s3_url
      const has_s3_files = frm.doc.generated_files && 
                          frm.doc.generated_files.some(file => file.s3_url);
      if (!has_s3_files) {
        frappe.show_alert({ 
          message: '没有 S3 文件需要生成链接，请先上传文件', 
          indicator: 'orange' 
        }, 5);
        return;
      }
      await frappe.call({
        method: 'patent_hub.api.file_list.generate_signed_urls',
        args: {
          doclabel: 'Upload Final Docx',
          docname: frm.doc.name,
        },
        freeze: true,
        freeze_message: '生成预览链接中...'
      });
      await frm.reload_doc();
      frappe.show_alert({ message: '✅ 已刷新链接', indicator: 'blue' }, 7);
      // 刷新后更新按钮状态
      update_download_buttons(frm);
    });
    setup_clickable_column(frm);
    update_download_buttons(frm);
  }
});


// 检查链接是否过期（1小时）
function is_url_expired(generated_at) {
  if (!generated_at) {
    return true;
  }
  let generated;
  if (typeof generated_at === 'string') {
    generated = frappe.datetime.str_to_obj(generated_at);
  } else {
    generated = new Date(generated_at);
  }
  const now = frappe.datetime.now_datetime();
  const nowObj = frappe.datetime.str_to_obj(now);
  const diffMs = nowObj.getTime() - generated.getTime();
  const diffHours = diffMs / (1000 * 60 * 60);
  return diffHours >= 1;
}


// 从 s3_url 中找到对应的文件
function find_file_by_type(files, type) {
  if (!files || !Array.isArray(files)) return null;
  for (let file of files) {
    if (!file.s3_url) continue;
    if (type === 'markdown') {
      // final_markdown 以 "final_markdown.txt" 或 "final_markdown.md" 结尾
      if (file.s3_url.includes('final_markdown') && 
          (file.s3_url.endsWith('.txt') || file.s3_url.endsWith('.md'))) {
        return file;
      }
    } else if (type === 'docx') {
      // final_docx 以 "final_docx.docx" 结尾
      if (file.s3_url.includes('final_docx') && file.s3_url.endsWith('.docx')) {
        return file;
      }
    }
  }
  return null;
}


// 更新下载按钮状态
function update_download_buttons(frm) {
  const markdown_file = find_file_by_type(frm.doc.generated_files, 'markdown');
  const docx_file = find_file_by_type(frm.doc.generated_files, 'docx');
  // 这里可以添加按钮状态更新逻辑，如果需要的话
  // 目前主要是为了保持与 claims_to_docx.js 的一致性
  console.log('Markdown file found:', !!markdown_file);
  console.log('Docx file found:', !!docx_file);
}


// 设置可点击列
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
    // 从文档数据获取URL
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
    // 检查链接是否过期
    const file = frm.doc.generated_files[row_index];
    if (is_url_expired(file.signed_url_generated_at)) {
      frappe.msgprint({
        title: '链接已过期',
        message: '此链接已过期（超过1小时），请先点击"🔁 刷新链接"按钮',
        indicator: 'orange'
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
