// Copyright (c) 2025, sz and contributors
// For license information, please see license.txt

// frappe.ui.form.on("Scene To Tech", {
// 	refresh(frm) {

// 	},
// });
frappe.ui.form.on('Scene To Tech', {
  refresh(frm) {
    frm.add_custom_button(__('→ Patent'), () => {
      if (frm.doc.patent_id) {
        frappe.set_route('Form', 'Patent', frm.doc.patent_id);
      } else {
        frappe.msgprint(__('No associated Patent found.'));
      }
    });
    frm.add_custom_button(__('+ Tech To Claims'), () => {
      frappe.new_doc('Tech To Claims', {}, (doc) => {
        doc.writer_id = frm.doc.writer_id
        doc.patent_id = frm.doc.patent_id
        doc.scene_to_tech_id = frm.doc.scene_to_tech_id
        doc.save();
      });
    });
  }
});
