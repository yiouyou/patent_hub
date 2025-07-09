// Copyright (c) 2025, sz and contributors
// For license information, please see license.txt

// frappe.ui.form.on("Patent Writer", {
// 	refresh(frm) {

// 	},
// });
frappe.ui.form.on('Patent Writer', {
  refresh(frm) {
    frm.add_custom_button(__('+ Patent'), () => {
      frappe.new_doc('Patent', {}, (doc) => {
        doc.writer_id = frm.doc.writer_id
        doc.save();
      });
    });
  }
});
