frappe.ui.form.on('LLM Chat Session', {
  refresh(frm) {
    // console.log('temperature:', frm.doc.temperature);
    // console.log('sys_prompt:', frm.doc.sys_prompt);
    // 清理旧的按钮
    frm.custom_buttons = {};
    
    // 添加自定义按钮
    frm.add_custom_button('Clear Chat', async () => {
      frappe.confirm(
        'Are you sure you want to clear all messages? This action cannot be undone.',
        async () => {
          try {
            frm.doc.chat_messages = [];
            frm.temp_chat_messages = []; // 清空临时消息
            if (frm.session_manager) {
              frm.session_manager.temp_messages = [];
            }
            frm.dirty();
            
            if (frm.render_messages) {
              frm.render_messages();
            }
            
            frappe.show_alert({
              message: 'Chat cleared successfully',
              indicator: 'green'
            }, 2);
          } catch (error) {
            console.error('Error clearing chat:', error);
            frappe.msgprint('Failed to clear chat');
          }
        }
      );
    });

    frm.add_custom_button('Export Chat', () => {
      try {
        // 使用临时消息或文档消息
        const all_messages = frm.temp_chat_messages || frm.doc.chat_messages || [];
        
        if (all_messages.length === 0) {
          frappe.msgprint('No messages to export');
          return;
        }

        let content = `Chat Session Export\n`;
        content += `Session: ${frm.doc.name || 'New Session'}\n`;
        content += `Exported: ${frappe.datetime.now_datetime()}\n\n`;
        
        all_messages.forEach(msg => {
          content += `[${msg.role.toUpperCase()}] ${msg.timestamp || ''}\n`;
          content += `${msg.message || ''}\n`;
          // if (msg.attachment) {
          //   content += `Attachment: ${msg.attachment}\n`;
          // }
          content += '\n';
        });

        const blob = new Blob([content], { type: 'text/plain' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `chat_session_${frm.doc.name || 'new'}_${Date.now()}.txt`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
      } catch (error) {
        console.error('Error exporting chat:', error);
        frappe.msgprint('Failed to export chat');
      }
    });

    // 如果UI已经渲染，只更新按钮
    if (frm.rendered_chat_ui) return;

    // 如果是新文档或者文档发生变化，重置UI状态
    if (frm.is_new() || frm.doc.__islocal || frm.doc_changed) {
      frm.rendered_chat_ui = false;
      
      // 清理之前的聊天容器
      if (frm.chat_container) {
        frm.chat_container.remove();
        frm.chat_container = null;
      }
      
      // 清理会话管理器
      if (frm.session_manager) {
        frm.session_manager.destroy();
        frm.session_manager = null;
      }
      
      // 重置其他相关变量
      // frm.latest_attachment = null;
    }

    // 初始化临时消息存储
    if (!frm.temp_chat_messages) {
      frm.temp_chat_messages = [...(frm.doc.chat_messages || [])];
    }

    // 创建聊天容器
    frm.chat_container = $(`
      <div class="chat-container">
        <div class="chat-header">
          <div class="chat-header-title">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
              <path d="M20 2H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h4l4 4 4-4h4c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zm-2 12H6v-2h12v2zm0-3H6V9h12v2zm0-3H6V6h12v2z"/>
            </svg>
            <span>AI Assistant</span>
          </div>
        </div>
        <div class="chat-messages">
          <div class="chat-inner"></div>
        </div>
        <div class="chat-input-area">
          <div class="chat-input-container">
            <!--
            <button class="chat-btn attach" title="Attach file">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                <path d="M16.5 6v11.5c0 2.21-1.79 4-4 4s-4-1.79-4-4V5c0-1.38 1.12-2.5 2.5-2.5s2.5 1.12 2.5 2.5v10.5c0 .55-.45 1-1 1s-1-.45-1-1V6H10v9.5c0 1.38 1.12 2.5 2.5 2.5s2.5-1.12 2.5-2.5V5c0-2.21-1.79-4-4-4S7 2.79 7 5v12.5c0 3.04 2.46 5.5 5.5 5.5s5.5-2.46 5.5-5.5V6h-1.5z"/>
              </svg>
            </button>
            -->
            <textarea class="chat-input" placeholder="Type your message..." rows="1"></textarea>
            <button class="chat-btn send" title="Send message">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/>
              </svg>
            </button>
          </div>
        </div>
      </div>
    `).insertBefore(frm.fields_dict['chat_messages'].wrapper);

    const chat_inner = frm.chat_container.find('.chat-inner');
    const chatMessages = frm.chat_container.find('.chat-messages');
    const input = frm.chat_container.find('.chat-input');
    // const attachBtn = frm.chat_container.find('.chat-btn.attach');
    const sendBtn = frm.chat_container.find('.chat-btn.send');

    // 消息同步函数
    function sync_messages_to_doc() {
      try {
        // 清空现有消息
        frm.doc.chat_messages = [];
        
        // 同步临时消息到文档
        if (frm.temp_chat_messages && frm.temp_chat_messages.length > 0) {
          frm.temp_chat_messages.forEach(msg => {
            frm.add_child('chat_messages', {
              role: msg.role,
              message: msg.message,
              attachment: msg.attachment,
              timestamp: msg.timestamp
            });
          });
          frm.dirty();
        }
        
        console.log('Messages synced to document:', frm.temp_chat_messages.length);
      } catch (error) {
        console.error('Error syncing messages to document:', error);
      }
    }

    // 渲染消息函数
    function render_messages() {
      if (!chat_inner.length) return;
      
      chat_inner.empty();
      // 使用临时消息进行渲染
      const all_messages = frm.temp_chat_messages || [];

      if (all_messages.length === 0) {
        chat_inner.html(`
          <div class="empty-state">
            <div class="empty-state-icon">
              <svg width="48" height="48" viewBox="0 0 24 24" fill="currentColor">
                <path d="M20 2H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h4l4 4 4-4h4c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zm-2 12H6v-2h12v2zm0-3H6V9h12v2zm0-3H6V6h12v2z"/>
              </svg>
            </div>
            <h3>No messages yet</h3>
            <p>Start a conversation with your AI assistant</p>
          </div>
        `);
        return;
      }

      all_messages.forEach(msg => {
        const isUser = msg.role === 'user';
        const timestamp = msg.timestamp ? frappe.datetime.str_to_user(msg.timestamp) : '';
        
        const messageWrapper = $(`
          <div class="message-wrapper ${isUser ? 'user' : 'assistant'}">
            ${!isUser ? `
              <div class="message-avatar assistant">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                  <path d="M11,4H13L14.5,9H18.5L12.5,20L11,14H7.5L11,4Z"/>
                </svg>
              </div>
            ` : ''}
            <div class="message-bubble ${isUser ? 'user' : 'assistant'}">
              <div class="message-content">${frappe.utils.escape_html(msg.message || '').replace(/\n/g, '<br>')}</div>
              ${msg.attachment ? `
                <div class="message-attachment">
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" style="margin-right: 4px;">
                    <path d="M16.5 6v11.5c0 2.21-1.79 4-4 4s-4-1.79-4-4V5c0-1.38 1.12-2.5 2.5-2.5s2.5 1.12 2.5 2.5v10.5c0 .55-.45 1-1 1s-1-.45-1-1V6H10v9.5c0 1.38 1.12 2.5 2.5 2.5s2.5-1.12 2.5-2.5V5c0-2.21-1.79-4-4-4S7 2.79 7 5v12.5c0 3.04 2.46 5.5 5.5 5.5s5.5-2.46 5.5-5.5V6h-1.5z"/>
                  </svg>
                  <a href="${frappe.urllib.get_full_url('/api/method/frappe.utils.file_manager.download_file?file_url=' + encodeURIComponent(msg.attachment))}" target="_blank">
                    ${msg.attachment.split('/').pop()}
                  </a>
                </div>
              ` : ''}
              ${timestamp ? `<div class="message-time">${timestamp}</div>` : ''}
            </div>
            ${isUser ? `
              <div class="message-avatar user">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                  <path d="M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z"/>
                </svg>
              </div>
            ` : ''}
          </div>
        `);
        
        chat_inner.append(messageWrapper);
      });

      // 平滑滚动到底部
      if (chatMessages.length) {
        setTimeout(() => {
          chatMessages.animate({
            scrollTop: chatMessages[0].scrollHeight
          }, 300);
        }, 100);
      }
    }

    // 将 render_messages 函数绑定到 frm 对象
    frm.render_messages = render_messages;
    // 将同步函数绑定到 frm 对象
    frm.sync_messages_to_doc = sync_messages_to_doc;

    // 优化的会话管理器
    frm.session_manager = {
      auto_save_timeout: null,
      
      init() {
        // 页面离开时确保保存
        if (!frm.beforeunload_handler) {
          frm.beforeunload_handler = () => {
            this.saveMessages();
          };
          $(window).on('beforeunload', frm.beforeunload_handler);
        }
      },
      
      // 延迟自动保存
      scheduleAutoSave() {
        if (this.auto_save_timeout) {
          clearTimeout(this.auto_save_timeout);
        }
        
        this.auto_save_timeout = setTimeout(() => {
          this.saveMessages();
        }, 1000); // 1秒后自动保存
      },
      
      // 保存消息到文档
      saveMessages() {
        try {
          if (frm.temp_chat_messages && frm.temp_chat_messages.length > 0) {
            sync_messages_to_doc();
            this.showSaveIndicator();
            console.log('Auto-saved messages:', frm.temp_chat_messages.length);
          }
        } catch (error) {
          console.error('Error auto-saving messages:', error);
        }
      },
      
      showSaveIndicator() {
        if (!frm.chat_container) return;
        
        const indicator = $('<div class="auto-save-indicator">Saved</div>');
        frm.chat_container.append(indicator);
        
        indicator.animate({opacity: 1}, 300)
                 .delay(1500)
                 .animate({opacity: 0}, 300, function() {
                   $(this).remove();
                 });
      },
      
      destroy() {
        if (this.auto_save_timeout) {
          clearTimeout(this.auto_save_timeout);
          this.auto_save_timeout = null;
        }
      }
    };

    // 初始化会话管理器
    frm.session_manager.init();

    // 创建隐藏的文件输入（注释掉）
    // const fileInput = $('<input type="file" accept=".txt,.pdf,.doc,.docx,.png,.jpg,.jpeg" style="display:none;">').appendTo(frm.chat_container);

    // 自动调整输入框高度
    input.on('input', function() {
      this.style.height = 'auto';
      this.style.height = Math.min(this.scrollHeight, 100) + 'px';
    });

    // 显示文件上传指示器（注释掉）
    /*
    function showFileIndicator(filename) {
      const indicator = $(`<div class="file-upload-indicator">📎 ${filename} attached</div>`);
      frm.chat_container.find('.chat-input-container').css('position', 'relative').append(indicator);
      
      setTimeout(() => {
        indicator.fadeOut(300, function() {
          $(this).remove();
          frm.chat_container.find('.chat-input-container').css('position', '');
        });
      }, 3000);
    }
    */

    // 显示思考指示器
    function show_thinking() {
      const thinking = $(`
        <div class="thinking-indicator">
          <div class="message-avatar assistant">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
              <path d="M11,4H13L14.5,9H18.5L12.5,20L11,14H7.5L11,4Z"/>
            </svg>
          </div>
          <div>
            <span>AI is thinking</span>
            <div class="thinking-dots">
              <div class="thinking-dot"></div>
              <div class="thinking-dot"></div>
              <div class="thinking-dot"></div>
            </div>
          </div>
        </div>
      `);
      chat_inner.append(thinking);
      if (chatMessages.length) {
        chatMessages.scrollTop(chatMessages[0].scrollHeight);
      }
      return thinking;
    }

    render_messages();

    // 文件上传处理函数（注释掉）
    /*
    async function uploadFile(file) {
      const maxSize = 10 * 1024 * 1024; // 10MB
      
      if (file.size > maxSize) {
        frappe.msgprint('File size should not exceed 10MB');
        return null;
      }

      try {
        frappe.show_progress(__('Uploading file...'), 20, 100);
        
        // 使用标准的文件上传API
        const result = await new Promise((resolve, reject) => {
          const formData = new FormData();
          formData.append('file', file);
          formData.append('is_private', '1');
          formData.append('folder', 'Home/Attachments');
          formData.append('file_name', file.name);
          
          // 如果文档已保存，关联到文档
          if (frm.doc.name && !frm.doc.__islocal) {
            formData.append('doctype', 'LLM Chat Session');
            formData.append('docname', frm.doc.name);
          }
          
          $.ajax({
            url: '/api/method/upload_file',
            type: 'POST',
            data: formData,
            processData: false,
            contentType: false,
            headers: {
              'X-Frappe-CSRF-Token': frappe.csrf_token
            },
            success: (response) => {
              if (response && response.message) {
                resolve(response.message);
              } else {
                reject(new Error('Invalid response from server'));
              }
            },
            error: (xhr, status, error) => {
              console.error('Upload error:', xhr.responseText);
              let errorMsg = 'Upload failed';
              try {
                const errorResponse = JSON.parse(xhr.responseText);
                if (errorResponse.message) {
                  errorMsg = errorResponse.message;
                } else if (errorResponse.exc) {
                  errorMsg = errorResponse.exc;
                }
              } catch (e) {
                errorMsg = error || 'Unknown error';
              }
              reject(new Error(errorMsg));
            }
          });
        });
        
        frappe.hide_progress();
        
        if (result && result.file_url) {
          return result.file_url;
        } else {
          throw new Error('No file URL returned from server');
        }
      } catch (error) {
        frappe.hide_progress();
        console.error('Upload error:', error);
        frappe.msgprint(__('Upload failed: ') + error.message);
        return null;
      }
    }
    */

    // 文件上传处理（注释掉）
    /*
    attachBtn.on('click', () => fileInput.click());

    fileInput.on('change', async () => {
      if (fileInput[0].files.length === 0) return;
      
      const file = fileInput[0].files[0];
      const fileUrl = await uploadFile(file);
      
      if (fileUrl) {
        frm.latest_attachment = fileUrl;
        showFileIndicator(file.name);
        frappe.show_alert({
          message: `File uploaded successfully`,
          indicator: 'green'
        }, 2);
      }

      fileInput.val('');
    });
    */

    // 发送消息
    async function send_message() {
      const message = input.val().trim();
      if (!message) {
        frappe.show_alert({
          message: __('Please enter a message'),
          indicator: 'orange'
        }, 2);
        return;
      }

      sendBtn.prop('disabled', true);
      // attachBtn.prop('disabled', true);

      // 添加用户消息到临时存储
      const userMessage = {
        role: 'user',
        message,
        attachment: null, // 附件设为空
        timestamp: frappe.datetime.now_datetime()
      };
      
      // 确保临时消息数组存在
      if (!frm.temp_chat_messages) {
        frm.temp_chat_messages = [];
      }
      
      frm.temp_chat_messages.push(userMessage);
      
      // 清空输入框
      input.val('').trigger('input');
      render_messages();

      // 显示思考指示器
      const thinking = show_thinking();

      try {
        // 准备历史对话数据（排除刚添加的用户消息）
        const chat_history = frm.temp_chat_messages.slice(0, -1);
        
        // 获取系统提示词（如果文档中有相关字段）
        const sys_prompt = frm.doc.sys_prompt || null;

        // 调用新的AI API
        const response = await new Promise((resolve, reject) => {
          frappe.call({
            method: 'patent_hub.api.anthropic_chat.anthropic_call',
            args: {
              user_prompt: message || "",  // 字符串
              sys_prompt: sys_prompt || "",  // 字符串
              chat_history: chat_history,  // 数组
              temperature: frm.doc.temperature,
              attachment_path: null,
              // model: frm.doc.model,  // 字符串
              // max_tokens: frm.doc.max_tokens,  // 整数
            },
            callback: resolve,
            error: reject
          });
        });

        thinking.remove();

        if (response.message && typeof response.message === 'string' && response.message.trim()) {
          const assistantMessage = {
            role: 'assistant',
            message: response.message,
            timestamp: frappe.datetime.now_datetime()
          };
          
          // 添加到临时存储
          frm.temp_chat_messages.push(assistantMessage);
          render_messages();
          
          // 触发自动保存
          frm.session_manager.scheduleAutoSave();
          
        } else {
          console.error('Invalid response:', response);
          frappe.msgprint(__('No response from AI'));
        }
      } catch (err) {
        thinking.remove();
        console.error('API Error:', err);
        frappe.msgprint(__('Failed to get response from AI: ') + (err.message || err));
      } finally {
        sendBtn.prop('disabled', false);
        // attachBtn.prop('disabled', false);
      }
    }

    // 事件绑定
    sendBtn.on('click', send_message);
    
    input.on('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        send_message();
      }
    });

    // 支持拖拽上传（注释掉）
    /*
    frm.chat_container.on('dragover', (e) => {
      e.preventDefault();
      e.stopPropagation();
      frm.chat_container.addClass('drag-over');
    });

    frm.chat_container.on('dragleave', (e) => {
      e.preventDefault();
      e.stopPropagation();
      frm.chat_container.removeClass('drag-over');
    });

    frm.chat_container.on('drop', async (e) => {
      e.preventDefault();
      e.stopPropagation();
      frm.chat_container.removeClass('drag-over');
      
      const files = e.originalEvent.dataTransfer.files;
      if (files.length > 0) {
        const file = files[0];
        const fileUrl = await uploadFile(file);
        
        if (fileUrl) {
          frm.latest_attachment = fileUrl;
          showFileIndicator(file.name);
          frappe.show_alert({
            message: `File uploaded successfully`,
            indicator: 'green'
          }, 2);
        }
      }
    });
    */

    frm.rendered_chat_ui = true;

    // 清理函数
    frm.cleanup_chat = () => {
      if (frm.session_manager) {
        frm.session_manager.destroy();
      }
      if (frm.beforeunload_handler) {
        $(window).off('beforeunload', frm.beforeunload_handler);
        frm.beforeunload_handler = null;
      }
    };
  },

  // 文档保存前同步消息
  before_save(frm) {
    if (frm.sync_messages_to_doc) {
      frm.sync_messages_to_doc();
    }
  },

  // 新文档加载时重置状态
  onload(frm) {
    if (frm.is_new()) {
      frm.rendered_chat_ui = false;
      frm.temp_chat_messages = [];
      // frm.latest_attachment = null;
    } else {
      // 加载现有文档时，初始化临时消息
      frm.temp_chat_messages = [...(frm.doc.chat_messages || [])];
    }
  },

  // 文档加载前清理状态
  before_load(frm) {
    frm.rendered_chat_ui = false;
    if (frm.chat_container) {
      frm.chat_container.remove();
      frm.chat_container = null;
    }
    if (frm.cleanup_chat) {
      frm.cleanup_chat();
    }
  }
});
