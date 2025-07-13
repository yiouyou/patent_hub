frappe.ui.form.on('Claims To Docx', {
  refresh(frm) {
    frm.add_custom_button(__('→ Tech To Claims'), () => {
      if (frm.doc.tech_to_claims_id) {
        frappe.set_route('Form', 'Tech To Claims', frm.doc.tech_to_claims_id);
      } else {
        frappe.msgprint(__('No associated Tech To Claims found.'));
      }
    });
    frm.add_custom_button(__('+ Docx Proofreading'), () => {
      if (!frm.doc.is_done) {
        frappe.show_alert({ message: '任务未完成，不能下一步。', indicator: 'red' }, 7);
        return;
      }
      frappe.new_doc('Docx Proofreading', {}, (doc) => {
        doc.writer_id = frm.doc.writer_id
        doc.patent_id = frm.doc.patent_id
        doc.scene_to_tech_id = frm.doc.scene_to_tech_id
        doc.tech_to_claims_id = frm.doc.tech_to_claims_id
        doc.patent_title = frm.doc.patent_title
        doc.claims_to_docx_id = frm.doc.claims_to_docx_id
        doc.save();
      });
    });
    // ✅ 运行任务按钮
    frm.add_custom_button(__('▶️ Run'), async function () {
      try {
        // 🟡 先处理未保存的新文档（new-claims-to-docx-xxx）
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
        // 🟠 检查 claims 字段
        if (!frm.doc.claims) {
          frappe.show_alert({ message: '❗请先填写 Claims 再运行任务。', indicator: 'red' }, 7);
          return;
        }
        // 🚀 提交任务
        const res = await frappe.call({
          method: 'patent_hub.api.run_claims_to_docx.run',
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
        method: 'patent_hub.api.run_claims_to_docx.generate_signed_urls',
        args: { docname: frm.doc.name },
        freeze: true,
        freeze_message: '生成预览链接中...'
      });
      await frm.reload_doc();
      frappe.show_alert({ message: '✅ 已刷新链接', indicator: 'blue' }, 7);
      // 刷新后更新下载按钮状态
      update_download_buttons(frm);
    });
    // 🔔 实时事件绑定
    if (!frm._realtime_bound) {
      frappe.realtime.on('claims_to_docx_done', data => {
        if (data.docname === frm.doc.name) {
          frappe.show_alert({ message: '📄 文档已生成完成！', indicator: 'blue' }, 7);
          frm.reload_doc().then(() => {
            update_download_buttons(frm);
          });
        }
      });
      frappe.realtime.on('claims_to_docx_failed', data => {
        if (data.docname === frm.doc.name) {
          frappe.show_alert({ message: `❌ 生成失败：${data.error}`, indicator: 'red' }, 7);
          frm.reload_doc().then(() => {
            update_download_buttons(frm);
          });
        }
      });
      frm._realtime_bound = true;
    }
    setup_clickable_column(frm);
    setup_download_buttons(frm);
  }
});


// 设置下载按钮
function setup_download_buttons(frm) {
  // 清除现有按钮
  frm.remove_custom_button('📄 下载 Markdown');
  frm.remove_custom_button('📄 下载 Docx');
  // 添加 Markdown 下载按钮
  frm.add_custom_button(__('📄 下载 Markdown'), async function () {
    await handle_download(frm, 'markdown');
  });
  // 添加 Docx 下载按钮
  frm.add_custom_button(__('📄 下载 Docx'), async function () {
    await handle_download(frm, 'docx');
  });
  // 初始化按钮状态
  update_download_buttons(frm);
}


// 更新下载按钮状态
function update_download_buttons(frm) {
  const files = frm.doc.generated_files || [];
  const now = new Date();
  let markdown_file = null;
  let docx_file = null;
  // 查找对应的文件
  files.forEach(file => {
    if (file.s3_url) {
      if (file.s3_url.endsWith('c2d/input_text.txt')) {
        markdown_file = file;
      } else if (file.s3_url.includes('c2d/') && file.s3_url.endsWith('.docx')) {
        // 检查是否是最终的docx文件（排除特定的4个文件）
        const excluded_files = ['abstract.docx', 'claims.docx', 'description.docx', 'figures.docx'];
        const filename = file.s3_url.split('/').pop();
        if (!excluded_files.includes(filename)) {
          docx_file = file;
        }
      }
    }
  });
  // 更新按钮状态
  update_button_state(frm, '📄 下载 Markdown', markdown_file, now);
  update_button_state(frm, '📄 下载 Docx', docx_file, now);
}


// 更新单个按钮状态
function update_button_state(frm, button_text, file, now) {
  const button = frm.custom_buttons[button_text];
  if (!button) return;
  const $button = button.parent();
  if (!file || !file.signed_url || !file.signed_url_generated_at) {
    // 没有文件或链接，设置为灰色
    $button.removeClass('btn-primary btn-success').addClass('btn-secondary');
    $button.prop('disabled', false); // 仍可点击，但会提示刷新
    return;
  }
  // 检查链接是否过期（1小时）
  const generated_time = new Date(file.signed_url_generated_at);
  const expired = (now - generated_time) > (60 * 60 * 1000); // 1小时
  if (expired) {
    // 链接过期，设置为橙色
    $button.removeClass('btn-primary btn-success').addClass('btn-warning');
  } else {
    // 链接有效，设置为绿色
    $button.removeClass('btn-secondary btn-warning').addClass('btn-success');
  }
  $button.prop('disabled', false);
}


// 处理下载
async function handle_download(frm, file_type) {
  const files = frm.doc.generated_files || [];
  const now = new Date();
  let target_file = null;
  // 查找目标文件
  files.forEach(file => {
    if (file.s3_url) {
      if (file_type === 'markdown' && file.s3_url.endsWith('c2d/input_text.txt')) {
        target_file = file;
      } else if (file_type === 'docx' && file.s3_url.includes('c2d/') && file.s3_url.endsWith('.docx')) {
        const excluded_files = ['abstract.docx', 'claims.docx', 'description.docx', 'figures.docx'];
        const filename = file.s3_url.split('/').pop();
        if (!excluded_files.includes(filename)) {
          target_file = file;
        }
      }
    }
  });
  if (!target_file) {
    frappe.show_alert({ 
      message: `❗ 未找到${file_type === 'markdown' ? 'Markdown' : 'Docx'}文件`, 
      indicator: 'red' 
    }, 5);
    return;
  }
  if (!target_file.signed_url || !target_file.signed_url_generated_at) {
    frappe.show_alert({ 
      message: '❗ 请先点击"刷新链接"按钮生成下载链接', 
      indicator: 'orange' 
    }, 5);
    return;
  }
  // 检查链接是否过期
  const generated_time = new Date(target_file.signed_url_generated_at);
  const expired = (now - generated_time) > (60 * 60 * 1000);
  if (expired) {
    frappe.show_alert({ 
      message: '⏰ 下载链接已过期，请先点击"刷新链接"按钮', 
      indicator: 'orange' 
    }, 5);
    return;
  }
  // 开始下载
  try {
    // 获取文件名
    const filename = target_file.s3_url.split('/').pop();
    const display_name = file_type === 'markdown' ? 
      `${frm.doc.patent_title || 'patent'}_claims.txt` : 
      `${frm.doc.patent_title || 'patent'}_final.docx`;
    // 创建下载
    const link = document.createElement('a');
    link.href = target_file.signed_url;
    link.download = display_name;
    link.target = '_blank';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    frappe.show_alert({ 
      message: `✅ 开始下载 ${file_type === 'markdown' ? 'Markdown' : 'Docx'} 文件`, 
      indicator: 'green' 
    }, 3);
  } catch (error) {
    frappe.show_alert({ 
      message: `❌ 下载失败: ${error.message}`, 
      indicator: 'red' 
    }, 5);
  }
}


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
