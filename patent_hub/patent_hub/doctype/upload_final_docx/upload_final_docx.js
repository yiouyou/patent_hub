// Copyright (c) 2025, sz and contributors
// For license information, please see license.txt

// frappe.ui.form.on("Upload Final Docx", {
// 	refresh(frm) {

// 	},
// });
frappe.ui.form.on('Upload Final Docx', {
  refresh(frm) {
    frm.add_custom_button(__('→ Docx Proofreading'), () => {
      if (frm.doc.docx_proofreading_id) {
        frappe.set_route('Form', 'Docx Proofreading', frm.doc.docx_proofreading_id);
      } else {
        frappe.msgprint(__('No associated Docx Proofreading found.'));
      }
    });
  }
});
