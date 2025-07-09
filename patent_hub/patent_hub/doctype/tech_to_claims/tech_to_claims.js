// Copyright (c) 2025, sz and contributors
// For license information, please see license.txt

// frappe.ui.form.on("Tech To Claims", {
// 	refresh(frm) {

// 	},
// });
frappe.ui.form.on('Tech To Claims', {
  refresh(frm) {
    frm.add_custom_button(__('→ Scene To Tech'), () => {
      if (frm.doc.scene_to_tech_id) {
        frappe.set_route('Form', 'Scene To Tech', frm.doc.scene_to_tech_id);
      } else {
        frappe.msgprint(__('No associated Scene To Tech found.'));
      }
    });
    frm.add_custom_button(__('+ Claims To Docx'), () => {
      frappe.new_doc('Claims To Docx', {}, (doc) => {
        doc.writer_id = frm.doc.writer_id
        doc.patent_id = frm.doc.patent_id
        doc.scene_to_tech_id = frm.doc.scene_to_tech_id
        doc.tech_to_claims_id = frm.doc.tech_to_claims_id
        doc.save();
      });
    });
  }
});
