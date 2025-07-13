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
      // 使用 Promise 处理异步操作
      get_file_content(frm, 'markdown_before_tex')
        .then(markdown_before_tex_content => {
          frappe.new_doc('Docx Proofreading', {}, (doc) => {
            doc.writer_id = frm.doc.writer_id
            doc.patent_id = frm.doc.patent_id
            doc.scene_to_tech_id = frm.doc.scene_to_tech_id
            doc.tech_to_claims_id = frm.doc.tech_to_claims_id
            doc.patent_title = frm.doc.patent_title
            doc.claims_to_docx_id = frm.doc.claims_to_docx_id
            doc.markdown_before_tex = markdown_before_tex_content || "获取内容失败"
            doc.save();
          });
        })
        .catch(error => {
          // 如果获取文件内容失败，仍然创建文档但使用默认值
          console.warn('获取 markdown_before_tex 内容失败:', error.message);
          frappe.new_doc('Docx Proofreading', {}, (doc) => {
            doc.writer_id = frm.doc.writer_id
            doc.patent_id = frm.doc.patent_id
            doc.scene_to_tech_id = frm.doc.scene_to_tech_id
            doc.tech_to_claims_id = frm.doc.tech_to_claims_id
            doc.patent_title = frm.doc.patent_title
            doc.claims_to_docx_id = frm.doc.claims_to_docx_id
            doc.markdown_before_tex = `获取内容失败: ${error.message}`
            doc.save();
          });
          frappe.show_alert({ 
            message: `⚠️ 无法获取文件内容，已使用默认值创建文档`, 
            indicator: 'orange'
          }, 5);
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
      // 刷新后更新按钮状态
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
    update_download_buttons(frm);
  },
  // 处理 final_markdown 按钮点击
  final_markdown: function(frm) {
    // console.log('final_markdown button clicked');
    handle_download_click(frm, 'markdown');
  },
  // 处理 final_docx 按钮点击
  final_docx: function(frm) {
    // console.log('final_docx button clicked');
    handle_download_click(frm, 'docx');
  },
  // 处理 markdown_before_tex 按钮点击
  'markdown_before_tex': function(frm) {
    handle_download_click(frm, 'markdown_before_tex');
  }
});


// 检查链接是否过期（1小时）
function is_url_expired(generated_at) {
  if (!generated_at) {
    // console.log('No generated_at timestamp');
    return true;
  }
  // 处理不同的时间格式
  let generated;
  if (typeof generated_at === 'string') {
    // Frappe 通常返回 "YYYY-MM-DD HH:mm:ss" 格式
    generated = frappe.datetime.str_to_obj(generated_at);
  } else {
    generated = new Date(generated_at);
  }
  const now = frappe.datetime.now_datetime();
  const nowObj = frappe.datetime.str_to_obj(now);
  // 计算时间差（毫秒）
  const diffMs = nowObj.getTime() - generated.getTime();
  const diffHours = diffMs / (1000 * 60 * 60);
  // console.log('Generated at:', generated_at);
  // console.log('Generated obj:', generated);
  // console.log('Now:', now);
  // console.log('Now obj:', nowObj);
  // console.log(`URL age: ${diffHours.toFixed(2)} hours`);
  return diffHours >= 1;
}


// 从 s3_url 中找到对应的文件
function find_file_by_type(files, type) {
  // console.log('Looking for file type:', type);
  // console.log('Available files:', files);
  if (!files || !Array.isArray(files)) return null;
  for (let file of files) {
    // console.log('Checking file:', file.s3_url);
    if (!file.s3_url) continue;
    if (type === 'markdown') {
      // final_markdown 以 "c2d/input_text.txt" 结尾
      if (file.s3_url.endsWith('c2d/input_text.txt')) {
        // console.log('Found markdown file:', file);
        return file;
      }
    } else if (type === 'markdown_before_tex') {
      // markdown_before_tex 以 "c-tex/input_text.txt" 结尾
      if (file.s3_url.endsWith('c-tex/input_text.txt')) {
        return file;
      }
    } else if (type === 'docx') {
      // final_docx 以 "c2d/*.docx" 结尾且不是指定的4个docx
      if (file.s3_url.includes('c2d/') && file.s3_url.endsWith('.docx')) {
        const filename = file.s3_url.split('/').pop();
        const excluded_files = ['abstract.docx', 'claims.docx', 'description.docx', 'figures.docx'];
        if (!excluded_files.includes(filename)) {
          // console.log('Found docx file:', file);
          return file;
        }
      }
    }
  }
  console.log('No matching file found for type:', type);
  return null;
}


// 更新下载按钮状态
function update_download_buttons(frm) {
  // console.log('Updating download buttons...');
  const markdown_before_tex_file = find_file_by_type(frm.doc.generated_files, 'markdown_before_tex');
  const markdown_file = find_file_by_type(frm.doc.generated_files, 'markdown');
  const docx_file = find_file_by_type(frm.doc.generated_files, 'docx');
  console.log('Markdown before tex file found:', !!markdown_before_tex_file);
  console.log('Markdown file found:', !!markdown_file);
  console.log('Docx file found:', !!docx_file);
  // 更新 markdown_before_tex 按钮
  const markdown_before_tex_field = frm.get_field('markdown_before_tex');
  if (markdown_before_tex_field && markdown_before_tex_field.$input) {
    const markdown_before_tex_valid = markdown_before_tex_file && 
                                     markdown_before_tex_file.signed_url && 
                                     !is_url_expired(markdown_before_tex_file.signed_url_generated_at);
    // console.log('Markdown Before Tex button valid:', markdown_before_tex_valid);
    if (markdown_before_tex_valid) {
      markdown_before_tex_field.$input.removeClass('btn-default').addClass('btn-primary');
      markdown_before_tex_field.$input.prop('disabled', false);
      markdown_before_tex_field.$input.css('opacity', '1');
    } else {
      markdown_before_tex_field.$input.removeClass('btn-primary').addClass('btn-default');
      markdown_before_tex_field.$input.prop('disabled', true);
      markdown_before_tex_field.$input.css('opacity', '0.5');
    }
  }
  // 更新 markdown 按钮
  const markdown_field = frm.get_field('final_markdown');
  if (markdown_field && markdown_field.$input) {
    const markdown_valid = markdown_file && 
                          markdown_file.signed_url && 
                          !is_url_expired(markdown_file.signed_url_generated_at);
    // console.log('Markdown button valid:', markdown_valid);
    if (markdown_valid) {
      markdown_field.$input.removeClass('btn-default').addClass('btn-primary');
      markdown_field.$input.prop('disabled', false);
      markdown_field.$input.css('opacity', '1');
    } else {
      markdown_field.$input.removeClass('btn-primary').addClass('btn-default');
      markdown_field.$input.prop('disabled', true);
      markdown_field.$input.css('opacity', '0.5');
    }
  }
  // 更新 docx 按钮
  const docx_field = frm.get_field('final_docx');
  if (docx_field && docx_field.$input) {
    const docx_valid = docx_file && 
                      docx_file.signed_url && 
                      !is_url_expired(docx_file.signed_url_generated_at);
    // console.log('Docx button valid:', docx_valid);
    if (docx_valid) {
      docx_field.$input.removeClass('btn-default').addClass('btn-primary');
      docx_field.$input.prop('disabled', false);
      // docx_field.$input.css('opacity', '1');
    } else {
      docx_field.$input.removeClass('btn-primary').addClass('btn-default');
      docx_field.$input.prop('disabled', true);
      // docx_field.$input.css('opacity', '0.5');
    }
  }
}


// 处理下载按钮点击
async function handle_download_click(frm, type) {
  // console.log('Handle download click for type:', type);
  const file = find_file_by_type(frm.doc.generated_files, type);
  if (!file) {
    let file_type_name;
    switch(type) {
      case 'markdown': file_type_name = 'Markdown'; break;
      case 'markdown_before_tex': file_type_name = 'Markdown Before Tex'; break;
      case 'docx': file_type_name = 'DOCX'; break;
      default: file_type_name = type;
    }
    frappe.msgprint({
      title: '文件未找到',
      message: `未找到对应的${type === 'markdown' ? 'Markdown' : 'DOCX'}文件`,
      indicator: 'red'
    });
    return;
  }
  // console.log('Found file:', file);
  if (!file.signed_url) {
    frappe.msgprint({
      title: '链接未生成',
      message: '请先点击"🔁 刷新链接"按钮生成下载链接',
      indicator: 'orange'
    });
    return;
  }
  if (is_url_expired(file.signed_url_generated_at)) {
    console.log('URL expired');
    frappe.msgprint({
      title: '链接已过期',
      message: '下载链接已过期（超过1小时），请先点击"🔁 刷新链接"按钮',
      indicator: 'orange'
    });
    return;
  }
  // 开始下载
  try {
    // 从 s3_url 中提取文件名
    const filename = file.s3_url.split('/').pop();
    console.log('Starting download for:', filename);
    console.log('Download URL:', file.signed_url);
    frappe.show_alert({ 
      message: `正在下载 ${filename}...`, 
      indicator: 'blue' 
    }, 3);
    // 方法1: 使用 window.open (适用于大多数浏览器)
    const downloadWindow = window.open(file.signed_url, '_blank');
    // 如果弹窗被阻止，尝试其他方法
    if (!downloadWindow || downloadWindow.closed || typeof downloadWindow.closed == 'undefined') {
      console.log('Popup blocked, trying alternative method');
      // 方法2: 使用隐藏的 a 标签
      const link = document.createElement('a');
      link.href = file.signed_url;
      link.download = filename;  // 使用自定义文件名
      link.target = '_blank';
      link.style.display = 'none';
      // 添加到 DOM，点击，然后移除
      document.body.appendChild(link);
      link.click();
      // 延迟移除，确保下载开始
      setTimeout(() => {
        if (document.body.contains(link)) {
          document.body.removeChild(link);
        }
      }, 100);
    }
    frappe.show_alert({ 
      message: `✅ ${filename} 下载已开始`, 
      indicator: 'green' 
    }, 3);
  } catch (error) {
    console.error('Download error:', error);
    frappe.msgprint({
      title: '下载失败',
      message: `下载失败: ${error.message}`,
      indicator: 'red'
    });
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


// 获取文件内容的函数
async function get_file_content(frm, type) {
  const file = find_file_by_type(frm.doc.generated_files, type);
  if (!file) {
    throw new Error(`未找到 ${type} 文件`);
  }
  if (!file.signed_url) {
    throw new Error('文件链接未生成，请先刷新链接');
  }
  if (is_url_expired(file.signed_url_generated_at)) {
    throw new Error('文件链接已过期，请先刷新链接');
  }
  try {
    const response = await fetch(file.signed_url);
    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }
    const content = await response.text();
    return content;
  } catch (error) {
    throw new Error(`获取文件内容失败: ${error.message}`);
  }
}
