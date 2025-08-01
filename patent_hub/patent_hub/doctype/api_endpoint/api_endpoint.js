frappe.ui.form.on("API Endpoint", {
    refresh(frm) {
        // 根据当前状态切换按钮样式
        update_start_button_style(frm);
    },

    start_ali_spot(frm) {
        const url = frm.doc.server_ip_port;
        if (!url) {
            frappe.show_alert({ message: "尚未配置 server_ip_port，将尝试启动实例...", indicator: "orange" }, 5);
            call_backend_to_start(frm);
            return;
        }

        const ip = url.replace(/^https?:\/\//, "").split(":")[0];

        frappe.call({
            method: "patent_hub.api._ali_spot.ping",
            args: { host: ip },
            callback: function (r) {
                if (r.message === true) {
                    frappe.show_alert({ message: "实例已在线，无需重启！", indicator: "green" }, 3);
                    frm.set_value("spot_status", "On");
                    update_start_button_style(frm);
                    frm.save();
                } else {
                    frappe.show_alert({ message: "实例离线，尝试重新启动...", indicator: "orange" }, 5);
                    call_backend_to_start(frm);
                }
            }
        });

        function call_backend_to_start(frm) {
            frappe.call({
                method: "patent_hub.api._ali_spot.run",
                args: { docname: frm.doc.name },
                callback: function (res) {
                    if (!res.exc) {
                        frappe.show_alert({ message: "实例启动成功，IP: " + res.message, indicator: "green" }, 3);
                        frm.set_value("spot_status", "On");
                    } else {
                        frappe.show_alert({ message: "启动失败，请检查日志。", indicator: "red" }, 7);
                        frm.set_value("spot_status", "Off");
                    }
                    update_start_button_style(frm);
                    frm.save();
                }
            });
        }
    },

    check_status(frm) {
        const url = frm.doc.server_ip_port;
        if (!url) {
            frappe.show_alert({ message: "未配置 server_ip_port，无法检测状态。", indicator: "red" }, 7);
            return;
        }

        const ip = url.replace(/^https?:\/\//, "").split(":")[0];

        frappe.call({
            method: "patent_hub.api._ali_spot.ping",
            args: { host: ip },
            callback: function (r) {
                if (r.message === true) {
                    frappe.show_alert({ message: "实例在线", indicator: "green" }, 3);
                    frm.set_value("spot_status", "On");
                } else {
                    frappe.show_alert({ message: "实例离线", indicator: "red" }, 7);
                    frm.set_value("spot_status", "Off");
                }
                update_start_button_style(frm);
                frm.save();
            }
        });
    }
});

// 控制按钮样式和启用状态
function update_start_button_style(frm) {
    const status = frm.doc.spot_status;
    if (status === "On") {
        toggle_button_state(frm, "start_ali_spot", false); // 禁用
    } else {
        toggle_button_state(frm, "start_ali_spot", true); // 启用
    }
}

// 控制按钮状态和样式的通用函数
function toggle_button_state(frm, button_name, enabled, danger = false) {
    const btn = frm.get_field(button_name);
    if (btn && btn.$wrapper) {
        const $btn = btn.$wrapper.find('button');
        $btn.toggleClass('btn-primary', enabled && !danger);
        $btn.toggleClass('btn-danger', enabled && danger);
        $btn.toggleClass('btn-default', !enabled);
        $btn.prop('disabled', !enabled);
    }
}
