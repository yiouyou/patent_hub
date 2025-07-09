// Copyright (c) 2025, sz and contributors
// For license information, please see license.txt

// frappe.ui.form.on("Claims To Docx", {
// 	refresh(frm) {

// 	},
// });
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
      frappe.new_doc('Docx Proofreading', {}, (doc) => {
        doc.writer_id = frm.doc.writer_id
        doc.patent_id = frm.doc.patent_id
        doc.scene_to_tech_id = frm.doc.scene_to_tech_id
        doc.tech_to_claims_id = frm.doc.tech_to_claims_id
        doc.claims_to_docx_id = frm.doc.claims_to_docx_id
        doc.save();
      });
    });
  }
});
