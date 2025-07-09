// Copyright (c) 2025, sz and contributors
// For license information, please see license.txt

// frappe.ui.form.on("Patent", {
// 	refresh(frm) {

// 	},
// });
frappe.ui.form.on('Patent', {
  refresh(frm) {
    frm.add_custom_button(__('+ Scene To Tech'), () => {
      frappe.new_doc('Scene To Tech', {}, (doc) => {
        doc.patent_id = frm.doc.patent_id
        doc.save();
      });
    });
  }
});
