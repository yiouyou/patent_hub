// Copyright (c) 2025, sz and contributors
// For license information, please see license.txt

frappe.ui.form.on("API Endpoint", {
    start_ali_spot: function (frm) {
        const url = frm.doc.server_ip_port;
        // 如果未配置 IP，直接调用后端启动
        if (!url) {
            frappe.show_alert({message: "尚未配置 server_ip_port，将尝试启动实例...", indicator: "orange"}, 5);
            call_backend_to_start(frm);
            return;
        }
        // 提取 IP（移除 http(s):// 和端口号）
        const ip = url.replace(/^https?:\/\//, "").split(":")[0];
        // 调用后端 ping 方法检测实例是否在线
        frappe.call({
            method: "patent_hub.api._start_ali_spot.ping",
            args: { host: ip },
            callback: function (r) {
                if (r.message === true) {
                    frappe.show_alert({message: "实例已在线，无需重启！", indicator: "green"}, 3);
                } else {
                    frappe.show_alert({message: "实例离线，尝试重新启动...", indicator: "orange"}, 5);
                    call_backend_to_start(frm);
                }
            }
        });
        // 封装调用后端启动方法
        function call_backend_to_start(frm) {
            frappe.call({
                method: "patent_hub.api._start_ali_spot.run",
                args: { docname: frm.doc.name },
                callback: function (res) {
                    if (!res.exc) {
                        frappe.show_alert({message: "实例启动成功，IP: " + res.message, indicator: "green"},3 );
                        frm.reload_doc();
                    } else {
                        frappe.show_alert({message: "启动失败，请检查日志。", indicator: "red"}, 7);
                    }
                }
            });
        }
    }
});
